import sqlite3, time, random, uuid, os, re
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import uuid

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DB = "shiftly.db"

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
        upi_id TEXT DEFAULT '', created_at TEXT
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
        context TEXT, time TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT, type TEXT, amount REAL, note TEXT, time TEXT
    )""")
    conn.commit()
    conn.close()

init_db()

class RegisterReq(BaseModel):
    username: str
    name: str
    role: str
    major: str
    dorm: str
    avatar_color: Optional[str] = "indigo"
    upi_id: Optional[str] = ""

class PostGigReq(BaseModel):
    username: str
    title: str
    category: str
    reward: float
    urgency: str
    location: str
    instructions: str

class MsgReq(BaseModel):
    from_user: str
    to_user: str
    text: str
    context: Optional[str] = ""

class AcceptReq(BaseModel):
    username: str

class TopUpReq(BaseModel):
    username: str
    amount: float

class UpiPayReq(BaseModel):
    username: str
    amount: float
    pin: str  # mock verification

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

@app.post("/api/register")
def register(req: RegisterReq):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (username,name,role,major,dorm,avatar_color,balance,escrow,pending_payout,total_earned,rating,upi_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (req.username, req.name, req.role, req.major, req.dorm, req.avatar_color, 250, 0, 0, 0, 5.0, req.upi_id or "", datetime.now(timezone.utc).isoformat()))
    conn.execute("UPDATE users SET name=?, role=?, major=?, dorm=?, avatar_color=?, upi_id=? WHERE username=?",
                 (req.name, req.role, req.major, req.dorm, req.avatar_color, req.upi_id or "", req.username))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE username=?", (req.username,)).fetchone()
    conn.close()
    return {"ok": True, "user": dict(row) if row else {}}

@app.get("/api/users")
def list_users():
    conn = get_db()
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return {"users": [dict(r) for r in rows]}

@app.get("/api/me")
def me(username: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else {"error":"not found"}

@app.post("/api/gigs")
def post_gig(req: PostGigReq):
    gid = str(uuid.uuid4())[:8]
    conn = get_db()
    conn.execute("INSERT INTO gigs (id,title,category,reward,urgency,location,instructions,poster,worker,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (gid, req.title, req.category, req.reward, req.urgency, req.location, req.instructions, req.username, None, "OPEN", datetime.now(timezone.utc).isoformat()))
    # Escrow hold from poster
    conn.execute("UPDATE users SET balance = MAX(0, balance - ?), escrow = escrow + ? WHERE username=?",
                 (req.reward, req.reward, req.username))
    conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?, ?,?)",
                 (req.username, "escrow_hold", -req.reward, f"Gig: {req.title}", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return {"ok": True, "gig": {"id":gid,"title":req.title,"status":"OPEN"}}

@app.get("/api/gigs")
def get_gigs():
    conn = get_db()
    rows = conn.execute("SELECT * FROM gigs ORDER BY created_at DESC").fetchall()
    conn.close()
    return {"gigs": [dict(r) for r in rows]}

@app.get("/api/gigs/{gig_id}")
def get_gig(gig_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    conn.close()
    return {"gig": dict(row)} if row else {"error":"not found"}

@app.post("/api/gigs/{gig_id}/accept")
def accept_gig(gig_id: str, req: AcceptReq):
    conn = get_db()
    gig = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    if not gig or gig["status"] != "OPEN":
        conn.close(); return {"error":"not available"}
    conn.execute("UPDATE gigs SET status='IN_PROGRESS', worker=? WHERE id=?", (req.username, gig_id))
    poster = gig["poster"]
    worker = req.username
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
def send_dm(req: MsgReq):
    conn = get_db()
    conn.execute("INSERT INTO messages (from_user,to_user,text,context,time) VALUES (?,?,?,?,?)",
                 (req.from_user, req.to_user, req.text, req.context or "", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/dm")
def get_dm(me: str, with_user: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE ((from_user=? AND to_user=?) OR (from_user=? AND to_user=?)) ORDER BY time ASC",
        (me, with_user, with_user, me)).fetchall()
    conn.close()
    return {"messages": [dict(r) for r in rows]}

@app.get("/api/dm_list")
def dm_list(me: str):
    conn = get_db()
    # Get unique partners for this user
    rows = conn.execute(
        "SELECT DISTINCT CASE WHEN from_user=? THEN to_user ELSE from_user END as partner FROM messages WHERE from_user=? OR to_user=?",
        (me, me, me)).fetchall()
    partners = [r["partner"] for r in rows if r["partner"] and r["partner"]!=me]
    result = []
    for p in partners:
        name_row = conn.execute("SELECT name FROM users WHERE username=?", (p,)).fetchone()
        last = conn.execute("SELECT text FROM messages WHERE (from_user=? AND to_user=?) OR (from_user=? AND to_user=?) ORDER BY time DESC LIMIT 1",
                            (me, p, p, me)).fetchone()
        result.append({"with": p, "name": name_row["name"] if name_row else p, "last": last["text"] if last else ""})
    conn.close()
    return {"conversations": result}

@app.post("/api/wallet/topup")
def topup(req: TopUpReq):
    conn = get_db()
    conn.execute("UPDATE users SET balance = balance + ? WHERE username=?", (req.amount, req.username))
    conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?, ?,?)",
                 (req.username, "topup", req.amount, "UPI / Wallet top-up", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT balance FROM users WHERE username=?", (req.username,)).fetchone()
    conn.close()
    return {"ok": True, "balance": row["balance"] if row else 0}

@app.post("/api/wallet/upi_pay")
def upi_pay(req: UpiPayReq):
    # Mock UPI verification (4-digit PIN)
    if req.pin != "1234":
        return {"error":"Invalid UPI PIN. Try 1234 for demo."}
    conn = get_db()
    # Deduct from wallet / hold
    conn.execute("UPDATE users SET balance = MAX(0, balance - ?), escrow = escrow + ? WHERE username=?",
                 (req.amount, req.amount, req.username))
    conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?, ?,?)",
                 (req.username, "upi_pay", -req.amount, f"UPI Payment Lock — Demo PIN verified", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return {"ok": True, "message":"UPI PIN verified. Funds locked in escrow."}

@app.post("/api/gigs/{gig_id}/complete")
def complete_gig(gig_id: str, req: AcceptReq):
    conn = get_db()
    gig = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    if not gig: conn.close(); return {"error":"not found"}
    conn.execute("UPDATE gigs SET status='COMPLETED' WHERE id=?", (gig_id,))
    poster = gig["poster"]
    worker = gig["worker"] or req.username
    if poster and worker:
        payout = float(gig["reward"]) * 0.97
        conn.execute("UPDATE users SET escrow = MAX(0, escrow - ?), pending_payout = MAX(0, pending_payout - ?) WHERE username=?",
                     (float(gig["reward"]), payout, poster))
        # Give worker payout
        conn.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE username=?",
                     (payout, payout, worker))
        conn.execute("INSERT INTO transactions (username,type,amount,note,time) VALUES (?,?,?, ?,?)",
                     (worker, "payout", payout, f"EOD Settlement — Gig {gig_id}", datetime.now(timezone.utc).isoformat()))
        # Auto message
        conn.execute("INSERT INTO messages (from_user,to_user,text,context,time) VALUES (?,?,?,?,?)",
                     ("system", poster, f"Gig '{gig['title']}' completed. Worker has been paid ₹{payout:.2f} (97%).", "", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    updated = conn.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone()
    conn.close()
    return {"ok": True, "gig": dict(updated)}

# --- OTP Mock System (replace with Twilio/Msg91 for real SMS) ---
import random, time
otp_store = {}

from pydantic import BaseModel
class OtpSendReq(BaseModel):
    phone: str

class OtpVerifyReq(BaseModel):
    phone: str
    otp: str

@app.post("/api/otp/send")
def otp_send(req: OtpSendReq):
    phone = req.phone.strip()
    if not phone.startswith("+"):
        phone = "+91" + phone.replace("+91","").replace(" ","")
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
    # Verify success — allow registration
    return {"ok": True, "phone": phone, "message": "Verified. Proceed to profile."}
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
    GOOGLE_AUTH_AVAILABLE = True
except Exception:
    GOOGLE_AUTH_AVAILABLE = False

class GoogleAuthReq(BaseModel):
    id_token: str

class AuthError(Exception):
    def __init__(self, code, message, status=401):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

def _migrate_users_table():
    """Add Google-auth columns to an existing users table (safe to re-run)."""
    conn = get_db()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for name, ddl in (("email", "TEXT"), ("auth_uid", "TEXT"), ("email_verified", "INTEGER DEFAULT 0")):
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
        if "certificate" in msg.lower() or "transport" in msg.lower() or "connection" in msg.lower():
            raise AuthError("cert_fetch_failed",
                            f"Could not reach Google to verify the token: {msg}", 503)
        raise AuthError("bad_token", f"Could not verify Google token: {msg}", 401)
    if not claims.get("sub"):
        raise AuthError("bad_token", "Token is missing a subject (sub) claim.", 401)
    return claims

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
        "username": username,
        "profile_complete": bool((user.get("major") or "").strip() and (user.get("dorm") or "").strip()),
    }

_migrate_users_table()  # runs after the helper above is defined

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn, os
    port = int(os.environ.get("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)

