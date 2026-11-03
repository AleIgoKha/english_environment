# Railway deployment

## Destination and data boundary

- GitHub: `AleIgoKha/english_environment`, branch `main`.
- Railway project: `My Tools` (`01debade-17e6-4ad3-a9dd-ddbd14373e24`).
- Environment: `production` (`550023b3-036b-4e9d-aca5-aa1c32478ac4`).
- App: `english-environment` (`8e816988-cec0-4d63-89d5-ecd745a0ba81`).
- Public URL: https://english-environment-production.up.railway.app
- Dedicated database: `Postgres` (`a5cc38b7-366f-4da1-bc0d-0e83274d7645`). It is separate from `budget-db`.
- Start production with an empty deck. Never upload `.env`, SQLite databases, private exports, local PostgreSQL test clusters, or credentials to GitHub or a Docker build.

## How deployments work

Railway's native GitHub source tracks `main`. The repository's Dockerfile installs pinned dependencies and runs as an unprivileged user. The service's deployment settings run migrations before release and require `/health/` to return 200. At startup, `scripts/start.py` checks production security settings, collects static assets, and launches Gunicorn. WhiteNoise serves CSS, the manifest, and icons from that container. Python startup avoids Windows CRLF shell-script failures.

Railway no longer applies legacy `railway.json` to new services. The small, non-secret profile `scripts/railway_service.json` records our build/deploy settings. `scripts/apply_railway_config.py` uses the existing Railway CLI login with Railway's public API, verifies the service belongs to the selected project, applies the profile, and reads it back to verify it persisted. Pass exact project, environment, and service IDs. Reapply only when that profile changes; normal code pushes use the native GitHub deployment connection. This avoids adding a TypeScript infrastructure toolchain or managing unrelated services in My Tools.

The API's `RAILPACK` builder automatically detects the Dockerfile; `DOCKERFILE` is not a valid public-API builder enum. The native repository source and push trigger are separate: both must exist. The helper can ensure the exact repo/branch trigger without duplicating it. GitHub's Railway app must be allowed to access this repository, independently of the OpenAI/Codex GitHub app.

Prerequisites: Railway CLI already authenticated, this project's virtual environment installed, and Railway's GitHub app granted access to this repository. Reapply the service profile from PowerShell with:

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
.\.venv\Scripts\python.exe scripts/apply_railway_config.py --project 01debade-17e6-4ad3-a9dd-ddbd14373e24 --environment 550023b3-036b-4e9d-aca5-aa1c32478ac4 --service 8e816988-cec0-4d63-89d5-ecd745a0ba81 --repository AleIgoKha/english_environment --branch main
```

The app requires PostgreSQL when debug is disabled: it will not silently create a disposable SQLite database in production. Database storage belongs to the dedicated PostgreSQL service, so replacing the app container does not replace saved words. Migrations create missing tables and seed singleton settings; they do not erase data on later deployments.

## Secrets and configuration

Store all values in the app service's Railway environment variables:

| Variable | Value or source |
| --- | --- |
| `DATABASE_URL` | Reference `${{Postgres.DATABASE_URL}}`, using Railway's private network. |
| `DJANGO_SECRET_KEY` | A fresh, random production secret. |
| `APP_PIN_HASH` | The existing local PIN's Django PBKDF2 hash. Never a plaintext PIN. |
| `DJANGO_DEBUG` | `false` |
| `DJANGO_ALLOWED_HOSTS` | `healthcheck.railway.app`; Railway's generated public domain is also added automatically. |
| `PORT` | `8000` |

`RAILWAY_PUBLIC_DOMAIN` and `RAILWAY_ENVIRONMENT_ID` are supplied by Railway. The app derives its public allowed host and trusted HTTPS origin from the domain. For a custom domain, add its hostname to `DJANGO_ALLOWED_HOSTS` and its full HTTPS origin to `DJANGO_CSRF_TRUSTED_ORIGINS`.

`scripts/configure_railway.py` sends variables through CLI stdin, hides captured command output, copies only the PIN hash from the ignored local `.env`, and generates a separate production Django secret. It never reads or transfers local word data. Run it only for initial setup: rerunning it rotates the production Django secret and logs out existing devices.

To change the PIN later, generate a Django PBKDF2 hash privately and replace only `APP_PIN_HASH` in Railway. Redeploy to apply it. Existing remembered logins are invalidated automatically; words and saved study sessions remain. Never paste secret values into chat, issue bodies, or Git commands.

## Verification

Prerequisites: Python 3.11, installed project `.venv`, and PostgreSQL tools on PATH for the database tests. Commands below run from this repository in PowerShell:

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
.\.venv\Scripts\python.exe scripts/check_production.py
pwsh -NoProfile -File scripts/test_postgres.ps1
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Production checks use ephemeral test configuration, not real credentials. PostgreSQL tests create their own localhost-only cluster on port 55434, run migrations and tests, destroy the test database, and stop the cluster. Cluster files stay ignored under `.local/`. Never point test settings at production.

After each deployment, verify the Railway status is successful, `/health/` returns only `{"status":"ok"}`, login loads over HTTPS, anonymous private routes redirect to login, and CSS/icons/manifest load. A live PIN login and study-flow check should use the owner's device. Railway's deployment health check is a release gate, not continuous uptime monitoring.

To check persistence, create a disposable test word after login, redeploy the same code, and confirm the word and any unfinished study session remain. Delete only the test word afterward. Keep this manual verification separate from local automated database tests.

## iPhone 16 Pro installation

Open the production HTTPS URL in Safari, log in, and choose **Share → Add to Home Screen**. Enable **Open as Web App** if shown, then tap Add. Launch the new icon and confirm standalone display, safe-area spacing, word forms, Reveal, response buttons, and remembered login. Home Screen login storage can differ from Safari, so a separate PIN login may be needed. Offline studying is not supported.

## Troubleshooting and recovery

- **Railway cannot access the GitHub repo:** grant its GitHub App access to `AleIgoKha/english_environment`, then reconnect the service source to `main`.
- **Deployment readiness fails:** check deployment logs for missing variables or migration errors. `/health/` intentionally hides database details. Do not print the variable list or database URL into shared logs.
- **HTTPS redirect loop / CSRF error:** verify the generated domain variables, trusted origins, and Railway proxy settings. Do not disable CSRF or secure cookies to bypass the error.
- **Static files missing:** confirm startup completed `collectstatic` and WhiteNoise loaded its manifest. The pre-deploy container is separate; static files must be collected in the running app container.
- **PIN locked out:** wait 15 minutes after five consecutive failures. Attempts during the lock do not extend it.
- **Failed release:** use Railway's prior successful deployment to roll back the app. Review schema compatibility before rolling back after a migration; application rollback does not roll back the database.

Backup availability depends on the Railway workspace plan. Confirm supported PostgreSQL volume backup options in Railway. Keep encrypted database exports outside this repository if using manual backups; never commit a backup. No plan upgrade or backup schedule is implied by this deployment.

## Validation status

- Production settings check and static collection: passed locally.
- Full final suite: 78 tests passed on isolated PostgreSQL, including real concurrent Start/duplicate-answer requests, installable manifest assets, and deployment-profile safety checks.
- GitHub publication and native source connection: completed for `AleIgoKha/english_environment`, `main`.
- Production migrations: applied successfully to the dedicated PostgreSQL service.
- Verified automatic release: `9edcd6a5-61fe-4767-af88-253627cf1a87`, triggered by publishing GitHub commit `8c17e29a661d564fc87b506bbf8f1c8ed07523b2` to `main`.
- Live health and static assets: verified. Private routes redirect anonymous visitors to PIN login. Production debug is disabled and HTTPS protections are enabled.
- Native push trigger: verified for `AleIgoKha/english_environment`, `main`, production, and this app service only. Trigger ID: `01f22e81-fd54-4eb6-bdb0-8cf14824bccf`. The subsequent push started and completed a new deployment without a manual deploy command.
- PostgreSQL migration history and initialized schema survived replacement of the app container; the later deployment reported `No migrations to apply` and passed readiness. Actual owner word/progress checks remain separate below.
- No local word data was imported. The PostgreSQL service uses private networking; no public database proxy or account-wide SSH keys were added for verification.
- Owner PIN login, word/progress persistence checked from the owner's device, and real iPhone Home Screen visual acceptance: pending manual checks.

References: [Railway config](https://docs.railway.com/config-as-code/reference), [Railway health checks](https://docs.railway.com/deployments/healthchecks), [WhiteNoise](https://whitenoise.readthedocs.io/en/stable/django.html), [Django proxy settings](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header).
