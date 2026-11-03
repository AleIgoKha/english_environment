"""Linux production entry point; Python avoids Windows shell line endings."""
import os
import subprocess
import sys


def main():
    subprocess.run([sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"], check=True)
    subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"], check=True)
    port = int(os.environ.get("PORT", "8000"))
    os.execvp("gunicorn", [
        "gunicorn", "config.wsgi:application", "--bind", f"0.0.0.0:{port}",
        "--workers", "2", "--threads", "2", "--timeout", "60",
        "--access-logfile", "-", "--error-logfile", "-",
    ])


if __name__ == "__main__":
    main()
