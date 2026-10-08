import sqlite3, time, random, uuid, os, re, mimetypes
import base64, hashlib, hmac, json, secrets
from fastapi import FastAPI, Request, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import uuid

app = FastAPI()
# The frontend is served by this same app (same origin), so CORS headers are
# for THIRD-PARTY browser apps only. Default: none. Allow specific origins via
# ALLOWED_ORIGINS=https://app.example.com,https://other.example.com
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_credentials=bool(ALLOWED_ORIGINS),
                   allow_methods=["*"], allow_headers=["*"])

def _load_dotenv(path=".env"):
    """Minimal .env loader so config can live in one file. No extra dependency.
    Real environment variables always win, so Render/CI settings are unaffected."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f"[config] could not read {path}: {e}")

_load_dotenv()


# SQLite lives next to the code by default. On Render (or any host with an
# ephemeral filesystem) point DB_PATH at a mounted disk so sign-ups, gigs and
# wallet balances survive deploys and restarts:
#   DB_PATH=/var/data/shiftly.db        (Render persistent disk)
DB = os.environ.get("DB_PATH", "shiftly.db")
PHOTO_MAX_B64 = 273_000   # ~200 KB binary image, as a base64 data URL

def get_db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        name TEXT, role TEXT, major TEXT, dorm TEXT,
        avatar_color TEXT, balance REAL DEFAULT 0,
        escrow REAL DEFAULT 0, pending_payout REAL DEFAULT 0,
        total_earned REAL DEFAULT 0, rating REAL DEFAULT 5.0,
        upi_id TEXT DEFAULT '', created_at TEXT,
        email TEXT, auth_uid TEXT, email_verified INTEGER DEFAULT 0,
        photo_mime TEXT, photo_b64 TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS gigs (
        id TEXT PRIMARY KEY, title TEXT, category TEXT,
        reward REAL, urgency TEXT, location TEXT, instructions TEXT,
        poster TEXT, worker TEXT, status TEXT DEFAULT 'OPEN', created_at TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user TEXT, to_user TEXT, text TEXT,
        context TEXT, time TEXT,
        seen INTEGER DEFAULT 0
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT, type TEXT, amount REAL, note TEXT, time TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ratings (
        gig_id TEXT, rater TEXT, ratee TEXT,
        stars INTEGER, review TEXT, time TEXT,
        PRIMARY KEY (gig_id, rater, ratee)
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gateway TEXT, order_id TEXT, payment_id TEXT,
        username TEXT, amount_paise INTEGER, status TEXT,
        signature TEXT, payload TEXT, created_at TEXT,
        UNIQUE(payment_id)
    )""")
    conn.commit()
    conn.close()

init_db()

class RegisterReq(BaseModel):
    username: Optional[str] = None   # ignored — identity comes from the session token
    name: str
    role: str
    major: str
    dorm: str
    avatar_color: Optional[str] = "indigo"
    upi_id: Optional[str] = ""

class PostGigReq(BaseModel):
    username: Optional[str] = None   # ignored — identity comes from the session token
    title: str
    category: str
    reward: float
    urgency: str
    location: str
    instructions: str

class MsgReq(BaseModel):
    from_user: Optional[str] = None  # ignored — sender comes from the session token
    to_user: str
    text: str
    context: Optional[str] = ""

class AcceptReq(BaseModel):
    username: Optional[str] = None   # ignored — identity comes from the session token

class TopUpReq(BaseModel):
    username: Optional[str] = None   # ignored — identity comes from the session token
    amount: float

class UpiPayReq(BaseModel):
    username: Optional[str] = None   # ignored — identity comes from the session token
    amount: float
    pin: str  # mock verification

class RateReq(BaseModel):
    stars: int
    review: Optional[str] = ""

# Seed Indian demo users
CONN = get_db()
for u in [
    ("arjun","Arjun Mehta","Poster / Host","B.Tech CSE '25","VIT Boys Hostel A-Block","indigo","arjun@okaxis",250,35,0,180,4.9,"2026-10-04T09:00:00Z"),
    ("priya","Priya Sharma","Worker / Runner","BBA '26","VIT Girls Hostel C-Block","emerald","priya@ybl",45,0,67.9,310.2,5.0,"2026-10-04T09:00:00Z"),
    ("vikram","Vikram Reddy","Event Host","ECE '24","SRM Boys Hostel D-Block","amber","vikram@paytm",85,0,15,95,4.8,"2026-10-04T09:00:00Z")
]:
    try:
        CONN.execute(
            "INSERT OR IGNORE INTO users (username,name,role,major,dorm,avatar_color,"
            "upi_id,balance,escrow,pending_payout,total_earned,rating,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", u)
    except Exception as e:
        pass
CONN.commit()

# Seed Indian gigs
for g in [
    ("g1","Deliver Food from Campus Cafeteria to Hostel A", "Food Run", 45, "High", "VIT Main Cafeteria / Boys Hostel A", "Pick up 2 meals from Campus Cafeteria and deliver to room 304, Hostel A.", "arjun", None, "OPEN", "2026-10-04T10:00:00Z"),
    ("g2","Python / DSA Tutor for Mid-Term Prep", "Tutoring", 350, "Medium", "VIT Study Lounge (Library)", "Need 1 hour help reviewing Trees, Graphs, Dijkstra before tomorrow exam.", "arjun", "priya", "IN_PROGRESS", "2026-10-04T09:30:00Z"),
    ("g3","Move Study Table & Small Fridge to Hostel D", "Heavy Lifting", 400, "Low", "SRM Hostel D Lobby", "Help move a study table and compact fridge to room 412, Hostel D.", "vikram", None, "OPEN", "2026-10-04T08:30:00Z")
]:
    try:
        CONN.execute("INSERT OR IGNORE INTO gigs VALUES (?,?,?,?,?,?,?,?,?,?,?)", g)
    except:
        pass
CONN.commit()
CONN.close()

# Helper for msg key
def msg_key(a,b):
    return "|".join(sorted([a,b]))

# ---------------------------------------------------------------------------
# Sessions — one token format for BOTH login paths
# ---------------------------------------------------------------------------
# Google users arrive with a Firebase ID token (expires in 1h, no refresh).
# OTP users arrive with nothing at all. Rather than bolt two auth schemes onto
# every endpoint, we verify the caller once at login and hand back our own
# short-lived signed session token. Endpoints then only ever trust that.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
SESSION_HOURS  = int(os.environ.get("SESSION_HOURS", "24") or 24)
if not SESSION_SECRET:
    # Ephemeral fallback: sessions die on restart. Fine locally; on a real
    # deployment set SESSION_SECRET so users are not logged out every deploy.
    SESSION_SECRET = secrets.token_hex(32)
    print("[session] SESSION_SECRET not set - generating an ephemeral one. "
          "Sessions will not survive a restart.")

def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

def _unb64(s: str):
    return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))

PENDING_SECONDS = 1800   # OTP-verified, profile-not-done tokens live 30 min

def _sign(obj) -> str:
    payload = _b64(obj)
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(),
                   hashlib.sha256).hexdigest()[:32]
    return payload + "." + sig

def create_session(username: str) -> str:
    return _sign({"u": username, "exp": time.time() + SESSION_HOURS * 3600})

def create_pending_session(phone: str) -> str:
    """Issued after a correct OTP: proves ONLY that this phone was verified.
    Good enough to POST /api/register exactly once the profile is filled."""
    return _sign({"phone": phone, "pending": True,
                  "exp": time.time() + PENDING_SECONDS})

def read_session(token: str):
    """Return the signed payload if the token is genuine and unexpired, else None."""
    if not token or "." not in token:
        return None
    payload, _, sig = token.rpartition(".")
    expected = hmac.new(SESSION_SECRET.encode(), payload.encode(),
                        hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):   # constant time
        return None
    try:
        data = _unb64(payload)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data

def session_payload(authorization: str = Header(default="")) -> dict:
    """Dependency: any valid token (full OR pending OTP token)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, {"ok": False, "error": "Sign-in required.",
                                  "code": "no_session"})
    payload = read_session(authorization[7:].strip())
    if not payload:
        raise HTTPException(401, {"ok": False,
                                  "error": "Session expired or invalid. Sign in again.",
                                  "code": "bad_session"})
    return payload

def current_user(authorization: str = Header(default="")) -> str:
    """Dependency: a FULL session. Rejects OTP-pending tokens."""
    payload = session_payload(authorization)
    if payload.get("pending") or not payload.get("u"):
        raise HTTPException(401, {"ok": False,
                                  "error": "Finish creating your account first.",
                                  "code": "registration_pending"})
    return payload["u"]


@app.post("/api/register")
def register(req: RegisterReq, auth: dict = Depends(session_payload)):
    """Create or complete a profile. The username is NEVER taken from the body:
      - pending OTP token  -> username derived from the verified phone number
      - full session       -> profile update for the session's own user
    """
    conn = get_db()
    if auth.get("pending"):
        phone = auth.get("phone") or ""
        username = re.sub(r"[^0-9]", "", phone) or _unique_username(conn, "user")
    else:
        username = auth["u"]
    conn.execute("INSERT OR IGNORE INTO users (username,name,role,major,dorm,avatar_color,balance,escrow,pending_payout,total_earned,rating,upi_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (username, req.name, req.role, req.major, req.dorm, req.avatar_color, 250, 0, 0, 0, 5.0, req.upi_id or "", datetime.now(timezone.utc).isoformat()))
    conn.execute("UPDATE users SET name=?, role=?, major=?, dorm=?, avatar_color=?, upi_id=? WHERE username=?",
                 (req.name, req.role, req.major, req.dorm, req.avatar_color, req.upi_id or "", username))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    # All done — upgrade the caller to a full session, whatever token they came in on.
    return {"ok": True, "username": username, "user": dict(row) if row else {},
            "session": create_session(username)}

@app.get("/api/users")
def list_users(user: str = Depends(current_user)):
    """Directory of public profiles. Balance/escrow/UPI/email/auth data never leave
    the server from here — other users have no business seeing them."""
    conn = get_db()
    rows = conn.execute(
        "SELECT username,name,role,major,dorm,avatar_color,rating,total_earned,created_at,photo_b64 FROM users").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["has_photo"] = bool(d.pop("photo_b64", None))
        out.append(d)
    return {"users": out}

@app.get("/api/me")
def me(username: str = Depends(current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, {"ok": False, "error": "Account not found. Please register again."})
    user = dict(row)
    user.pop("photo_b64", None)
    user["has_photo"] = bool(user.get("photo_b64") or user.get("photo_mime"))
    user.pop("photo_mime", None)
    user["ok"] = True
    return user

# --- Profile photos ---------------------------------------------------------
# Stored in SQLite as base64 data (max ~200 KB image). Served back over
# /api/users/<u>/photo with long cache headers — one request per user, then
# the browser CDN-caches it so gig cards cost nothing.
class PhotoReq(BaseModel):
    data_url: str  # e.g. "data:image/jpeg;base64,/9j/4AAQ..." — "" to clear

PHOTO_DATA_RE = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$")

@app.put("/api/me/photo")
def set_photo(req: PhotoReq, username: str = Depends(current_user)):
    conn = get_db()
    if not req.data_url.strip():
        conn.execute("UPDATE users SET photo_mime=NULL, photo_b64=NULL WHERE username=?", (username,))
        conn.commit(); conn.close()
        return {"ok": True, "has_photo": False}
    m = PHOTO_DATA_RE.match(req.data_url.strip())
    if not m:
        conn.close()
        raise HTTPException(400, {"ok": False, "error": "photo must be a PNG, JPEG or WebP image."})
    mime, b64 = m.group(1), re.sub(r"\s+", "", m.group(2))
    if len(b64) > PHOTO_MAX_B64:
        conn.close()
        raise HTTPException(413, {"ok": False,
            "error": "Image is too large. Crop or compress it below ~200 KB."})
    conn.execute("UPDATE users SET photo_mime=?, photo_b64=? WHERE username=?",
                 (mime, b64, username))
    conn.commit(); conn.close()
    return {"ok": True, "has_photo": True, "url": f"/api/users/{username}/photo"}

@app.get("/api/users/{username}/photo")
def user_photo(username: str):
    from fastapi.responses import Response
    conn = get_db()
    row = conn.execute("SELECT photo_mime, photo_b64 FROM users WHERE username=?",
                       (username,)).fetchone()
    conn.close()
    if not row or not row["photo_b64"]:
        raise HTTPException(404, {"ok": False, "error": "no photo"})
    return Response(content=base64.b64decode(row["photo_b64"]),
                    media_type=row["photo_mime"] or "image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})

MAX_GIG_REWARD = 100000      # sanity cap — any real gig is far below this
MAX_TOPUP      = 100000      # mock wallet cap; real gateway replaces this

def _move_to_escrow(conn, username: str, amount: float) -> bool:
    """Atomically move balance -> escrow ONLY if the funds actually exist.
    Returns False (and moves nothing) when the balance can't cover it, so
    escrow can never  be backed by money nobody has."""
    cur = conn.execute(
        "UPDATE users SET balance = balance - ?, escrow = escrow + ? "
        "WHERE username=? AND balance >= ?",
        (amount, amount, username, amount))
    return cur.rowcount == 1

@app.post("/api/gigs")
def post_gig(req: PostGigReq, poster: str = Depends(current_user)):
    if not (0 < req.reward <= MAX_GIG_REWARD):
        raise HTTPException(400, {"ok": False,
            "error": f"Reward must be between ₹1 and ₹{MAX_GIG_REWARD:,}."})
    gid = str(uuid.uuid4())[:8]
    conn = get_db()
    if not _move_to_escrow(conn, poster, req.reward):
        conn.close()
        raise HTTPException(400, {"ok": False,
            "error": "Insufficient balance. Top up your wallet first."})
    conn.execute("INSERT INTO gigs (id,title,category,reward,urgency,location,instructions,poster,worker,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (gid, req.title, req.category, req.reward, req.urgency, req.location, req.instructions, poster, None, "OPEN", datetime.now(timezone.utc).isoformat()))
    # Escrow hold from poster
    conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?, ?,?)",
                 (poster, "escrow_hold", -req.reward, f"Gig: {req.title}", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return {"ok": True, "gig": {"id":gid,"title":req.title,"status":"OPEN"}}

@app.get("/api/gigs")
def get_gigs():
    conn = get_db()
    rows = conn.execute(
        "SELECT g.*, u.name AS poster_name, COALESCE(u.rating, 5.0) AS poster_rating, "
        "u.photo_b64 AS _p FROM gigs g LEFT JOIN users u ON u.username = g.poster "
        "ORDER BY g.created_at DESC").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["poster_has_photo"] = bool(d.pop("_p", None))
        out.append(d)
    return {"gigs": out}

@app.get("/api/gigs/{gig_id}")
def get_gig(gig_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    conn.close()
    return {"gig": dict(row)} if row else {"error":"not found"}

@app.post("/api/gigs/{gig_id}/accept")
def accept_gig(gig_id: str, req: AcceptReq, worker: str = Depends(current_user)):
    conn = get_db()
    gig = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    if not gig or gig["status"] != "OPEN":
        conn.close(); return {"error":"not available"}
    if gig["poster"] == worker:
        conn.close()
        raise HTTPException(403, {"ok": False, "error": "You can't accept your own gig."})
    conn.execute("UPDATE gigs SET status='IN_PROGRESS', worker=? WHERE id=?", (worker, gig_id))
    poster = gig["poster"]
    # Auto system message
    conn.execute("INSERT INTO messages (from_user,to_user,text,context,time) VALUES (?,?,?,?,?)",
                 ("system", poster, f"{worker} accepted your gig: '{gig['title']}'. Chat now to coordinate.", f"Gig: {gig['title']} | Reward: ₹{gig['reward']}", datetime.now(timezone.utc).isoformat()))
    # Auto worker message
    conn.execute("INSERT INTO messages (from_user,to_user,text,context,time) VALUES (?,?,?,?,?)",
                 (worker, poster, f"Namaste {dict(conn.execute('SELECT name FROM users WHERE username=?',(poster,)).fetchone() or {'name':'there'})['name']}, I'm on it! Planning to complete by EOD.", "", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    updated = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    conn.close()
    return {"ok": True, "gig": dict(updated), "dm_key": msg_key(poster, worker)}

@app.post("/api/dm")
def send_dm(req: MsgReq, sender: str = Depends(current_user)):
    conn = get_db()
    conn.execute("INSERT INTO messages (from_user,to_user,text,context,time) VALUES (?,?,?,?,?)",
                 (sender, req.to_user, req.text, req.context or "", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/dm")
def get_dm(with_user: str, me: str = Depends(current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE ((from_user=? AND to_user=?) OR (from_user=? AND to_user=?)) ORDER BY time ASC",
        (me, with_user, with_user, me)).fetchall()
    # Opening this conversation is an explicit act of reading it.
    conn.execute("UPDATE messages SET seen=1 WHERE from_user=? AND to_user=? AND seen=0",
                 (with_user, me))
    conn.commit()
    conn.close()
    return {"messages": [dict(r) for r in rows]}

@app.get("/api/dm_list")
def dm_list(me: str = Depends(current_user)):
    conn = get_db()
    # Get unique partners for this user
    rows = conn.execute(
        "SELECT DISTINCT CASE WHEN from_user=? THEN to_user ELSE from_user END as partner FROM messages WHERE from_user=? OR to_user=?",
        (me, me, me)).fetchall()
    partners = [r["partner"] for r in rows if r["partner"] and r["partner"]!=me]
    result = []
    total_unread = 0
    for p in partners:
        name_row = conn.execute("SELECT name, photo_b64 FROM users WHERE username=?", (p,)).fetchone()
        last = conn.execute("SELECT text FROM messages WHERE (from_user=? AND to_user=?) OR (from_user=? AND to_user=?) ORDER BY time DESC LIMIT 1",
                            (me, p, p, me)).fetchone()
        unread = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE from_user=? AND to_user=? AND seen=0",
            (p, me)).fetchone()["c"]
        total_unread += unread
        result.append({"with": p, "name": name_row["name"] if name_row else p,
                       "has_photo": bool(name_row and name_row["photo_b64"]),
                       "last": last["text"] if last else "", "unread": unread})
    conn.close()
    return {"conversations": result, "total_unread": total_unread}

@app.post("/api/wallet/topup")
def topup(req: TopUpReq, username: str = Depends(current_user)):
    if not (0 < req.amount <= MAX_TOPUP):
        raise HTTPException(400, {"ok": False,
            "error": f"Top-up amount must be between ₹1 and ₹{MAX_TOPUP:,}."})
    conn = get_db()
    conn.execute("UPDATE users SET balance = balance + ? WHERE username=?", (req.amount, username))
    conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?, ?,?)",
                 (username, "topup", req.amount, "UPI / Wallet top-up", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT balance FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return {"ok": True, "balance": row["balance"] if row else 0}

@app.post("/api/wallet/upi_pay")
def upi_pay(req: UpiPayReq, username: str = Depends(current_user)):
    # Mock UPI verification (4-digit PIN)
    if req.pin != "1234":
        return {"error":"Invalid UPI PIN. Try 1234 for demo."}
    if not (0 < req.amount <= MAX_TOPUP):
        raise HTTPException(400, {"ok": False, "error": "Invalid amount."})
    conn = get_db()
    if not _move_to_escrow(conn, username, req.amount):
        conn.close()
        raise HTTPException(400, {"ok": False,
            "error": "Insufficient balance to lock that much into escrow."})
    conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?, ?,?)",
                 (username, "upi_pay", -req.amount, f"UPI Payment Lock — Demo PIN verified", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return {"ok": True, "message":"UPI PIN verified. Funds locked in escrow."}

# ---------------------------------------------------------------------------
# Razorpay — real UPI payments for wallet top-ups (test mode free, live after KYC)
# ---------------------------------------------------------------------------
# How it fits together:
#   1. Browser asks our server for an order: POST /api/payments/order {amount}
#   2. Server creates the order with Razorpay (server->server, key secret never
#      leaves the box) and remembers it as "created" in the payments table.
#   3. Browser opens Razorpay Checkout with key_id + order_id; user pays via UPI.
#   4. Razorpay calls back to the browser with payment_id + signature; browser
#      POSTs them to /api/payments/verify.
#   5. Server re-computes HMAC-SHA256(order_id|payment_id, key_secret) and only
#      then credits the wallet — a payment the signature can't vouch for gets
#      nobody's rupees. The payments row flips created -> verified, with
#      UNIQUE(payment_id) making the credit provably once-only.
# No keys set => RZP_ENABLED is False and the app stays in demo wallet mode.
RAZORPAY_KEY_ID     = os.environ.get("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
# Plausible-key sanity check, same spirit as config_looks_ready() for Firebase:
# a truncated paste must never turn the "Pay with UPI" button on.
RZP_ENABLED = bool(
    re.fullmatch(r"rzp_(test|live)_[A-Za-z0-9]{10,}", RAZORPAY_KEY_ID or "") and
    len(RAZORPAY_KEY_SECRET) >= 20)

class PaymentOrderReq(BaseModel):
    amount: float   # rupees — capped the same as the mock top-up

class PaymentVerifyReq(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

def _rzp_record(order_id: str, username: str, amount_paise: int, status: str,
                payment_id: str = None, signature: str = None):
    conn = get_db()
    row = conn.execute("SELECT id FROM payments WHERE order_id=? AND username=?",
                       (order_id, username)).fetchone()
    if row:
        conn.execute("UPDATE payments SET payment_id=COALESCE(?,payment_id),"
                     " signature=COALESCE(?,signature), status=? WHERE id=?",
                     (payment_id, signature, status, row["id"]))
    else:
        conn.execute("INSERT INTO payments (gateway,order_id,payment_id,username,"
                     "amount_paise,status,signature,payload,created_at) "
                     "VALUES ('razorpay',?,?,?,?,?,?,NULL,?)",
                     (order_id, payment_id, username, amount_paise, status,
                      signature, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()

@app.post("/api/payments/order")
def create_payment_order(req: PaymentOrderReq, username: str = Depends(current_user)):
    if not RZP_ENABLED:
        raise HTTPException(503, {"ok": False, "code": "payments_not_configured",
            "error": "Card/UPI payments are being set up. The wallet top-up below works in the meantime."})
    if not (0 < req.amount <= MAX_TOPUP):
        raise HTTPException(400, {"ok": False,
            "error": f"Amount must be between ₹1 and ₹{MAX_TOPUP:,}."})
    amount_paise = int(round(req.amount * 100))
    try:
        import requests
        r = requests.post("https://api.razorpay.com/v1/orders",
                          auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
                          json={"amount": amount_paise, "currency": "INR",
                                "receipt": "shiftly_" + uuid.uuid4().hex[:16]},
                          timeout=15)
        if r.status_code != 200:
            print(f"[razorpay] order call failed: {r.status_code} {r.text[:300]}")
            raise HTTPException(502, {"ok": False,
                "error": "Payment gateway rejected the order. Try again in a moment."})
        order = r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, {"ok": False,
            "error": f"Could not reach the payment gateway: {e}"})
    _rzp_record(order["id"], username, amount_paise, "created")
    return {"ok": True, "order_id": order["id"], "amount": amount_paise,
            "currency": "INR", "key_id": RAZORPAY_KEY_ID}

@app.post("/api/payments/verify")
def verify_payment(req: PaymentVerifyReq, username: str = Depends(current_user)):
    """Razorpay says "paid"; we only believe it if the signature checks out."""
    if not RZP_ENABLED:
        raise HTTPException(503, {"ok": False, "error": "Payments are not configured."})
    conn = get_db()
    order = conn.execute("SELECT * FROM payments WHERE order_id=? AND username=?",
                         (req.razorpay_order_id, username)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(404, {"ok": False, "error": "Unknown payment order."})
    if order["status"] == "verified":
        # Already credited once — idempotent hand-shake, NOT an error: the
        # browser can safely retry this call (double-tap, flaky network).
        conn.close()
        return {"ok": True, "verified": True, "duplicate": True}
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        f"{req.razorpay_order_id}|{req.razorpay_payment_id}".encode(),
        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, req.razorpay_signature or ""):
        _rzp_record(req.razorpay_order_id, username, order["amount_paise"],
                    "failed", req.razorpay_payment_id, req.razorpay_signature)
        conn.close()
        raise HTTPException(400, {"ok": False,
            "error": "Payment signature did not verify. If money left your account, contact support with your UPI ref number."})
    # Signature good — credit exactly what the order was created for, once.
    amount_rupees = order["amount_paise"] / 100.0
    conn.execute("UPDATE users SET balance = balance + ? WHERE username=?",
                 (amount_rupees, username))
    conn.execute("UPDATE payments SET status='verified', payment_id=?, signature=? WHERE id=?",
                 (req.razorpay_payment_id, req.razorpay_signature, order["id"]))
    conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?,?,?)",
                 (username, "topup", amount_rupees,
                  f"UPI top-up via Razorpay · {req.razorpay_payment_id}",
                  datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT balance FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return {"ok": True, "verified": True, "balance": row["balance"] if row else 0}

@app.post("/api/gigs/{gig_id}/complete")
def complete_gig(gig_id: str, req: AcceptReq, caller: str = Depends(current_user)):
    conn = get_db()
    gig = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    if not gig: conn.close(); return {"error":"not found"}
    poster = gig["poster"]
    if caller != poster:
        conn.close()
        raise HTTPException(403, {"ok": False,
                                  "error": "Only the gig's poster can mark it complete and release the payment."})
    if gig["status"] != "IN_PROGRESS" or not gig["worker"]:
        conn.close()
        raise HTTPException(409, {"ok": False,
                                  "error": "This gig has no worker yet — completing it now would lock your escrow."})
    conn.execute("UPDATE gigs SET status='COMPLETED' WHERE id=?", (gig_id,))
    worker = gig["worker"]
    if poster and worker:
        payout = float(gig["reward"]) * 0.97
        conn.execute("UPDATE users SET escrow = MAX(0, escrow - ?), pending_payout = MAX(0, pending_payout - ?) WHERE username=?",
                     (float(gig["reward"]), payout, poster))
        # Give worker payout
        conn.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE username=?",
                     (payout, payout, worker))
        conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?, ?,?)",
                     (worker, "payout", payout, f"Settlement — Gig {gig_id}", datetime.now(timezone.utc).isoformat()))
        # poster's feed: show their escrow actually settled (money left via this gig)
        conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?,?,?)",
                     (poster, "escrow_settled", 0, f"Escrow settled — paid ₹{payout:.2f} to {worker} for '{gig['title']}'",
                      datetime.now(timezone.utc).isoformat()))
        # Auto message
        conn.execute("INSERT INTO messages (from_user,to_user,text,context,time) VALUES (?,?,?,?,?)",
                     ("system", poster, f"Gig '{gig['title']}' completed. Worker has been paid ₹{payout:.2f} (97%).", "", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    updated = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    conn.close()
    return {"ok": True, "gig": dict(updated)}

# --- Transaction history ----------------------------------------------------
# The table has always recorded every money movement; users just couldn't see
# it. Now they can — but only their OWN rows.
@app.get("/api/transactions")
def my_transactions(username: str = Depends(current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT type, amount, note, time FROM transactions WHERE username=? "
        "ORDER BY time DESC, id DESC LIMIT 50", (username,)).fetchall()
    conn.close()
    return {"ok": True, "transactions": [dict(r) for r in rows]}

# --- Gig cancellation -------------------------------------------------------
# Posting locks escrow instantly. Without this endpoint, a gig nobody accepts
# traps the poster's money forever. OPEN gigs can always be cancelled: the
# escrow goes straight back. Once a worker has accepted, cancelling needs a
# dispute flow (roadmap level 4) — so it's a clean 409 here, not silent damage.
@app.post("/api/gigs/{gig_id}/cancel")
def cancel_gig(gig_id: str, poster: str = Depends(current_user)):
    conn = get_db()
    gig = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    if not gig:
        conn.close(); return JSONResponse(status_code=404, content={"ok": False, "error": "Gig not found."})
    if gig["poster"] != poster:
        conn.close()
        raise HTTPException(403, {"ok": False, "error": "Only the gig's poster can cancel it."})
    if gig["status"] != "OPEN":
        conn.close()
        raise HTTPException(409, {"ok": False,
            "error": "A worker has already accepted this gig — cancelling mid-work needs a dispute flow (coming later)."})
    reward = float(gig["reward"])
    cur = conn.execute(
        "UPDATE users SET balance = balance + ?, escrow = escrow - ? "
        "WHERE username=? AND escrow >= ?", (reward, reward, poster, reward))
    if cur.rowcount != 1:
        conn.close()
        raise HTTPException(409, {"ok": False, "error": "Escrow mismatch — contact support."})
    conn.execute("UPDATE gigs SET status='CANCELLED' WHERE id=? AND status='OPEN'", (gig_id,))
    conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?,?,?)",
                 (poster, "escrow_refund", reward, f"Gig '{gig['title']}' cancelled — escrow returned",
                  datetime.now(timezone.utc).isoformat()))
    conn.commit()
    updated = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    conn.close()
    return {"ok": True, "gig": dict(updated)}

# --- Ratings ----------------------------------------------------------------
# The rating column existed but nothing could ever change it — everyone was
# 5.0 forever. Now each completed gig lets BOTH participants rate each other,
# once. The running average lives on the users row.
@app.post("/api/gigs/{gig_id}/rate")
def rate_gig(gig_id: str, req: RateReq, rater: str = Depends(current_user)):
    if not (1 <= req.stars <= 5):
        raise HTTPException(400, {"ok": False, "error": "Rating must be 1–5 stars."})
    conn = get_db()
    gig = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    if not gig:
        conn.close(); return JSONResponse(status_code=404, content={"ok": False, "error": "Gig not found."})
    if gig["status"] != "COMPLETED":
        conn.close()
        raise HTTPException(409, {"ok": False, "error": "You can rate a gig after it is completed."})
    poster, worker = gig["poster"], gig["worker"]
    if rater == poster:
        ratee = worker
    elif rater == worker:
        ratee = poster
    else:
        conn.close()
        raise HTTPException(403, {"ok": False, "error": "Only the two people on this gig can rate it."})
    if not ratee:
        conn.close()
        raise HTTPException(409, {"ok": False, "error": "This gig had no counterpart to rate."})
    cur = conn.execute(
        "INSERT OR IGNORE INTO ratings (gig_id,rater,ratee,stars,review,time) VALUES (?,?,?,?,?,?)",
        (gig_id, rater, ratee, req.stars, (req.review or "")[:300], datetime.now(timezone.utc).isoformat()))
    if cur.rowcount != 1:
        conn.close()
        raise HTTPException(409, {"ok": False, "error": "You have already rated this gig."})
    avg = conn.execute("SELECT ROUND(AVG(stars),1) FROM ratings WHERE ratee=?",
                       (ratee,)).fetchone()[0]
    conn.execute("UPDATE users SET rating=? WHERE username=?", (avg, ratee))
    conn.commit()
    conn.close()
    return {"ok": True, "ratee": ratee, "new_rating": avg}

@app.get("/api/my_ratings")
def my_ratings(username: str = Depends(current_user)):
    """Gigs I have already rated — lets the UI hide the Rate button."""
    conn = get_db()
    rows = conn.execute("SELECT gig_id FROM ratings WHERE rater=?", (username,)).fetchall()
    conn.close()
    return {"ok": True, "rated_gig_ids": [r["gig_id"] for r in rows]}

# --- OTP Mock System (replace with Twilio/Msg91 for real SMS) ---
import random, time
otp_store = {}

from pydantic import BaseModel
class OtpSendReq(BaseModel):
    phone: str

class OtpVerifyReq(BaseModel):
    phone: str
    otp: str

# -- OTP abuse guard ---------------------------------------------------------
# Today each send is free (mock). The moment a real SMS provider is plugged in,
# each send costs money — and an open endpoint is an invitation to spend it.
# Cap: OTP_MAX_PER_HOUR per phone (default 8), tracked in-process. At the
# Razorpay/real-SMS level this moves to Redis + per-IP caps too.
OTP_MAX_PER_HOUR = int(os.environ.get("OTP_MAX_PER_HOUR", "8") or 8)
otp_send_log = {}   # phone -> [timestamps]

def _otp_allow(phone: str) -> int:
    """Returns seconds to wait if over the limit, else 0."""
    now = time.time()
    attempts = [t for t in otp_send_log.get(phone, []) if now - t < 3600]
    otp_send_log[phone] = attempts
    if len(attempts) >= OTP_MAX_PER_HOUR:
        return int(3600 - (now - min(attempts)))
    attempts.append(now)
    return 0

@app.post("/api/otp/send")
def otp_send(req: OtpSendReq):
    phone = req.phone.strip()
    if not phone.startswith("+"):
        phone = "+91" + phone.replace("+91","").replace(" ","")
    wait = _otp_allow(phone)
    if wait:
        return JSONResponse(status_code=429, content={
            "ok": False, "code": "otp_rate_limited",
            "error": f"Too many OTP requests for this number. Try again in ~{max(1, wait // 60)} min."})
    code = str(random.randint(100000, 999999))
    otp_store[phone] = {"code": code, "expires": time.time() + 300}
    # Mock only — real: call Twilio / Msg91 here
    return {"ok": True, "message": f"OTP sent to {phone}", "demo_code": code, "expires_in": 300}

@app.post("/api/otp/verify")
def otp_verify(req: OtpVerifyReq):
    phone = req.phone.strip()
    if not phone.startswith("+"):
        phone = "+91" + phone.replace("+91","").replace(" ","")
    entry = otp_store.get(phone)
    if not entry or entry["expires"] < time.time():
        return {"ok": False, "error": "OTP expired or not sent. Request again."}
    if entry["code"] != req.otp:
        return {"ok": False, "error": "Incorrect OTP. Try again."}
    del otp_store[phone]   # one-shot: an accepted code can never be replayed
    # Verify success — the caller gets a short-lived token proving the phone is
    # verified; /api/register exchanges it for a full account session.
    return {"ok": True, "phone": phone, "message": "Verified. Proceed to profile.",
            "pending_token": create_pending_session(phone)}

# ---------------------------------------------------------------------------
# Google Sign-In (Firebase Authentication) — free tier, no service account
# ---------------------------------------------------------------------------
# Set these env vars (Render dashboard / shell). Only PROJECT_ID is needed for
# token verification; the rest are public web config handed to the browser.
FIREBASE_PROJECT_ID  = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
FIREBASE_API_KEY     = os.environ.get("FIREBASE_API_KEY", "").strip()
FIREBASE_AUTH_DOMAIN = os.environ.get("FIREBASE_AUTH_DOMAIN", "").strip()
FIREBASE_APP_ID      = os.environ.get("FIREBASE_APP_ID", "").strip()
# Optional, comma-separated: "vitstudent.ac.in,vit.ac.in". Empty = any Google account.
ALLOWED_EMAIL_DOMAINS = [d.strip().lower().lstrip("@")
                         for d in os.environ.get("ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()]

try:
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
    from google.auth.exceptions import TransportError
    GOOGLE_AUTH_AVAILABLE = True
except Exception:
    GOOGLE_AUTH_AVAILABLE = False
    TransportError = None

class GoogleAuthReq(BaseModel):
    id_token: str

class AuthError(Exception):
    def __init__(self, code, message, status=401):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

def _migrate_messages_table():
    """Add the read-receipt column to an existing messages table (safe to re-run)."""
    conn = get_db()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "seen" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN seen INTEGER DEFAULT 0")
    conn.commit()
    conn.close()

def _migrate_users_table():
    """Add Google-auth columns to an existing users table (safe to re-run)."""
    conn = get_db()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for name, ddl in (("email", "TEXT"), ("auth_uid", "TEXT"),
                      ("email_verified", "INTEGER DEFAULT 0"),
                      ("photo_mime", "TEXT"), ("photo_b64", "TEXT")):
        if name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) "
                 "WHERE email IS NOT NULL AND email != ''")
    conn.commit()
    conn.close()

def _unique_username(conn, base):
    """Turn an email local-part into a free username."""
    base = re.sub(r"[^a-z0-9]", "", (base or "").lower())[:20] or "user"
    if not conn.execute("SELECT 1 FROM users WHERE username=?", (base,)).fetchone():
        return base
    for n in range(2, 9999):
        cand = f"{base}{n}"
        if not conn.execute("SELECT 1 FROM users WHERE username=?", (cand,)).fetchone():
            return cand
    return f"user{uuid.uuid4().hex[:8]}"

def config_looks_ready() -> bool:
    """True only when the values are present AND plausibly real.
    Stops a leftover placeholder from flipping the button on, which would
    otherwise fail later as a confusing Firebase error in the browser."""
    if not (FIREBASE_PROJECT_ID and FIREBASE_API_KEY and GOOGLE_AUTH_AVAILABLE):
        return False
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,62}", FIREBASE_PROJECT_ID):
        return False
    if not re.fullmatch(r"AIza[0-9A-Za-z_\-]{35}", FIREBASE_API_KEY):   # 39 chars
        return False
    return True

def verify_firebase_id_token(token: str) -> dict:
    """Verify a Firebase ID token using Google's public certs. No service account."""
    if not FIREBASE_PROJECT_ID:
        raise AuthError("not_configured",
                        "Google sign-in is not configured on this server yet. "
                        "Set FIREBASE_PROJECT_ID and the other FIREBASE_* env vars.", 503)
    if not GOOGLE_AUTH_AVAILABLE:
        raise AuthError("missing_dependency",
                        "google-auth is not installed on the server. Run: pip install -r requirements.txt", 503)
    if not token or token.count(".") != 2:
        raise AuthError("bad_token", "Malformed ID token.", 400)
    try:
        claims = google_id_token.verify_firebase_token(
            token, google_requests.Request(), audience=FIREBASE_PROJECT_ID)
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        # Distinguish "Google was unreachable" (our problem, 503, retryable) from
        # "this token is junk" (caller's problem, 401). Match on exception TYPE:
        # an unknown key id also says "certificate", but that is a bad token, not
        # a network failure, and reporting it as 503 sends you debugging the
        # wrong thing entirely.
        network_types = tuple(
            t for t in (globals().get("TransportError"), getattr(getattr(google_requests, "exceptions", None), "TransportError", None)) if t
        )
        is_network = isinstance(e, network_types) if network_types else False
        if not is_network and isinstance(e, (ConnectionError, TimeoutError, OSError)):
            is_network = True
        if is_network:
            raise AuthError("cert_fetch_failed",
                            f"Could not reach Google to verify the token: {msg}", 503)
        raise AuthError("bad_token", f"Could not verify Google token: {msg}", 401)
    if not claims.get("sub"):
        raise AuthError("bad_token", "Token is missing a subject (sub) claim.", 401)
    return claims

@app.get("/api/health")
def health():
    """Uptime monitor / Render health check. Includes feature flags so a deploy
    checklist can be verified from one URL."""
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat(),
            "google_sign_in": config_looks_ready(), "payments": RZP_ENABLED}

@app.get("/api/auth/config")
def auth_config():
    """Public web config for the browser. Firebase web config is not a secret."""
    return {
        "google_sign_in": config_looks_ready(),
        "firebase": {
            "apiKey": FIREBASE_API_KEY,
            "authDomain": FIREBASE_AUTH_DOMAIN,
            "projectId": FIREBASE_PROJECT_ID,
            "appId": FIREBASE_APP_ID,
        },
        "allowed_domains": ALLOWED_EMAIL_DOMAINS,
        "payments": {
            "enabled": RZP_ENABLED,
            "key_id": RAZORPAY_KEY_ID if RZP_ENABLED else "",   # publishable by design
        },
    }

@app.post("/api/auth/google")
def auth_google(req: GoogleAuthReq):
    """Verify a Google sign-in and create/link the local account."""
    try:
        claims = verify_firebase_id_token(req.id_token)
    except AuthError as e:
        return JSONResponse(status_code=e.status, content={"ok": False, "error": e.message, "code": e.code})

    email = (claims.get("email") or "").strip().lower()
    if not email:
        return JSONResponse(status_code=400, content={
            "ok": False, "code": "no_email",
            "error": "That Google account has no email address attached."})
    if not claims.get("email_verified"):
        return JSONResponse(status_code=403, content={
            "ok": False, "code": "email_unverified",
            "error": "That Google account's email is not verified with Google."})
    if ALLOWED_EMAIL_DOMAINS:
        domain = email.rsplit("@", 1)[-1]
        if domain not in ALLOWED_EMAIL_DOMAINS:
            return JSONResponse(status_code=403, content={
                "ok": False, "code": "domain_not_allowed",
                "error": f"{domain} is not an allowed campus domain. Allowed: {', '.join(ALLOWED_EMAIL_DOMAINS)}"})

    uid = claims.get("sub") or ""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email IS NOT NULL AND email != '' AND lower(email)=?",
                       (email,)).fetchone()
    if not row and uid:
        row = conn.execute("SELECT * FROM users WHERE auth_uid=?", (uid,)).fetchone()

    is_new = False
    if row:
        conn.execute("UPDATE users SET email=?, auth_uid=?, email_verified=1, "
                     "name=CASE WHEN ? != '' THEN ? ELSE name END WHERE username=?",
                     (email, uid, claims.get("name") or "", claims.get("name") or "", row["username"]))
        conn.commit()
        username = row["username"]
    else:
        username = _unique_username(conn, email.split("@")[0])
        conn.execute(
            "INSERT INTO users (username,name,role,major,dorm,avatar_color,balance,escrow,"
            "pending_payout,total_earned,rating,upi_id,created_at,email,auth_uid,email_verified) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (username, claims.get("name") or email.split("@")[0].title(), "Both", "", "",
             "indigo", 250, 0, 0, 0, 5.0, "", datetime.now(timezone.utc).isoformat(),
             email, uid, 1))
        conn.commit()
        is_new = True

    updated = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    user = dict(updated)
    return {
        "ok": True, "user": user, "is_new": is_new, "email": email,
        "username": username, "session": create_session(username),
        "profile_complete": bool((user.get("major") or "").strip() and (user.get("dorm") or "").strip()),
    }

_migrate_users_table()     # runs after the helpers above are defined
_migrate_messages_table()

class CachedStaticFiles(StaticFiles):
    """Images get long CDN-friendly caching; HTML is always re-validated so a
    deploy is visible on the next refresh, not after the old tab dies."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            ext = os.path.splitext(path)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
                resp.headers["Cache-Control"] = "public, max-age=86400"
            elif ext in (".html", ""):
                resp.headers["Cache-Control"] = "no-cache"
        return resp

app.mount("/", CachedStaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn, os
    port = int(os.environ.get("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)

