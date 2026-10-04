# Adding free Google (Gmail) verification login to Shiftly

**What this gives you:** a working *Continue with Google* button. Users sign in with any
Google account; the server independently verifies Google's signed token, checks the email
is verified with Google, then creates or links the local account. No passwords, no SMS
cost, no service account, no card required.

**Cost:** free. Google/social sign-in is free on Firebase's no-cost Spark plan — up to
50,000 monthly active users, with a 3,000-daily-active-user cap on Spark
([pricing](https://blog.logto.io/firebase-authentication-pricing),
[limits](https://firebase.google.com/docs/auth)). Phone/SMS auth is the one that isn't free.

**What you have to do:** about 5 minutes in the Firebase console (Steps 1–5). I can't
create the project for you — it's tied to your Google account.

---

## Step 1 — Create a Firebase project

1. Go to **https://console.firebase.google.com/**
2. Click **Add project** (or **Create a project**)
3. Name it `shiftly` → **Continue**
4. Google Analytics: **turn it off** (you don't need it) → **Create project**
5. Wait ~30s → **Continue**

## Step 2 — Register a Web app and copy the config

1. On the project home, click the **`</>`** (Web) icon
2. App nickname: `shiftly-web` → **Register app**
3. Firebase shows you a `firebaseConfig` block. **Copy these four values:**

```js
const firebaseConfig = {
  apiKey: "AIzaSy......................",        // → FIREBASE_API_KEY
  authDomain: "shiftly-xxxxx.firebaseapp.com",  // → FIREBASE_AUTH_DOMAIN
  projectId: "shiftly-xxxxx",                   // → FIREBASE_PROJECT_ID
  appId: "1:1234567890:web:abcdef123456"        // → FIREBASE_APP_ID
};
```

> These are **not secrets** — Firebase ships them to every browser by design. The real
> secret would be a service-account key, and this setup deliberately doesn't need one.
> Keep them in env vars anyway so you're not editing HTML to change projects.

## Step 3 — Enable the Google provider

1. Left sidebar → **Build → Authentication** → **Get started**
2. **Sign-in method** tab → click **Google** → toggle **Enable**
3. Pick a **support email** (yours) → **Save**

At this point Firebase has enabled Google sign-in. Without this, users get
`auth/operation-not-allowed`.

## Step 4 — Authorise your domains ⚠️ most-missed step

1. **Authentication → Settings** tab → **Authorized domains**
2. `localhost` is there by default. **Add your deployed domain** — e.g.
   `shiftly.onrender.com` (no `https://`, no trailing slash)
3. If you use a custom domain, add that too.

Skip this and sign-in fails with `auth/unauthorized-domain` the moment you test in
production. It works locally and breaks live — that's the classic version of this mistake.

## Step 5 — Set the environment variables

**Locally** (PowerShell / bash):

```bash
export FIREBASE_PROJECT_ID="shiftly-xxxxx"
export FIREBASE_API_KEY="AIzaSy......................"
export FIREBASE_AUTH_DOMAIN="shiftly-xxxxx.firebaseapp.com"
export FIREBASE_APP_ID="1:1234567890:web:abcdef123456"
python main.py
```

**On Render:** Dashboard → your service → **Environment** → add the same four keys with
their values. (`render.yaml` already declares the keys with `sync: false`, so the
dashboard is where the values belong — never in git.)

Then redeploy.

## Step 6 — Test it

1. Open the app in a **real browser** (not the sandboxed in-app preview — see caveats)
2. Should see **Continue with Google** on the entry screen
3. Click it → pick a Google account → you land on the profile step with your name
   prefilled and *"Google account you@gmail.com verified"*
4. Fill in course/hostel/UPI → **Join Shiftly**
5. Bonus: sign in with the *same* account again → it skips the profile step and logs
   you straight in (account is linked by email, no duplicates)

---

## How the login actually works

```
Browser                     Your server (main.py)              Google
  │                                │                             │
  ├─ signInWithPopup() ────────────┼─────────────────────────────► (account picker)
  │◄──── ID token (JWT, RS256) ────┼─────────────────────────────┤
  ├─ POST /api/auth/google ────────►                             │
  │                          verify_firebase_id_token()          │
  │                                ├─ fetch public certs ────────►
  │                                │◄── JWKS (cached) ───────────┤
  │                          checks: signature, exp, aud=projectId,
  │                                  iss, sub, email_verified
  │                          create/link user by email
  │◄──── {ok, user, profile_complete} ──
```

**Verification is server-side** (`main.py` → `verify_firebase_id_token`), using
`google.oauth2.id_token.verify_firebase_token` against Google's published certificates.
That's why no service-account key is needed — the token carries a verifiable signature, so
the server just needs to know your project ID
([reference](https://stackoverflow.com/questions/42430105/verify-firebase-idtoken-via-python)).

**Enforced checks:**

| Check | Rejects |
|---|---|
| Google's signature on the token | anyone forging a token |
| `aud` == your project ID | tokens minted for someone else's Firebase project |
| `iss` == `https://securetoken.google.com/<your-project>` | tokens from another issuer |
| `exp` / `iat` | expired or not-yet-valid tokens |
| `sub` present | malformed tokens |
| `email` present | Google accounts with no email |
| `email_verified` == true | **unverified emails** ← this is the "Gmail verification" |
| `ALLOWED_EMAIL_DOMAINS` (optional) | off-campus accounts, if you turn it on |

**API errors you can act on:** `not_configured` (503), `bad_token` (401),
`email_unverified` (403), `no_email` (400), `domain_not_allowed` (403),
`cert_fetch_failed` (503 — server couldn't reach Google).

---

## Bugs fixed along the way

The Google button wasn't just misconfigured — it was broken in **five** independent ways:

1. **No button existed.** `signInWithGoogle()` was defined in a script tag but nothing in
   the UI ever called it. Added a real button.
2. **No `firebaseConfig` / `initializeApp()` anywhere.** `firebase.auth()` would throw
   *"No Firebase App '[DEFAULT]' has been created"*. Now initialised from server config.
3. **It crashed before doing anything.** `signInWithGoogle` wrote to `$('reg-username')`,
   but **no element with that id exists** in the HTML — so it threw
   *"Cannot set properties of null"* every single time. Removed that dependency.
4. **The profile form didn't exist as a form.** 5 `<form>` opens vs 6 `</form>` closes —
   the profile section's opening tag was missing and there was a stray `</form>`. The
   *Join Shiftly* button was outside any form, so clicking it did nothing. Rebuilt as a
   proper `step-profile` form.
5. **`upi_id` was being set to the user's email address** (`$('reg-upi').value = email`).
   An email is not a UPI ID and would break payouts. Now left blank for the user to fill.

Verified: 11 backend tests (account creation, linking, dedupe, `email_verified`
enforcement, domain rules, migration idempotency) and 26 frontend tests in a real DOM
(button wiring, step navigation, new-user vs returning-user paths, sign-out). All pass.

---

## Honest limits — read before you demo this as "secure"

Everything above is real, but be precise about what it does and doesn't cover:

- ✅ **Who a user is** is now genuinely verified by Google.
- ❌ **What a user may do is still unchecked.** Every other endpoint still trusts a
  `username` from the request body — `POST /api/wallet/topup`, `/api/gigs`, `/api/dm` will
  happily act on behalf of any username you name. Signing in with Google doesn't change
  that. Anyone can still call `POST /api/wallet/topup` with `{"username":"priya"}` and
  curl, with no token at all.

Closing that is the next step: send the stored ID token as
`Authorization: Bearer <token>`, verify it server-side on each request, and use the
verified `sub`/email to authorise — instead of trusting the body. The frontend already
stores the token (`localStorage.cp_id_token`) for exactly this.

- The ID token is stored in `localStorage` and **expires after 1 hour**. Nothing refreshes
  it yet.
- A returning visitor with `cp_user` in `localStorage` skips re-verification on page load.
  Fine for a prototype; pair it with the server-side check above before real use.
- "Free Gmail" here means *any* Google account, per your choice. To restrict to campus
  emails set `ALLOWED_EMAIL_DOMAINS="vitstudent.ac.in,vit.ac.in"` — no code change needed.
- The wallet/UPI flow is still a simulation: `upi_pay` accepts any 4-digit PIN by design
  (`# mock verification`). No real money moves.

---

## Caveats about testing in this workspace

- The in-app preview runs in a sandboxed iframe **with no network access**, so the
  Firebase SDK and Tailwind CDN can't load there. The button will show
  *"Firebase SDK could not load"* — expected. **Test in a real browser.**
- The page also degrades gracefully when the env vars are missing: you get
  *"Google sign-in is not switched on yet…"* instead of a crash, and mobile sign-in still
  works.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `auth/unauthorized-domain` | Domain not added in Firebase → Authentication → Settings → Authorized domains (Step 4) |
| `auth/operation-not-allowed` | Google provider not enabled (Step 3) |
| `auth/popup-blocked` | Browser blocked the popup — allow popups for the site |
| `auth/popup-closed-by-user` | User dismissed the picker; shown as "Sign-in cancelled" |
| Notice says "not switched on yet" | `FIREBASE_*` env vars missing — restart the server after exporting them |
| `not_configured` (503) | Same thing, seen from the API side |
| `email_unverified` (403) | The Google account's email isn't verified with Google |
| `bad_token` (401) | Token expired (>1h) or `FIREBASE_PROJECT_ID` doesn't match the project that minted it |
| `cert_fetch_failed` (503) | Server can't reach `googleapis.com` to fetch signing certs — check outbound network |
| Button does nothing | Open the browser console; if the SDK didn't load, you're offline or behind a blocker |
