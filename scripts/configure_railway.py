"""Set production variables via stdin; never print credentials or copy the deck."""
import argparse
from pathlib import Path
import secrets
import subprocess

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, help="Path to a Railway CLI v5 executable")
    parser.add_argument("--project", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--database-service", required=True)
    parser.add_argument("--environment", default="production")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    local = dotenv_values(root / ".env")
    google_keys = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ALLOWED_GOOGLE_EMAIL")
    if not all(local.get(key) for key in google_keys):
        raise SystemExit("Set Google credentials and allowed email in local .env first. Nothing was sent.")
    # Initial setup only: this rotates the production Django secret.
    values = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        **{key: local[key] for key in google_keys},
        "DJANGO_LOCAL_TEST_LOGIN": "false",
        "DJANGO_DEBUG": "false",
        "DJANGO_ALLOWED_HOSTS": "healthcheck.railway.app",
        "DATABASE_URL": "${{" + arguments.database_service + ".DATABASE_URL}}",
        "PORT": "8000",
    }
    for key, value in values.items():
        result = subprocess.run([
            arguments.cli, "variable", "set", key, "--stdin", "--skip-deploys",
            "--project", arguments.project, "--service", arguments.service,
            "--environment", arguments.environment,
        ], input=value, text=True, capture_output=True, cwd=root)
        if result.returncode:
            raise SystemExit(f"Could not set {key}. Check Railway access and retry. Secret output was suppressed.")
        print(f"Configured {key} (value hidden).")
    print("Production configuration complete. No local words or database files were transferred.")


if __name__ == "__main__":
    main()
