"""Owner-only OAuth authorization-code + S256 PKCE provider using Authlib."""
from datetime import timedelta
from functools import wraps
import hashlib
from ipaddress import ip_address
import json
import re
import secrets
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

from authlib.common.urls import add_params_to_uri
from authlib.integrations.django_oauth2 import AuthorizationServer
from authlib.oauth2.rfc6749 import InvalidRequestError, OAuth2Error
from authlib.oauth2.rfc6749.grants import AuthorizationCodeGrant, RefreshTokenGrant
from authlib.oauth2.rfc7636 import CodeChallenge
from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import learning
from .models import McpOAuthClient, McpOAuthCode, McpOAuthToken
from .security import auth_version, is_authenticated

SCOPES = ["cards:read", "cards:add"]


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def enabled(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not settings.CARDY_OAUTH_ENABLED:
            raise Http404
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped


def issuer():
    return settings.CARDY_PUBLIC_URL


def resource():
    return issuer() + reverse("mcp")


def valid_owner(credential):
    return (credential.owner_email == settings.ALLOWED_GOOGLE_EMAIL
            and constant_time_compare(credential.owner_version, auth_version())
            and credential.resource == resource())


def owner(credential):
    if valid_owner(credential):
        return SimpleNamespace(pk=credential.owner_subject, subject=credential.owner_subject,
                               email=credential.owner_email, version=credential.owner_version)


def access_scopes(token_hash):
    if not settings.CARDY_OAUTH_ENABLED:
        return set()
    token = McpOAuthToken.objects.filter(access_hash=token_hash, revoked_at__isnull=True,
                                         expires_at__gt=timezone.now()).first()
    return set(token.scope.split()) if token and valid_owner(token) else set()


def challenge(scope="cards:read", error="invalid_token"):
    return (f'Bearer resource_metadata="{issuer()}/.well-known/oauth-protected-resource", '
            f'error="{error}", error_description="Connect your Cardy account to continue", scope="{scope}"')


class RequiredS256(CodeChallenge):
    SUPPORTED_CODE_CHALLENGE_METHOD = ["S256"]

    def validate_code_challenge(self, grant, redirect_uri):
        super().validate_code_challenge(grant, redirect_uri)
        if (not grant.request.payload.data.get("code_challenge")
                or grant.request.payload.data.get("code_challenge_method") != "S256"):
            raise InvalidRequestError("S256 PKCE is required.")


class CodeGrant(AuthorizationCodeGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]

    @sensitive_variables()
    def save_authorization_code(self, code, request):
        McpOAuthCode.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()
        McpOAuthCode.objects.create(
            code_hash=digest(code), client=request.client, redirect_uri=request.payload.redirect_uri,
            scope=request.scope, resource=resource(), owner_email=request.user.email,
            owner_subject=request.user.subject, owner_version=request.user.version,
            code_challenge=request.payload.data["code_challenge"], code_challenge_method="S256",
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def query_authorization_code(self, code, client):
        return McpOAuthCode.objects.select_for_update().filter(
            code_hash=digest(code), client=client, consumed_at__isnull=True,
            expires_at__gt=timezone.now(), resource=resource()).first()

    def delete_authorization_code(self, code):
        code.consumed_at = timezone.now()
        code.save(update_fields=["consumed_at"])

    def authenticate_user(self, code):
        return owner(code)


class RefreshGrant(RefreshTokenGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]
    INCLUDE_NEW_REFRESH_TOKEN = True

    def authenticate_refresh_token(self, refresh_token):
        token = McpOAuthToken.objects.select_for_update().filter(refresh_hash=digest(refresh_token)).first()
        if not token or not valid_owner(token) or token.refresh_expires_at <= timezone.now():
            return None
        if token.revoked_at:
            # Reusing an already-rotated refresh token revokes the whole family.
            McpOAuthToken.objects.filter(family=token.family).update(revoked_at=timezone.now())
            return None
        return token

    def authenticate_user(self, token):
        return owner(token)

    def revoke_old_credential(self, token):
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])


class CardyAuthorizationServer(AuthorizationServer):
    @sensitive_variables()
    def save_token(self, token, request):
        previous = getattr(request, "refresh_token", None)
        now = timezone.now()
        McpOAuthToken.objects.create(
            access_hash=digest(token["access_token"]), refresh_hash=digest(token["refresh_token"]),
            client=request.client, family=previous.family if previous else uuid.uuid4(),
            scope=token["scope"], resource=resource(), owner_email=request.user.email,
            owner_subject=request.user.subject, owner_version=request.user.version,
            expires_at=now + timedelta(seconds=token["expires_in"]),
            refresh_expires_at=previous.refresh_expires_at if previous else now + timedelta(days=14),
        )


def server():
    instance = CardyAuthorizationServer(McpOAuthClient, McpOAuthToken)
    instance.register_grant(CodeGrant, [RequiredS256(required=True)])
    instance.register_grant(RefreshGrant)
    return instance


@enabled
@require_GET
def authorization_metadata(request):
    return JsonResponse({
        "issuer": issuer(), "authorization_endpoint": issuer() + reverse("oauth-authorize"),
        "token_endpoint": issuer() + reverse("oauth-token"),
        "registration_endpoint": issuer() + reverse("oauth-register"),
        "revocation_endpoint": issuer() + reverse("oauth-revoke"),
        "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"],
        "scopes_supported": SCOPES, "authorization_response_iss_parameter_supported": True,
    })


@enabled
@require_GET
def resource_metadata(request):
    return JsonResponse({"resource": resource(), "authorization_servers": [issuer()],
                         "scopes_supported": SCOPES, "bearer_methods_supported": ["header"]})


def permitted_redirect(uri):
    try:
        parsed = urlsplit(uri)
        if parsed.username or parsed.password or parsed.fragment or len(uri) > 2048:
            return False
        if parsed.scheme == "https" and parsed.hostname == "chatgpt.com" and parsed.port in (None, 443):
            return (parsed.path == "/connector_platform_oauth_redirect"
                    or bool(re.fullmatch(r"/connector/oauth/[A-Za-z0-9_-]+", parsed.path))) and not parsed.query
        if parsed.scheme == "http" and parsed.port and not parsed.query:
            return parsed.hostname == "localhost" or ip_address(parsed.hostname).is_loopback
    except (ValueError, TypeError):
        pass
    return False


@csrf_exempt
@enabled
@require_POST
@sensitive_post_parameters()
@sensitive_variables()
def register(request):
    if request.content_type != "application/json":
        return JsonResponse({"error": "invalid_client_metadata"}, status=400)
    try:
        raw = request.read(8193)
        data = json.loads(raw) if len(raw) <= 8192 else None
    except (ValueError, UnicodeError, RecursionError):
        data = None
    if not isinstance(data, dict):
        return JsonResponse({"error": "invalid_client_metadata"}, status=400)
    uris = data.get("redirect_uris")
    if (not isinstance(uris, list) or not 1 <= len(uris) <= 5
            or any(not isinstance(uri, str) or not permitted_redirect(uri) for uri in uris)
            or data.get("token_endpoint_auth_method", "none") != "none"
            or data.get("response_types", ["code"]) != ["code"]
            or not isinstance(data.get("grant_types", ["authorization_code"]), list)
            or any(not isinstance(item, str) for item in data.get("grant_types", ["authorization_code"]))
            or "authorization_code" not in data.get("grant_types", ["authorization_code"])
            or not set(data.get("grant_types", ["authorization_code"])) <= {"authorization_code", "refresh_token"}):
        return JsonResponse({"error": "invalid_client_metadata"}, status=400)
    name = data.get("client_name", "ChatGPT or Codex")
    if not isinstance(name, str) or not 1 <= len(name) <= 100:
        return JsonResponse({"error": "invalid_client_metadata"}, status=400)
    normalized = sorted(set(uris))
    key = digest(json.dumps(normalized))
    with transaction.atomic():
        learning.lock_preferences()
        client = McpOAuthClient.objects.filter(registration_hash=key).first()
        if not client:
            if McpOAuthClient.objects.count() >= 256:
                return JsonResponse({"error": "temporarily_unavailable"}, status=429)
            client = McpOAuthClient.objects.create(client_id=secrets.token_urlsafe(24), client_name=name,
                                                   redirect_uris=normalized, registration_hash=key)
    return JsonResponse({"client_id": client.client_id, "client_name": client.client_name,
                         "redirect_uris": client.redirect_uris, "token_endpoint_auth_method": "none",
                         "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                         "scope": "cards:read cards:add"}, status=201)


def require_resource(params):
    if params.getlist("resource") != [resource()]:
        raise InvalidRequestError("The resource must be Cardy's exact MCP URL.")


@enabled
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
@sensitive_variables()
def authorize(request):
    if len(request.GET.urlencode()) > 4096:
        return HttpResponse(status=400)
    authorization = server()
    try:
        require_resource(request.GET)
        grant = authorization.get_consent_grant(request)
    except OAuth2Error:
        return JsonResponse({"error": "invalid_request", "error_description": "Invalid authorization request."}, status=400)
    if not is_authenticated(request):
        if request.method != "GET":
            return HttpResponse(status=403)
        request.session["oauth_authorize_query"] = request.GET.urlencode()
        return redirect("login")
    if request.session.get("auth_kind") != "google":
        return HttpResponse("Connect using the owner's Google sign-in.", status=403)
    query_hash = digest(request.GET.urlencode())
    if request.method == "GET":
        nonce = secrets.token_urlsafe(24)
        request.session["oauth_consent"] = {"query_hash": query_hash, "nonce": nonce}
        return render(request, "cards/oauth_consent.html", {
            "client_name": grant.request.client.client_name,
            "redirect_host": urlsplit(grant.redirect_uri).netloc,
            "can_add": "cards:add" in grant.request.scope.split(), "nonce": nonce,
        })
    consent = request.session.pop("oauth_consent", {})
    if (set(request.POST) - {"csrfmiddlewaretoken", "consent_nonce", "decision"}
            or consent.get("query_hash") != query_hash
            or not constant_time_compare(consent.get("nonce", ""), request.POST.get("consent_nonce", ""))
            or request.POST.get("decision") not in ("allow", "deny")):
        return HttpResponse("Reopen the connection request and review it again.", status=403)
    user = SimpleNamespace(pk=request.session["google_subject"], subject=request.session["google_subject"],
                           email=settings.ALLOWED_GOOGLE_EMAIL, version=auth_version())
    with transaction.atomic():
        learning.lock_preferences()
        response = authorization.create_authorization_response(grant.request,
            grant_user=user if request.POST["decision"] == "allow" else None, grant=grant)
    if response.get("Location"):
        response["Location"] = add_params_to_uri(response["Location"], [("iss", issuer())])
    return response


@csrf_exempt
@enabled
@require_POST
@sensitive_post_parameters()
@sensitive_variables()
def token(request):
    if request.content_type != "application/x-www-form-urlencoded" or len(request.body) > 8192:
        return JsonResponse({"error": "invalid_request"}, status=400)
    try:
        require_resource(request.POST)
    except OAuth2Error:
        return JsonResponse({"error": "invalid_target"}, status=400)
    with transaction.atomic():
        learning.lock_preferences()
        return server().create_token_response(request)


@csrf_exempt
@enabled
@require_POST
@sensitive_post_parameters()
@sensitive_variables()
def revoke(request):
    if request.content_type != "application/x-www-form-urlencoded" or len(request.body) > 8192:
        return HttpResponse(status=400)
    value = request.POST.get("token", "")
    client_id = request.POST.get("client_id", "")
    if value and client_id:
        with transaction.atomic():
            learning.lock_preferences()
            token_hash = digest(value)
            credential = (McpOAuthToken.objects.filter(client__client_id=client_id, refresh_hash=token_hash).first()
                          or McpOAuthToken.objects.filter(client__client_id=client_id, access_hash=token_hash).first())
            if credential:
                McpOAuthToken.objects.filter(family=credential.family).update(revoked_at=timezone.now())
    return HttpResponse(status=200)
