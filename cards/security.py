"""Cardy account sessions and explicitly enabled local test access."""
from functools import wraps
from ipaddress import ip_address, ip_network
from uuid import UUID

from django.conf import settings
from django.shortcuts import redirect
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import Account, LOCAL_TEST_ACCOUNT_ID, OWNER_ACCOUNT_ID


def google_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.ALLOWED_GOOGLE_EMAIL)


def auth_version():
    values = [settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET, settings.ALLOWED_GOOGLE_EMAIL]
    return salted_hmac("cards.google-session", "\n".join(values)).hexdigest()


def local_test_login_enabled():
    database = settings.DATABASES["default"]
    return (
        settings.DEBUG and settings.LOCAL_TEST_LOGIN
        and not settings.RAILWAY_ENVIRONMENT_ID
        and (database["ENGINE"] == "django.db.backends.sqlite3"
             or database.get("HOST") in ("127.0.0.1", "localhost", "::1"))
    )


def local_request(request):
    try:
        address = ip_address(request.META.get("REMOTE_ADDR", ""))
        if address.is_loopback:
            return True
        if not settings.LOCAL_TEST_NETWORK:
            return False
        network = ip_network(settings.LOCAL_TEST_NETWORK, strict=True)
        # Only explicitly configured private LANs may use the local test account.
        private_lans = (ip_network("10.0.0.0/8"), ip_network("172.16.0.0/12"), ip_network("192.168.0.0/16"))
        return network.version == 4 and any(network.subnet_of(lan) for lan in private_lans) and address in network
    except ValueError:
        return False


def public_sign_in_enabled():
    return settings.CARDY_PUBLIC_SIGN_IN and Account.objects.filter(
        pk=OWNER_ACCOUNT_ID, google_subject__isnull=False,
    ).exists()


def authenticated_account(request):
    kind = request.session.get("auth_kind")
    try:
        account_id = UUID(request.session.get("account_id", ""))
    except (AttributeError, TypeError, ValueError):
        return None
    if kind == "local-test":
        if account_id == LOCAL_TEST_ACCOUNT_ID and local_test_login_enabled() and local_request(request):
            return Account.objects.filter(pk=account_id).first()
        return None
    if (kind != "google" or account_id == LOCAL_TEST_ACCOUNT_ID or not google_configured()
            or not constant_time_compare(request.session.get("auth_version", ""), auth_version())):
        return None
    subject = request.session.get("google_subject")
    if not subject or (account_id != OWNER_ACCOUNT_ID and not public_sign_in_enabled()):
        return None
    return Account.objects.filter(pk=account_id, google_subject=subject).first()


def is_authenticated(request):
    return authenticated_account(request) is not None


def mcp_account_id():
    return LOCAL_TEST_ACCOUNT_ID if local_test_login_enabled() else OWNER_ACCOUNT_ID


def account_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        account = authenticated_account(request)
        if account is None:
            request.session.flush()
            return redirect("login")
        request.account = account
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped
