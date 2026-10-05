# Shiftly — Indian Campus Gig Marketplace & UPI Wallet

Real-time student gig marketplace with direct peer-to-peer wallet, Google Auth, and Indian campus context.

## Stack
- Python FastAPI + SQLite
- Tailwind CSS (light Indian-theme UI)
- Firebase Google Auth (free tier)
- Render / Oracle Cloud deploy ready

## Quick Start
```bash
pip install -r requirements.txt
python main.py
```

## Features
- Post campus gigs with UPI escrow hold (3% host fee / 97% worker)
- Auto-DM when taking a gig
- Wallet + UPI wallet top-up (mock PIN demo)
- End-of-Day settlement simulation
- Multi-device real-time sync

## Tests

```
pip install -r requirements-dev.txt
pytest tests/ -q
```

Replays every attack the app has ever survived — forged sessions, tampered
tokens, cross-user money moves, negative/huge rewards, insolvent escrow — and
the honest post → accept → complete → 97%-payout flow they must not break.
11 tests covering auth, identity, economy, cancel/refund and ratings.

## Deploy (Render — free tier)
Connect GitHub repo to Render → Build `pip install -r requirements.txt` → Start `python main.py`
