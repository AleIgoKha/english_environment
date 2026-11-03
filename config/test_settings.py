"""Isolated, disposable test configuration; never use for deployment."""
import os
import secrets

os.environ["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["DJANGO_DEBUG"] = "true"
os.environ["APP_PIN_HASH"] = ""

from .settings import *  # noqa: E402,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ALLOWED_HOSTS = ["testserver", "localhost"]
