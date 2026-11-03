"""Create a private local .env without printing or storing a plaintext PIN."""
from getpass import getpass
from pathlib import Path
import secrets

from django.contrib.auth.hashers import PBKDF2PasswordHasher


def main():
    target = Path(__file__).resolve().parent.parent / ".env"
    if target.exists():
        raise SystemExit(".env already exists; it was not changed.")
    pin = getpass("Choose a four-digit local PIN (hidden): ")
    if not (len(pin) == 4 and pin.isascii() and pin.isdigit()):
        raise SystemExit("Use exactly four digits (0-9). Nothing was written.")
    if pin != getpass("Confirm your PIN (hidden): "):
        raise SystemExit("PINs did not match. Nothing was written.")
    hasher = PBKDF2PasswordHasher()
    pin_hash = hasher.encode(pin, hasher.salt())
    # Single quotes keep the dollar signs in Django's encoded hash literal.
    content = (
        "DJANGO_DEBUG=true\n"
        f"DJANGO_SECRET_KEY='{secrets.token_urlsafe(48)}'\n"
        f"APP_PIN_HASH='{pin_hash}'\n"
        "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]\n"
    )
    with target.open("x", encoding="utf-8", newline="\n") as env_file:
        env_file.write(content)
    print("Created ignored .env with a random secret and hashed PIN. No plaintext PIN was saved.")


if __name__ == "__main__":
    main()
