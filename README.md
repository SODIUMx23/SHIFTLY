# Shiftly — Indian Campus Gig Marketplace & UPI Wallet

A real campus gig marketplace: students post errands and micro-jobs with
escrow-protected bounties, other students take them, chat in-app, and the
worker is paid to their UPI wallet the moment the poster marks it done.

## Stack
- **Backend:** Python FastAPI + SQLite (single-file `main.py`)
- **Frontend:** single-page app in `static/index.html` (Tailwind CDN, no build step)
- **Auth:** Google (Firebase, free) + mobile-OTP session system
- **Payments:** Razorpay Orders API (HMAC signature-verified, demo mode without keys)
- **Deploy:** one Render web service from this repo (`render.yaml` included)

## Features
- Google sign-in (popup → redirect fallback) or mobile-number login with OTP
- Profiles with photo upload, course/hostel, UPI ID, live rating
- Gig feed with category art, search/filter, detail view, auto-opened DMs
- Escrow lifecycle: lock on post → accept → complete → instant 97% payout
  (3% platform fee), cancel-before-accept → instant full refund
- Ratings (each participant rates once, only after completion)
- Wallet: real UPI top-up via Razorpay **when keys are configured**
  (`rzp_test_` keys are free and work in minutes; demo "play money" button
  available for offline testing)
- Transaction ledger with full audit trail per user
- Privacy / Terms / Contact pages included (Razorpay KYC checklist ✓)

## Run locally
```bash
cp .env.example .env        # then paste your FIREBASE_API_KEY (see SETUP-GOOGLE-LOGIN.md)
pip install -r requirements.txt
python check_config.py      # preflight — verifies the config the server will read
python main.py              # → http://localhost:8081
```

Sign in with any Google account, or use mobile-number sign-in (the demo OTP
is shown on screen).

## Money modes
| Mode | How | Money |
|---|---|---|
| **Demo wallet** | Default — no keys needed | Play money, instant |
| **Razorpay test** | Free test keys from [dashboard.razorpay.com/app/keys](https://dashboard.razorpay.com/app/keys) → set `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | No real money; use UPI `success@razorpay` or card 4111 1111 1111 1111 |
| **Razorpay live** | Same keys, `rzp_live_` pair after KYC | Real UPI payments |

Payment flow is signature-verified and idempotent: the server creates the
order, the browser pays Razorpay directly, the server re-computes the HMAC
before crediting, and `UNIQUE(payment_id)` guarantees once-only crediting.

## Deploy options
- **No credit card:** Back4App Containers (free tier) — connect the GitHub
  repo, it auto-detects the included `Dockerfile`, paste the `FIREBASE_*` +
  `SESSION_SECRET` env vars, deploy. Then add the `*.b4a.run` hostname to
  Firebase → Authentication → Authorized domains.
- **Render** (card required for new accounts): Render dashboard →
  **New → Blueprint** → pick the repo (reads `render.yaml`), paste the
  four `FIREBASE_*` values, deploy, then whitelist the `onrender.com`
  hostname in Firebase as above. Optional paid disk: uncomment the
  `disk:`/`DB_PATH` pair in `render.yaml` for persistence.
SQLite resets on restarts/redeploy under every free plan; paid hosts let you
mount a disk (set `DB_PATH` to it — the app persists automatically).

## Tests
```bash
pip install -r requirements-dev.txt
pytest tests/ -q        # 18 tests: auth forgery, money flows, photos, payments gating
```

## Roadmap (not shipped, intentionally)
- Real SMS OTP — needs an SMS provider + DLT registration (paid). Mobile
  login today uses an on-screen demo code; swap the sendOtp call in `main.py`
  for MSG91/Twilio when ready.
- Razorpay Route payouts to workers' bank accounts (needs live KYC).
