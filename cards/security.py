"""Owner-only Google sessions and explicitly enabled local test access."""
from functools import wraps
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.shortcuts import redirect
from django.utils.crypto import constant_time_compare, salted_hmac


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


def is_authenticated(request):
    if request.session.get("auth_kind") == "local-test":
        return local_test_login_enabled() and local_request(request)
    return (
        google_configured() and request.session.get("auth_kind") == "google"
        and bool(request.session.get("google_subject"))
        and request.session.get("google_email") == settings.ALLOWED_GOOGLE_EMAIL
        and constant_time_compare(request.session.get("auth_version", ""), auth_version())
    )


def owner_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_authenticated(request):
            request.session.flush()
            return redirect("login")
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped
