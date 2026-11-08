#!/usr/bin/env python
"""Run Django development and maintenance commands."""
import os
import sys
from pathlib import Path


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "runserver":
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
        # Use the same local address unless one is supplied explicitly.
        if not any(not arg.startswith("-") for arg in sys.argv[2:]):
            sys.argv.insert(2, os.environ.get("LOCAL_DEV_ADDRESS", "127.0.0.1:8000"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
