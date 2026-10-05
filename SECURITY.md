# Shiftly sessions & API security

As of this commit, **every endpoint that reads or writes personal data requires a
signed session token**. The identity of the caller always comes from that token —
never from a `username` (or `me`, `from_user`) field in the request body, which was
the vulnerability: anyone could move anyone's money with a single `curl`.

## How it works

```
                 ┌──────────────┐        ┌─────────────────────┐
 Google sign-in  │ /api/auth/   │ verify │  full session       │
 (Firebase)  ───▶│ google       │ ─────▶ │  { u: username }    │
                 └──────────────┘        └─────────────────────┘

 OTP demo login  │ /api/otp/    │ correct│  pending session    │
 (mock SMS)  ───▶│ verify       │ ─────▶ │  { phone, pending } │
                 └──────────────┘        │  30 min expiry      │
                          │              └──────────┬──────────┘
                          │              /api/register exchange
                          ▼                         ▼
                      full session  ──▶  24 h, proves WHO is calling
```

- Tokens are HMAC-SHA256 signed (`payload.signature`), verified with constant-time
  comparison. Forgery requires `SESSION_SECRET`.
- Pending tokens (OTP verified, no profile yet) can only call `/api/register` —
  money and message endpoints reject them with 401 `registration_pending`.
- OTP codes are single-use and expire in 5 minutes.
- Both flows exchange to the same session format, which also fixes the old
  "Google ID token expires after 1 h with no refresh" problem.

## What changed, endpoint by endpoint

| Endpoint | Before (anyone, no proof) | Now |
|---|---|---|
| `POST /api/register` | claim any username in the body | needs a session; identity from verified phone or existing session |
| `GET /api/me` | `?username=<anyone>` | your own row only |
| `GET /api/users` | every row incl. balances/UPI/email | signed-in only; only public profile fields |
| `POST /api/gigs` | post/escrow as anyone | poster = session user |
| `POST /api/gigs/{id}/accept` | accept as anyone | session user; can't accept your own gig (403) |
| `POST /api/gigs/{id}/complete` | **anyone could release escrow** | only the gig's poster (403) — payments |
| `POST/GET /api/dm`, `/api/dm_list` | read/spoof anyone's DMs | sender/reader = session user |
| `POST /api/wallet/topup` | credit **any** account | your own account only (still mock money!) |
| `POST /api/wallet/upi_pay` | drain **any** account | your own account only |

Public on purpose: `GET /api/gigs`, `GET /api/gigs/{id}` (browsing),
`/api/otp/send`, `/api/otp/verify`, `/api/auth/config`, `/api/auth/google`,
`/api/health`.

## Configuration

| Var | Required | Notes |
|---|---|---|
| `SESSION_SECRET` | **production** | 64 hex chars. Generate: `python -c "import secrets; print(secrets.token_hex(32))"`. Render sets it automatically (`generateValue: true` in render.yaml). If unset, a random secret is made at boot and all sessions die on restart. |
| `SESSION_HOURS` | no | Default 24. |

## Still mock (next roadmap level — NOT real money yet)

- `/api/wallet/topup` still *creates* balance from nothing — it's a simulator for
  the (future) Razorpay checkout, not a payment gateway. Even signed-in, so treat
  balances as play money until that level is built.
- `/api/wallet/upi_pay` verifies a demo PIN of `1234`.
- OTP is returned in the API response instead of being sent by SMS.
