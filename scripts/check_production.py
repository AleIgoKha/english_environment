"""Check production settings with ephemeral test credentials, without a DB write."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

from django.contrib.auth.hashers import PBKDF2PasswordHasher


def main():
    hasher = PBKDF2PasswordHasher()
    environment = os.environ.copy()
    environment.update({
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
        "APP_PIN_HASH": hasher.encode(secrets.token_urlsafe(32), hasher.salt()),
        "DJANGO_DEBUG": "false",
        "DJANGO_ALLOWED_HOSTS": "checks.example.invalid",
        "DATABASE_URL": "postgresql://test_fixture:test_fixture@127.0.0.1:55434/checks",
        "RAILWAY_PUBLIC_DOMAIN": "checks.example.invalid",
        "RAILWAY_ENVIRONMENT_ID": "local-settings-check",
    })
    root = Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"], cwd=root, env=environment, check=True)
    subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"], cwd=root, env=environment, check=True)


if __name__ == "__main__":
    main()
