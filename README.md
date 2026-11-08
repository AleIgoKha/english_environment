# Cardy

A private English flashcard app with owner-only Google sign-in, a purple desktop and iPhone layout, word management, progress filters, and persistent spaced-repetition sessions. Django renders the pages; SQLite supports local development and PostgreSQL stores production data on Railway.

## Local setup (Windows PowerShell)

Prerequisite: Python 3.11 installed through the Python launcher (`py`). Run from this repository. Never paste secrets into chat or commit them.

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/setup_local.py
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

The setup script creates an ignored `.env` with a random Django secret and enables **Continue as test user**, matching Budget's local testing flow. Existing `.env` files are never overwritten. Google credentials are optional for local test access.

Open the URL saved in `LOCAL_DEV_URL` in your ignored `.env` and choose **Continue as test user**. For desktop and iPhone testing on the same Wi-Fi, use `LOCAL_DEV_ADDRESS=0.0.0.0:8000`, save the computer's Wi-Fi URL in `LOCAL_DEV_URL`, add its IP to `DJANGO_ALLOWED_HOSTS`, and set `LOCAL_TEST_NETWORK` to the private Wi-Fi subnet. The wildcard address is only for binding; open the computer's actual Wi-Fi address on the phone. Keep the computer running. Its IP may change unless reserved in the router.

Test mode uses a separate `db.local-test.sqlite3` deck; the original `db.sqlite3` is preserved. All local databases and `.env` are ignored by Git and excluded from the deployment image. Test sign-in requires debug mode, explicit `DJANGO_LOCAL_TEST_LOGIN=true`, a local database, a loopback or configured private Wi-Fi connection, and no Railway environment. Anyone on that configured subnet can use the local test deck while enabled. Disabling test access revokes existing test sessions.

To inspect the original local deck, set `DJANGO_LOCAL_TEST_LOGIN=false`, configure Google sign-in, run migrations, and restart the server. When test mode is enabled, Google login also uses the test database.

## Google sign-in setup

Create a **Web application** OAuth client in Google Cloud Console → Google Auth Platform → Clients. Configure the app audience; in Testing mode, add your permitted Google email as a test user. The app requests only `openid email`.

Add these exact **Authorized redirect URIs**, including trailing slashes. These belong under redirect URIs, not JavaScript origins:

- `http://127.0.0.1:8000/auth/google/callback/`
- `https://english-environment-production.up.railway.app/auth/google/callback/`

Store `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `ALLOWED_GOOGLE_EMAIL` privately in `.env` for local Google testing and in Railway variables before publishing this update. Only the configured verified email can access the shared deck. Never paste client secrets into chat. See [Google's OpenID Connect guide](https://developers.google.com/identity/openid-connect/openid-connect).

Configure the Google variables before publishing. Production Google access is restricted to the configured owner, and local test access is always disabled on Railway.

## Learning rules

- Chunk is required; Meaning and Example are optional. Open My cards to search or filter by All, To learn, Practiced, or Learned. The three-dot menu offers Reset, Edit, Select, and Delete. Reset returns a word to To learn, due immediately; Reset and Delete ask for confirmation. Select enables checkboxes and bulk Reset / Delete with confirmation.
- Start opens one ongoing session. New and due words are appended to the end; an empty queue waits without ending the session or reviewing future cards early. The study screen checks for arrivals every five seconds while visible, and progress resumes after reopening.
- Reveal the answer, then choose Know, Repeat, or Don't know.
- Know advances a stage unless there was an earlier setback in the current review round. The default fifth success marks a word Learned.
- Repeat reduces the stage at the start of the current review round by one; Don't know reduces it by two. Setbacks use the largest reduction, never stack, and never go below zero.
- A setback moves the card to the very end of the queue. A later Know finishes without advancing the reduced stage.
- Review gaps are 1, 3, 7, 14, 30, 60, 120, 240, and 365 days. Stage zero retries are due tomorrow. Calendar days use Europe/Chisinau; timestamps are stored in UTC.
- Undo below the card reverses answers one at a time, including the last answer while waiting for new cards. The previous card returns with its answer hidden, with its exact learning state and queue restored. Reveal the answer again before choosing a response. Undo history survives reopening the app and stays available in the ongoing session. Cards that arrived later remain at the end when undoing. Resetting or deleting cards clears the history so Undo cannot overwrite those deck changes. Only answers made after the Undo update are available to undo.
- Settings allow 1–10 successes. Changes affect subsequent review decisions without immediately changing existing review dates or Learned status.
- To learn counts active words due now or still queued for practice; Practiced counts active words waiting for their future review date; Learned counts completed words. The list filters and each card status use those same readiness rules. Reset restarts progress without removing the saved text. Responses are atomic and duplicate answer taps cannot apply twice. Old archive bookmarks redirect to the Learned filter.

## Import words from CSV

Open **My cards**, then tap the round upload icon beside Search. Choose a UTF-8 `.csv`, review the preview, then confirm the import. Uploading alone does not add words.

Supported headers are `Chunk,Meaning,Example` or DuoCards' `front,back,hint,publishedAt`. Only Chunk/front is required; published dates are ignored. Quoted commas and multiline examples are supported. Use no more than 1,000 rows or 2 MB per upload.

New words start in **To learn**, due immediately. Every CSV row is imported, including duplicates within the file or matching words already in the deck. Existing text and learning progress are preserved. Reconfirming the same preview does not import it twice.

Previews are private to the authenticated browser session, expire after 24 hours, and store parsed text in the database rather than an uploaded file. Parsed preview rows are cleared after import, and expired previews are cleaned up on the next valid upload. Repeated confirmation cannot add a batch twice. No private CSV exports belong in Git.

## Configuration and security

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Random secret; mandatory, never commit it. |
| `GOOGLE_CLIENT_ID` | Google Web application OAuth client ID. |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret. |
| `ALLOWED_GOOGLE_EMAIL` | Permitted verified owner email. |
| `DJANGO_DEBUG` | `true` locally only; defaults to `false`. |
| `DJANGO_LOCAL_TEST_LOGIN` | Explicit local test access; always `false` on Railway. |
| `LOCAL_DEV_ADDRESS` | Local server address; defaults to `127.0.0.1:8000`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames without schemes. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated HTTPS origins where needed. |
| `DATABASE_URL` | PostgreSQL URL; mandatory in production. |

Authlib implements OpenID Connect with authorization codes, PKCE, state and nonce validation. Google tokens are verified and discarded. Sessions last 30 days. Logout clears the current session; changing Google credentials or the allowed email revokes remembered access. Old PIN sessions no longer authenticate. Protected responses use `Cache-Control: no-store`.

Production requires HTTPS, CSRF protection, secure cookies, debug disabled, and PostgreSQL. Secrets belong in Railway variables. Callback query strings are excluded from production access logs and redacted in Django development logs. `/health/` returns only a generic status.

## Verification

Prerequisites: installed virtual environment and dependencies. Tests use disposable credentials and databases.

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\.venv\Scripts\python.exe scripts/check_production.py
```

For PostgreSQL verification, install PostgreSQL command-line tools on PATH, then run from this repository:

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
.\scripts\test_postgres.ps1
```

The script creates a disposable local cluster, runs the suite, and stops it. It never uses Railway's database.

Manually check login, Settings → Logout, word editing, study resumption, and the desktop layout. On iPhone, open the HTTPS app in Safari → Share → Add to Home Screen. Confirm standalone display, safe areas and remembered Google login on the device. Version one requires internet access.

See [DEPLOYMENT.md](DEPLOYMENT.md) for GitHub-to-Railway setup, migrations, static assets, recovery, and the last verified production state.
