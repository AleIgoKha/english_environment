"""PIN verification, shared attempt limits, and remembered login validation."""
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher
from django.db import transaction
from django.db.models import F
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import LoginGuard


def pin_configured():
    try:
        hasher = identify_hasher(settings.APP_PIN_HASH)
        if hasher.algorithm != "pbkdf2_sha256":
            return False
        decoded = hasher.decode(settings.APP_PIN_HASH)
        return bool(decoded["salt"] and decoded["hash"] and decoded["iterations"] > 0)
    except (ValueError, TypeError, KeyError):
        return False


def pin_version():
    return salted_hmac("cards.pin-version", settings.APP_PIN_HASH).hexdigest()


def is_authenticated(request):
    saved_version = request.session.get("pin_version", "")
    return pin_configured() and constant_time_compare(saved_version, pin_version())


def pin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_authenticated(request):
            return redirect("login")
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    return wrapped


@transaction.atomic
def verify_pin(pin):
    """Return (accepted, blocked_until). Never retain the submitted PIN.

    A write before reading serializes attempts on SQLite as well as PostgreSQL.
    The row is created by a migration, before the login endpoint is available.
    """
    LoginGuard.objects.filter(pk=1).update(failed_attempts=F("failed_attempts"))
    guard = LoginGuard.objects.select_for_update().get(pk=1)
    now = timezone.now()
    if guard.blocked_until and guard.blocked_until > now:
        return False, guard.blocked_until
    if guard.blocked_until:
        guard.failed_attempts = 0
        guard.blocked_until = None

    valid_format = len(pin) == 4 and pin.isascii() and pin.isdigit()
    if valid_format and check_password(pin, settings.APP_PIN_HASH):
        guard.failed_attempts = 0
        guard.blocked_until = None
        guard.save(update_fields=["failed_attempts", "blocked_until"])
        return True, None

    guard.failed_attempts += 1
    if guard.failed_attempts >= 5:
        guard.blocked_until = now + timedelta(minutes=15)
    guard.save(update_fields=["failed_attempts", "blocked_until"])
    return False, guard.blocked_until
