"""Shiftly security & money-flow regression suite.

Runs the whole app in-process (FastAPI TestClient) against a temp database and
replays every attack that has ever been found, plus the honest flows they must
not break:

  auth     — no token / forged token / tampered token / pending-token abuse
  identity — body `username` fields can never redirect another user's action
  economy  — negative reward, over-cap reward, insolvent escrow, negative topup
  lifecycle— cancel refunds escrow once, rating works once per participant

Run:  pip install -r requirements.txt -r requirements-dev.txt
      pytest tests/ -q
"""
import base64
import json
import os

import pytest

os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("SESSION_HOURS", "24")

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "test.db"))
    main.init_db()
    main.otp_store.clear()
    yield TestClient(main.app)
    main.otp_store.clear()


# ---------------------------------------------------------------- helpers ----

def register(client, phone, name="Tester"):
    """Complete OTP login -> full session. Returns (auth_headers, username)."""
    client.post("/api/otp/send", json={"phone": phone})
    code = client.post("/api/otp/send", json={"phone": phone}).json()["demo_code"]
    pending = client.post("/api/otp/verify", json={"phone": phone, "otp": code}).json()["pending_token"]
    r = client.post("/api/register",
                    json={"name": name, "role": "Both", "major": "CSE", "dorm": "M"},
                    headers={"Authorization": f"Bearer {pending}"})
    data = r.json()
    return {"Authorization": f"Bearer {data['session']}"}, data["username"]


def post_gig(client, auth, reward=100, title="Legit gig"):
    return client.post("/api/gigs", headers=auth, json={
        "title": title, "category": "Food Run", "reward": reward,
        "urgency": "Medium", "location": "M-block", "instructions": "go"})


# ------------------------------------------------------------------- auth ----

PROTECTED = [
    ("POST", "/api/wallet/topup", {"username": "arjun", "amount": 99999}),
    ("POST", "/api/wallet/upi_pay", {"username": "arjun", "amount": 1, "pin": "1234"}),
    ("POST", "/api/gigs", {"username": "arjun", "title": "x", "category": "x",
                           "reward": 1, "urgency": "x", "location": "x", "instructions": "x"}),
    ("POST", "/api/dm", {"from_user": "arjun", "to_user": "priya", "text": "hi"}),
    ("GET", "/api/me?username=arjun", None),
    ("GET", "/api/dm_list?me=arjun", None),
    ("GET", "/api/users", None),
    ("GET", "/api/transactions", None),
    ("POST", "/api/register", {"username": "mallory", "name": "M", "role": "x", "major": "x", "dorm": "x"}),
    ("POST", "/api/gigs/ghost/accept", {"username": "arjun"}),
    ("POST", "/api/gigs/ghost/complete", {"username": "arjun"}),
    ("POST", "/api/gigs/ghost/cancel", {}),
    ("POST", "/api/gigs/ghost/rate", {"stars": 5}),
]


def test_every_protected_endpoint_rejects_anonymous(client):
    for method, path, body in PROTECTED:
        r = client.request(method, path, json=body) if body is not None else client.request(method, path)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_forged_and_tampered_tokens(client):
    # no signature at all
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": "arjun", "exp": 9999999999}).encode()).rstrip(b"=").decode()
    for token in (payload, "garbage.token", ""):
        r = client.post("/api/wallet/topup", json={"amount": 1},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
    # tamper with a real token
    auth, _ = register(client, "9000000001")
    real = auth["Authorization"][7:]
    r = client.post("/api/wallet/topup", json={"amount": 1},
                    headers={"Authorization": f"Bearer {real[:-2]}zz"})
    assert r.status_code == 401


# --------------------------------------------------------------- identity ----

def test_body_username_is_ignored_everywhere(client):
    # an innocent third party whose account we'll try to act as
    arjun_auth, arjun = register(client, "9000000099", name="Arjun")
    auth, me = register(client, "9000000002")

    # register with a spoofed username -> server assigns phone-derived one
    client.post("/api/otp/send", json={"phone": "9000000003"})
    code = client.post("/api/otp/send", json={"phone": "9000000003"}).json()["demo_code"]
    pending = client.post("/api/otp/verify", json={"phone": "9000000003", "otp": code}).json()["pending_token"]
    r = client.post("/api/register", headers={"Authorization": f"Bearer {pending}",
                                              "Content-Type": "application/json"},
                    json={"username": arjun, "name": "X", "role": "Both", "major": "x", "dorm": "x"})
    assert r.json()["username"] != arjun

    # topup 'for arjun' credits the CALLER only
    r = client.post("/api/wallet/topup", json={"username": arjun, "amount": 5000}, headers=auth)
    assert r.json()["ok"]
    mine = client.get("/api/me", headers=auth).json()
    assert mine["balance"] == 250 + 5000
    # ... and the innocent third party really is untouched
    assert client.get("/api/me", headers=arjun_auth).json()["balance"] == 250

    # DM spoofed as arjun is recorded as ME
    client.post("/api/dm", json={"from_user": arjun, "to_user": "priya", "text": "ping"}, headers=auth)
    msgs = client.get("/api/dm", params={"with_user": "priya"}, headers=auth).json()["messages"]
    assert {m["from_user"] for m in msgs if m["text"] == "ping"} == {me}

    # user directory hides money/identity columns
    users = client.get("/api/users", headers=auth).json()["users"]
    assert users and not any(k in users[0] for k in
                             ("balance", "escrow", "upi_id", "email", "auth_uid", "pending_payout"))


def test_pending_token_cannot_touch_money(client):
    client.post("/api/otp/send", json={"phone": "9000000004"})
    code = client.post("/api/otp/send", json={"phone": "9000000004"}).json()["demo_code"]
    pending = client.post("/api/otp/verify", json={"phone": "9000000004", "otp": code}).json()["pending_token"]
    r = client.post("/api/wallet/topup", json={"amount": 5},
                    headers={"Authorization": f"Bearer {pending}"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "registration_pending"


def test_otp_single_use(client):
    client.post("/api/otp/send", json={"phone": "9000000005"})
    code = client.post("/api/otp/send", json={"phone": "9000000005"}).json()["demo_code"]
    assert client.post("/api/otp/verify", json={"phone": "9000000005", "otp": code}).json()["ok"]
    assert not client.post("/api/otp/verify", json={"phone": "9000000005", "otp": code}).json()["ok"]


# ---------------------------------------------------------------- economy ----

def test_negative_and_huge_amounts_rejected(client):
    auth, _ = register(client, "9000000006")
    assert post_gig(client, auth, reward=-100000).status_code == 400      # minting
    assert post_gig(client, auth, reward=200000).status_code == 400       # over cap
    assert post_gig(client, auth, reward=90000).status_code == 400        # > balance
    assert client.post("/api/wallet/topup", json={"amount": -5000}, headers=auth).status_code == 400
    assert client.post("/api/wallet/topup", json={"amount": 0}, headers=auth).status_code == 400
    me = client.get("/api/me", headers=auth).json()
    assert me["balance"] == 250 and me["escrow"] == 0


def test_uplock_requires_balance(client):
    auth, _ = register(client, "9000000007")
    r = client.post("/api/wallet/upi_pay", json={"amount": 999999, "pin": "1234"}, headers=auth)
    assert r.status_code == 400
    r = client.post("/api/wallet/upi_pay", json={"amount": 10, "pin": "0000"}, headers=auth)
    assert "error" in r.json()   # wrong demo pin


def test_honest_flow_post_accept_complete_payout(client):
    poster, poster_name = register(client, "9000000010")
    worker, worker_name = register(client, "9000000011")
    gid = post_gig(client, poster, reward=100).json()["gig"]["id"]

    me = client.get("/api/me", headers=poster).json()
    assert me["balance"] == 150 and me["escrow"] == 100

    # poster can't take their own gig
    assert client.post(f"/api/gigs/{gid}/accept", json={}, headers=poster).status_code == 403
    # complete with no worker is a 409, not locked escrow
    assert client.post(f"/api/gigs/{gid}/complete", json={}, headers=poster).status_code == 409

    assert client.post(f"/api/gigs/{gid}/accept", json={}, headers=worker).json()["ok"]
    # non-poster can't release payment
    assert client.post(f"/api/gigs/{gid}/complete", json={}, headers=worker).status_code == 403
    assert client.post(f"/api/gigs/{gid}/complete", json={}, headers=poster).json()["ok"]

    w = client.get("/api/me", headers=worker).json()
    p = client.get("/api/me", headers=poster).json()
    assert abs(w["total_earned"] - 97.0) < 0.01 and w["balance"] == 250 + 97
    assert p["balance"] == 150 and p["escrow"] == 0

    tx = client.get("/api/transactions", headers=worker).json()["transactions"]
    assert any(t["type"] == "payout" and abs(t["amount"] - 97.0) < 0.01 for t in tx)


# --------------------------------------------------------------- lifecycle ----

def test_cancel_refunds_escrow_exactly_once(client):
    poster, _ = register(client, "9000000020")
    other, _ = register(client, "9000000021")
    gid = post_gig(client, poster, reward=100).json()["gig"]["id"]

    assert client.post(f"/api/gigs/{gid}/cancel", json={}, headers=other).status_code == 403
    r = client.post(f"/api/gigs/{gid}/cancel", json={}, headers=poster)
    assert r.json()["ok"] and r.json()["gig"]["status"] == "CANCELLED"

    me = client.get("/api/me", headers=poster).json()
    assert me["balance"] == 250 and me["escrow"] == 0

    # second cancel: no double refund; cancelled gigs can't be accepted
    r2 = client.post(f"/api/gigs/{gid}/cancel", json={}, headers=poster)
    assert r2.status_code in (400, 409, 404)
    me = client.get("/api/me", headers=poster).json()
    assert me["balance"] == 250
    assert client.post(f"/api/gigs/{gid}/accept", json={}, headers=other).json().get("error") == "not available"

    # refund shows in history
    tx = client.get("/api/transactions", headers=poster).json()["transactions"]
    assert any(t["type"] == "escrow_refund" and t["amount"] == 100 for t in tx)

    # cancelling mid-work is a clean 409 (dispute flow later)
    gid2 = post_gig(client, poster, reward=50).json()["gig"]["id"]
    client.post(f"/api/gigs/{gid2}/accept", json={}, headers=other)
    assert client.post(f"/api/gigs/{gid2}/cancel", json={}, headers=poster).status_code == 409


def test_ratings_update_average_once_per_participant(client):
    poster, poster_name = register(client, "9000000030")
    worker, worker_name = register(client, "9000000031")
    gid = post_gig(client, poster, reward=100).json()["gig"]["id"]

    # can't rate before completion
    assert client.post(f"/api/gigs/{gid}/rate", json={"stars": 5}, headers=poster).status_code == 409

    client.post(f"/api/gigs/{gid}/accept", json={}, headers=worker)
    client.post(f"/api/gigs/{gid}/complete", json={}, headers=poster)

    # outsider can't rate
    outsider, _ = register(client, "9000000032")
    assert client.post(f"/api/gigs/{gid}/rate", json={"stars": 1}, headers=outsider).status_code == 403

    # poster rates worker 4, worker rates poster 5
    r = client.post(f"/api/gigs/{gid}/rate", json={"stars": 4, "review": "quick"}, headers=poster).json()
    assert r["ok"] and r["new_rating"] == 4.0 and r["ratee"] == worker_name
    r = client.post(f"/api/gigs/{gid}/rate", json={"stars": 5}, headers=worker).json()
    assert r["ok"] and r["new_rating"] == 5.0 and r["ratee"] == poster_name

    # double rating blocked
    assert client.post(f"/api/gigs/{gid}/rate", json={"stars": 1}, headers=poster).status_code == 409

    # average updates as more ratings arrive
    auth, user2 = register(client, "9000000033")
    gid2 = post_gig(client, auth, reward=10).json()["gig"]["id"]
    client.post(f"/api/gigs/{gid2}/accept", json={}, headers=poster)  # reuse poster account as worker? no—
    # (poster here is the new user; use worker instead)
    # simpler: second rating for our worker from a second poster
    authB, _ = register(client, "9000000034")
    gidB = post_gig(client, authB, reward=10).json()["gig"]["id"]
    client.post(f"/api/gigs/{gidB}/accept", json={}, headers=worker)
    client.post(f"/api/gigs/{gidB}/complete", json={}, headers=authB)
    r = client.post(f"/api/gigs/{gidB}/rate", json={"stars": 2}, headers=authB).json()
    # worker now has 4 and 2 -> avg 3.0
    assert r["new_rating"] == 3.0

    # my_ratings lets the UI hide buttons
    rated = client.get("/api/my_ratings", headers=poster).json()["rated_gig_ids"]
    assert gid in rated


def test_profile_edit_updates_and_keeps_identity(client):
    auth, me = register(client, "9000000040", name="Old Name")
    r = client.post("/api/register", headers=auth, json={
        "name": "New Name", "role": "Worker / Runner",
        "major": "ECE '27", "dorm": "C-Block", "upi_id": "new@okaxis"})
    d = r.json()
    assert d["ok"] and d["username"] == me   # same account, never a new identity
    me2 = client.get("/api/me", headers=auth).json()
    assert me2["name"] == "New Name" and me2["major"] == "ECE '27"
    assert me2["dorm"] == "C-Block" and me2["upi_id"] == "new@okaxis"
    assert me2["balance"] == 250             # profile edits never touch the wallet


def test_unread_counts_and_read_receipts(client):
    alice, alice_name = register(client, "9000000050")
    bob, bob_name = register(client, "9000000051")

    # alice sends bob two messages: bob's list shows them unread
    client.post("/api/dm", json={"to_user": bob_name, "text": "one"}, headers=alice)
    client.post("/api/dm", json={"to_user": bob_name, "text": "two"}, headers=alice)
    d = client.get("/api/dm_list", headers=bob).json()
    conv = next(c for c in d["conversations"] if c["with"] == alice_name)
    assert conv["unread"] == 2 and d["total_unread"] >= 2

    # alice sees no unread on her side (they are HER messages)
    d2 = client.get("/api/dm_list", headers=alice).json()
    conv2 = next(c for c in d2["conversations"] if c["with"] == bob_name)
    assert conv2["unread"] == 0

    # opening the conversation marks it read
    client.get("/api/dm", params={"with_user": alice_name}, headers=bob)
    d3 = client.get("/api/dm_list", headers=bob).json()
    conv3 = next(c for c in d3["conversations"] if c["with"] == alice_name)
    assert conv3["unread"] == 0


def test_otp_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "OTP_MAX_PER_HOUR", 3)
    for i in range(3):
        assert client.post("/api/otp/send", json={"phone": "9000000060"}).json()["ok"], f"send {i+1} should pass"
    r = client.post("/api/otp/send", json={"phone": "9000000060"})
    assert r.status_code == 429 and r.json()["code"] == "otp_rate_limited"
    # a different phone is unaffected (limit is per number)
    assert client.post("/api/otp/send", json={"phone": "9000000061"}).json()["ok"]


# ----------------------------------------------------------------- public ----

def test_public_endpoints_stay_public(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/gigs").status_code == 200
    assert client.get("/api/auth/config").status_code == 200
    assert client.post("/api/otp/send", json={"phone": "1"}).status_code == 200
