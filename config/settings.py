"""Shared settings; production secrets must come from the environment."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "Set DJANGO_SECRET_KEY. For local setup, run python scripts/setup_local.py."
    )
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
ALLOWED_GOOGLE_EMAIL = os.environ.get("ALLOWED_GOOGLE_EMAIL", "").strip().lower()
CARDY_MCP_TOKEN_SHA256 = os.environ.get("CARDY_MCP_TOKEN_SHA256", "").strip().lower()
CARDY_MCP_WRITE_TOKEN_SHA256 = os.environ.get("CARDY_MCP_WRITE_TOKEN_SHA256", "").strip().lower()
CARDY_OAUTH_ENABLED = os.environ.get("CARDY_OAUTH_ENABLED", "false").lower() == "true"
CARDY_PUBLIC_URL = os.environ.get("CARDY_PUBLIC_URL", "https://english-environment-production.up.railway.app").rstrip("/")
if CARDY_OAUTH_ENABLED:
    public_url = urlsplit(CARDY_PUBLIC_URL)
    if (public_url.scheme != "https" or not public_url.netloc or public_url.username or public_url.password
            or public_url.path or public_url.query or public_url.fragment):
        raise ImproperlyConfigured("CARDY_PUBLIC_URL must be an HTTPS origin without credentials, path, query or fragment.")
AUTHLIB_OAUTH2_PROVIDER = {
    "scopes_supported": ["cards:read", "cards:add"],
    "refresh_token_generator": True,
    "token_expires_in": {"authorization_code": 1800, "refresh_token": 1800},
}
LOCAL_TEST_LOGIN = os.environ.get("DJANGO_LOCAL_TEST_LOGIN", "false").lower() == "true"
LOCAL_TEST_NETWORK = os.environ.get("LOCAL_TEST_NETWORK", "").strip()
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
    if host.strip()
]
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
if RAILWAY_DOMAIN:
    ALLOWED_HOSTS.append(RAILWAY_DOMAIN)
if os.environ.get("RAILWAY_ENVIRONMENT_ID"):
    ALLOWED_HOSTS.append("healthcheck.railway.app")
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
if RAILWAY_DOMAIN:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RAILWAY_DOMAIN}")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "cards",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request", "cards.context_processors.local_asset_version"],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL:
    try:
        database = dj_database_url.parse(DATABASE_URL, conn_max_age=60, conn_health_checks=True)
    except (ValueError, KeyError):
        raise ImproperlyConfigured("DATABASE_URL is invalid; check its Railway variable.") from None
    if database["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("DATABASE_URL must point to PostgreSQL.")
    DATABASES = {"default": database}
elif DEBUG:
    # Test sign-in uses its own local deck; the original deck is preserved.
    local_database = "db.local-test.sqlite3" if LOCAL_TEST_LOGIN else "db.sqlite3"
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / local_database}}
else:
    raise ImproperlyConfigured("DATABASE_URL is required in production. SQLite is only for local development.")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Chisinau"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_MIMETYPES = {".webmanifest": "application/manifest+json"}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG else "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if not DEBUG else None
# Railway's internal deployment health probe uses HTTP; it carries no app data.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"oauth_query": {"()": "cards.logging.RedactOAuthQuery"}},
    "loggers": {"django.server": {"filters": ["oauth_query"]}},
}
LOGGING["loggers"]["authlib.oauth2"] = {"level": "WARNING"}
