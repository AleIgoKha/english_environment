"""Set production variables via stdin; never print credentials or copy the deck."""
import argparse
from pathlib import Path
import secrets
import subprocess

from django.contrib.auth.hashers import PBKDF2PasswordHasher
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
    pin_hash = local.get("APP_PIN_HASH", "")
    try:
        decoded = PBKDF2PasswordHasher().decode(pin_hash)
        if not (decoded["salt"] and decoded["hash"] and decoded["iterations"] > 0):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SystemExit("Configure a local PIN with scripts/setup_local.py first. No credentials were sent.")
    # This configures a fresh production secret and preserves the chosen PIN.
    values = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "APP_PIN_HASH": pin_hash,
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
