#!/usr/bin/env python3
"""Check the Firebase config before you waste time debugging a typo.

    python check_config.py

Validates the shape of each value (the mistakes copy-paste actually causes:
stray quotes, a trailing slash, an https:// prefix, a wrong project id) and
confirms the server would turn Google sign-in on. Does not contact Google.
"""
import os
import re
import sys

os.environ.pop("FIREBASE_PROJECT_ID", None)  # let .env win for this check

# reuse the app's own loader so we test exactly what the server reads
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (  # noqa: E402
    FIREBASE_PROJECT_ID, FIREBASE_API_KEY, FIREBASE_AUTH_DOMAIN,
    FIREBASE_APP_ID, ALLOWED_EMAIL_DOMAINS, GOOGLE_AUTH_AVAILABLE,
)

ok, warn, bad = [], [], []


def check(label, value, required=True, pattern=None, hint=""):
    if not value:
        (bad if required else warn).append(f"{label}: empty{(' — ' + hint) if hint else ''}")
        return
    if pattern and not re.fullmatch(pattern, value):
        bad.append(f"{label}: looks malformed -> {value!r}. {hint}")
        return
    ok.append(f"{label}: {value}")


check("FIREBASE_PROJECT_ID", FIREBASE_PROJECT_ID, pattern=r"[a-z0-9][a-z0-9-]{2,62}",
      hint="lowercase letters, digits and dashes only")
check("FIREBASE_API_KEY", FIREBASE_API_KEY, pattern=r"AIza[0-9A-Za-z_\-]{35}",
      hint="a Google API key is 39 chars and starts with 'AIza'")
check("FIREBASE_AUTH_DOMAIN", FIREBASE_AUTH_DOMAIN, pattern=r"[a-z0-9\-]+\.firebaseapp\.com",
      hint="no https:// and no trailing slash")
check("FIREBASE_APP_ID", FIREBASE_APP_ID, pattern=r"1:\d{6,}:web:[0-9a-f]+",
      hint="looks like 1:1234567890:web:abc123")

# cross-checks between fields
if FIREBASE_PROJECT_ID and FIREBASE_AUTH_DOMAIN:
    if FIREBASE_AUTH_DOMAIN != f"{FIREBASE_PROJECT_ID}.firebaseapp.com":
        warn.append(f"authDomain ({FIREBASE_AUTH_DOMAIN}) does not match "
                    f"projectId ({FIREBASE_PROJECT_ID}) — make sure both come from the "
                    "same Firebase project")
if FIREBASE_APP_ID and FIREBASE_APP_ID.startswith("1:"):
    sender = FIREBASE_APP_ID.split(":")[1]
    ok.append(f"appId project number: {sender} (should equal messagingSenderId in the console)")

# the server only switches the button on when all three are present
enabled = bool(FIREBASE_PROJECT_ID and FIREBASE_API_KEY and GOOGLE_AUTH_AVAILABLE)

print("\n".join(f"  ✓ {line}" for line in ok) or "  (nothing valid found)")
for line in warn:
    print(f"  ! {line}")
for line in bad:
    print(f"  ✗ {line}")

print()
print(f"  google-auth installed : {GOOGLE_AUTH_AVAILABLE}")
print(f"  allowed domains       : {ALLOWED_EMAIL_DOMAINS or 'any Google account'}")
print(f"  /api/auth/config says : google_sign_in={enabled}")

if bad:
    print("\nRESULT: fix the ✗ items above, then re-run.\n")
    sys.exit(1)
print("\nRESULT: looks good — start the server and test in a real browser.\n")
print("Still to do in the Firebase console if you haven't:")
print("  1. Authentication -> Sign-in method -> Google -> Enable")
print("  2. Authentication -> Settings -> Authorized domains -> add your domain")
