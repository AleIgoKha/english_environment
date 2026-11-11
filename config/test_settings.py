"""Isolated, disposable test configuration; never use for deployment."""
import os
import secrets

os.environ["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["DJANGO_DEBUG"] = "true"
os.environ["DJANGO_LOCAL_TEST_LOGIN"] = "false"
os.environ["LOCAL_TEST_NETWORK"] = ""

from .settings import *  # noqa: E402,F403

# Tests must never inherit real local MCP credentials.
CARDY_MCP_TOKEN_SHA256 = ""
CARDY_MCP_WRITE_TOKEN_SHA256 = ""
CARDY_OAUTH_ENABLED = False
CARDY_PUBLIC_URL = "https://testserver"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ALLOWED_HOSTS = ["testserver", "localhost"]
