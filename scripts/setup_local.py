"""Create a private local environment with a separate test deck."""
from pathlib import Path
import secrets


def main():
    target = Path(__file__).resolve().parent.parent / ".env"
    if target.exists():
        raise SystemExit(".env already exists; it was not changed.")
    content = (
        "DJANGO_DEBUG=true\n"
        f"DJANGO_SECRET_KEY='{secrets.token_urlsafe(48)}'\n"
        "LOCAL_DEV_ADDRESS=127.0.0.1:8000\n"
        "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]\n"
        "DJANGO_LOCAL_TEST_LOGIN=true\n"
        "GOOGLE_CLIENT_ID=\n"
        "GOOGLE_CLIENT_SECRET=\n"
        "ALLOWED_GOOGLE_EMAIL=\n"
    )
    with target.open("x", encoding="utf-8", newline="\n") as env_file:
        env_file.write(content)
    print("Created ignored .env. Local test sign-in is enabled; Google credentials are optional locally.")


if __name__ == "__main__":
    main()
