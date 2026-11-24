"""Google OpenID Connect login. Tokens are verified, then discarded."""
from authlib.integrations.django_client import OAuth
from authlib.integrations.base_client.errors import OAuthError
from django.conf import settings
from django.http import Http404
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_POST, require_safe
from joserfc.errors import JoseError
from requests import RequestException

from .models import Account, LearningSettings, LOCAL_TEST_ACCOUNT_ID, OWNER_ACCOUNT_ID
from .security import (
    auth_version, google_configured, is_authenticated, local_request, local_test_login_enabled,
    public_sign_in_enabled,
)


def google_client():
    return OAuth().register(
        name="google", client_id=settings.GOOGLE_CLIENT_ID, client_secret=settings.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email", "code_challenge_method": "S256", "timeout": 10},
    )


def login_page(request, error=None, status=200):
    return render(request, "cards/login.html", {
        "configured": google_configured(),
        "local_test_login": local_test_login_enabled() and local_request(request),
        "error": error,
    }, status=status)


@never_cache
@require_safe
def login(request):
    if is_authenticated(request):
        return redirect("home")
    return login_page(request)


@never_cache
@require_POST
def local_test_login(request):
    if not local_test_login_enabled() or not local_request(request):
        raise Http404
    request.session.flush()
    request.session["auth_kind"] = "local-test"
    request.session["account_id"] = str(LOCAL_TEST_ACCOUNT_ID)
    rotate_token(request)
    return redirect("home")


@never_cache
@require_POST
def google_login(request):
    if not google_configured():
        return login_page(request, "Google sign-in isn't configured yet.", status=503)
    try:
        request.session.cycle_key()
        return google_client().authorize_redirect(
            request, request.build_absolute_uri(reverse("google-callback")), prompt="select_account",
        )
    except (OAuthError, RequestException, ValueError):
        return login_page(request, "Google sign-in is temporarily unavailable. Please try again.", status=503)


@never_cache
@require_safe
@sensitive_variables()
def google_callback(request):
    if not google_configured():
        return redirect("login")
    try:
        # Authlib verifies state, signature, issuer, audience, expiry and nonce.
        token = google_client().authorize_access_token(request)
        info = token.get("userinfo", {})
        email = info.get("email")
        subject = info.get("sub")
        if (not isinstance(subject, str) or not subject or len(subject) > 255
                or not isinstance(email, str) or not email.strip() or info.get("email_verified") is not True):
            raise ValueError("Account not permitted")
        email = email.strip().lower()
        if email == settings.ALLOWED_GOOGLE_EMAIL and not settings.CARDY_PUBLIC_SIGN_IN:
            Account.objects.filter(pk=OWNER_ACCOUNT_ID, google_subject__isnull=True).update(google_subject=subject)
        if public_sign_in_enabled():
            account, _ = Account.objects.get_or_create(google_subject=subject)
        else:
            account = Account.objects.filter(pk=OWNER_ACCOUNT_ID, google_subject=subject).first()
            if account is None:
                raise ValueError("Account not permitted")
        LearningSettings.objects.get_or_create(account=account)
        oauth_query = request.session.pop("oauth_authorize_query", None)
        request.session.flush()
        request.session.update({
            "auth_kind": "google", "account_id": str(account.pk),
            "google_subject": subject, "auth_version": auth_version(),
        })
        rotate_token(request)
        if oauth_query and settings.CARDY_OAUTH_ENABLED:
            return redirect(reverse("oauth-authorize") + "?" + oauth_query)
        return redirect("home")
    except (OAuthError, JoseError, RequestException, ValueError, KeyError, TypeError):
        return login_page(request, "Sign-in failed or this Google account isn't allowed. Please try again.", status=403)


@never_cache
@require_POST
def logout(request):
    request.session.flush()
    return redirect("login")
