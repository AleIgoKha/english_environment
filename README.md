# Word by word

A small, private English flashcard app with PIN login, responsive purple layout, word management, archive, persistent study sessions, and repetition settings. Production setup uses PostgreSQL, Gunicorn, WhiteNoise, and Railway's native GitHub deployment. See [DEPLOYMENT.md](DEPLOYMENT.md) for setup, security, verification, recovery, and iPhone Home Screen installation.

## Local setup (Windows PowerShell)

Prerequisite: Python 3.11 installed through the Python launcher (`py`). Run from this repository. Do not paste secrets into chat or commit them.

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/setup_local.py
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

The setup script privately prompts for a four-digit PIN and writes only its salted hash and a random Django secret into `.env`. Existing `.env` files are never overwritten. `.env` and the SQLite database are ignored by Git. Open http://127.0.0.1:8000/ and enter your chosen PIN. No activation of the virtual environment is required with these commands.

## Verify this checkpoint

Prerequisite: the virtual environment and dependencies above. Tests use disposable settings and an in-memory SQLite database; they do not need your `.env` or PIN.

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Manually verify that `/` redirects to login, the correct PIN opens the home screen, refresh remembers your login, and Log out returns to login. Five wrong PIN submissions trigger a 15-minute lockout even with a new browser session. The lockout is global for this single-user app, so an outsider can temporarily block your own login too. A four-digit PIN has limited guessing resistance even with these controls.

Check the layout at phone and desktop widths. Below 900px, pages use a compact single column with touch-friendly controls and iPhone safe-area padding. On desktop, the home page places practice controls beside the word list, and login places the welcome text beside the PIN form. A web app manifest and 180/192/512px icons support Home Screen installation. Real iPhone visual acceptance remains a device-side check.

### Word management

After updating an existing local installation, run the following with the existing virtual environment and `.env` configured. This adds a word table without changing your PIN or login data:

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
.\.venv\Scripts\python.exe manage.py migrate
```

Refresh the home page and choose **Add word**. Only Chunk is required; Meaning and Example can be blank. Limits are 300 characters for Chunk, 2,000 for Meaning, and 4,000 for Example. Word lists show the saved content as escaped text. Use Edit to update a word or open its deletion confirmation; nothing is deleted until you confirm. Cancel keeps the saved word unchanged.

New words start at stage zero and are due immediately. Editing changes content only, preserving learning state. The due count reflects new and due active words.

**Archive** contains only learned words. Words move there automatically on a successful review when they reach the target in Settings. Restoring a word resets it to stage zero and makes it due immediately. A repeated restore request cannot reset an already-active word. Archived words can also be edited or deleted.

For this checkpoint, manually verify adding a Chunk-only word, adding all three fields, editing and clearing optional fields, cancelling a deletion, and confirming deletion of a test word. Archive filtering/restoration are covered by automated tests using disposable learned words; no sample words are inserted into your actual database. Desktop and iPhone visual review remains a manual check.

### Study sessions and settings

For an existing installation, run `manage.py migrate` from the repository with your configured virtual environment, as shown above, then restart the development server. The migration adds learning settings (default target: five) and session storage; existing words, PIN configuration, and login sessions are preserved.

- **Start** builds a queue of new and due words, oldest due first. **Resume** continues the existing queue. Newly added words join your next session, rather than changing an ongoing one.
- A card shows Chunk first. **Reveal answer** shows Meaning and Example, followed by **Know**, **Repeat**, and **Don't know**. Chunk-only cards can also be reviewed.
- **Know** advances a word one stage when it has had no setback this session, then removes it from today's queue. With the default target of five, gaps are 1, 3, 7, and 14 calendar days; the fifth success archives it.
- **Repeat** applies a one-stage setback; **Don't know** applies two. Both reference the stage saved at session start. Repeating either action cannot stack setbacks, and combining them produces at most two stages total. Stage zero is the minimum.
- Either setback puts the word after three other answers, or at the end of a shorter queue. A later **Know** finishes practice without recovering a stage. The next review is scheduled from the reduced stage; stage zero retries are due tomorrow.
- Review dates fall at midnight in Europe/Chisinau; timestamps are stored in UTC. The additional gaps for larger targets are 30, 60, 120, 240, and 365 days. This is a simple fixed stage schedule, not a personalized retention estimate.
- **Settings** accepts a target of 1–10. Changes apply to subsequent answers, including those in an ongoing session. They do not immediately archive existing words, rewrite their due dates, or restore archived words.
- **Pause and return home**, closing the browser, logging out, or restarting the server leaves the study queue and setbacks saved. Login and Resume pick up the same session. The queue closes when every remaining word is finished or deleted.

Session changes and word progress are saved in one database transaction. A per-card response token rejects duplicate and stale submissions, including double taps when only one word is left. An answer cannot be recorded before revealing the card. A shared database lock and a uniqueness constraint prevent simultaneous Start requests from creating separate open sessions. Functional tests pass on PostgreSQL, including real concurrent Start and duplicate-answer requests.

To review this checkpoint manually, add a test word, Start, reveal it, pause, and Resume. Try Repeat several times, then Know; it should remain active and be due on a later day. For a quick archive check, set the target to 1, add another test word, and answer Know; it should move to Archive. Restore it to the deck, and set your preferred target again. These actions change only words you choose to practice; tests never seed your real deck.

## Configuration and security

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Random secret, mandatory; never commit it. |
| `APP_PIN_HASH` | Django password hash of the four-digit PIN. Missing/invalid configuration disables login. |
| `DJANGO_DEBUG` | `true` only for local development; defaults to `false`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts without schemes. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated HTTPS origins, where needed for deployment. |
| `DATABASE_URL` | PostgreSQL connection URL, mandatory when debug is disabled. Railway uses a reference to the dedicated database service. |

Production defaults enforce HTTPS and secure cookies, trust Railway's forwarded HTTPS header, require PostgreSQL, and serve collected static assets through WhiteNoise. Railway holds production secrets in environment variables; the local `.env` is excluded from the Docker image and Git. The public `/health/` readiness endpoint returns only a generic status, with no private content.

PIN verification is server-side. Sessions last 30 days; logging out deletes the current session, and changing `APP_PIN_HASH` invalidates existing logins. Failed attempts and lockout expiry live in the database. PIN fields are excluded from Django error reports, and submitted PINs are not logged or echoed in responses. Protected responses use `Cache-Control: no-store`.

To change the local PIN, stop the development server, delete only the ignored `.env` file, and rerun `scripts/setup_local.py`. This also rotates the local Django secret and invalidates local sessions; words in the database will be preserved.

The project uses Django templates (HTML rendered on the server), SQLite for local development, and PostgreSQL in production. No frontend build system is needed. Publication and live verification status are recorded in [DEPLOYMENT.md](DEPLOYMENT.md).
