"""OAuth connection security; all accounts, codes, and tokens are disposable."""
from datetime import timedelta
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import McpOAuthClient, McpOAuthCode, McpOAuthToken, Word
from .oauth_server import digest
from .security import auth_version
from .tests import GOOGLE_SETTINGS

REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
RESOURCE = "https://testserver/mcp/"


@override_settings(**GOOGLE_SETTINGS, CARDY_OAUTH_ENABLED=True, CARDY_PUBLIC_URL="https://testserver")
class OAuthTests(TestCase):
    def setUp(self):
        self.verifier = "v" * 64
        registration = self.client.post(reverse("oauth-register"), json.dumps({"client_name": "ChatGPT",
            "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}), content_type="application/json", secure=True)
        self.assertEqual(registration.status_code, 201)
        self.client_id = registration.json()["client_id"]
        self.params = {"client_id": self.client_id, "redirect_uri": REDIRECT, "response_type": "code",
                       "scope": "cards:read cards:add", "state": "test-state", "resource": RESOURCE,
                       "code_challenge": create_s256_code_challenge(self.verifier), "code_challenge_method": "S256"}

    def login_owner(self, client=None):
        client = client or self.client
        session = client.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test", "google_subject": "owner",
                        "auth_version": auth_version()})
        session.save()

    def authorize(self, params=None, decision="allow"):
        self.login_owner()
        url = reverse("oauth-authorize") + "?" + urlencode(params or self.params)
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 200)
        approved = self.client.post(url, {"decision": decision, "consent_nonce": response.context["nonce"]}, secure=True)
        self.assertEqual(approved.status_code, 302)
        return parse_qs(urlsplit(approved["Location"]).query)

    def exchange(self, **overrides):
        values = {"grant_type": "authorization_code", "client_id": self.client_id, "redirect_uri": REDIRECT,
                  "resource": RESOURCE, "code_verifier": self.verifier, **overrides}
        return self.client.post(reverse("oauth-token"), urlencode(values),
                                content_type="application/x-www-form-urlencoded", secure=True)

    def rpc(self, method, params=None, access_token=None):
        headers = {"HTTP_ACCEPT": "application/json, text/event-stream"}
        if access_token is not None:
            headers["HTTP_AUTHORIZATION"] = "Bearer " + access_token
        return self.client.post(reverse("mcp"), json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                           "params": params or {}}),
                                content_type="application/json", secure=True, **headers)

    def test_discovery_and_public_tool_metadata_do_not_disclose_cards(self):
        Word.objects.create(chunk="private phrase")
        metadata = self.client.get("/.well-known/oauth-authorization-server", secure=True).json()
        self.assertEqual(metadata["issuer"], "https://testserver")
        self.assertEqual(metadata["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(metadata["authorization_response_iss_parameter_supported"])
        resource = self.client.get("/.well-known/oauth-protected-resource/mcp/", secure=True).json()
        self.assertEqual(resource["resource"], RESOURCE)
        inventory = self.rpc("tools/list")
        tools = inventory.json()["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["check_existing_cards", "add_cards"])
        self.assertEqual(tools[1]["securitySchemes"], [{"type": "oauth2", "scopes": ["cards:add"]}])
        self.assertNotContains(inventory, "private phrase")
        denied = self.rpc("tools/call", {"name": "check_existing_cards", "arguments": {"phrases": ["private phrase"]}})
        self.assertTrue(denied.json()["result"]["isError"])
        self.assertIn("mcp/www_authenticate", denied.json()["result"]["_meta"])
        wrong = self.rpc("tools/list", access_token="wrong-token-value-that-is-long-enough-to-test")
        self.assertEqual(wrong.status_code, 401)
        self.assertIn("resource_metadata", wrong["WWW-Authenticate"])

    def test_approved_code_exchange_and_code_reuse(self):
        query = self.authorize()
        self.assertEqual(query["state"], ["test-state"])
        self.assertEqual(query["iss"], ["https://testserver"])
        response = self.exchange(code=query["code"][0])
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["scope"], "cards:read cards:add")
        self.assertTrue(data["refresh_token"])
        stored = McpOAuthToken.objects.get()
        self.assertEqual(stored.access_hash, digest(data["access_token"]))
        self.assertNotIn(data["access_token"], str(list(McpOAuthToken.objects.values())))
        self.assertEqual(self.exchange(code=query["code"][0]).status_code, 400)
        self.assertEqual(McpOAuthToken.objects.count(), 1)

    def test_pkce_redirect_resource_and_unknown_scopes_are_rejected(self):
        code = self.authorize()["code"][0]
        for overrides in ({"code_verifier": "x" * 64}, {"redirect_uri": "https://evil.example/callback"},
                          {"resource": "https://evil.example/mcp"}, {"code_verifier": ""}):
            self.assertEqual(self.exchange(code=code, **overrides).status_code, 400)
        self.assertEqual(McpOAuthToken.objects.count(), 0)
        for overrides in ({"code_challenge_method": "plain"}, {"code_challenge": ""},
                          {"scope": "cards:delete"}, {"redirect_uri": "https://evil.example/callback"},
                          {"resource": "https://evil.example/mcp"}):
            params = self.params | overrides
            self.assertEqual(self.client.get(reverse("oauth-authorize"), params, secure=True).status_code, 400)

    def test_owner_login_consent_and_deny(self):
        url = reverse("oauth-authorize") + "?" + urlencode(self.params)
        response = self.client.get(url, secure=True)
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["oauth_authorize_query"], urlencode(self.params))
        self.assertFalse(McpOAuthCode.objects.exists())
        wrong_owner = self.client.session
        wrong_owner.update({"auth_kind": "google", "google_email": "other@example.test", "google_subject": "other",
                            "auth_version": auth_version()})
        wrong_owner.save()
        self.assertEqual(self.client.get(url, secure=True).status_code, 302)
        denied = self.authorize(decision="deny")
        self.assertEqual(denied["error"], ["access_denied"])
        self.assertEqual(denied["iss"], ["https://testserver"])
        self.assertFalse(McpOAuthCode.objects.exists())

    def test_consent_csrf_nonce_and_request_binding(self):
        self.login_owner()
        url = reverse("oauth-authorize") + "?" + urlencode(self.params)
        page = self.client.get(url, secure=True)
        self.assertEqual(self.client.post(url, {"decision": "allow", "consent_nonce": "wrong"}, secure=True).status_code, 403)
        self.assertFalse(McpOAuthCode.objects.exists())
        page = self.client.get(url, secure=True)
        changed = url.replace("cards%3Aread+cards%3Aadd", "cards%3Aread")
        self.assertEqual(self.client.post(changed, {"decision": "allow", "consent_nonce": page.context["nonce"]}, secure=True).status_code, 403)
        page = self.client.get(url, secure=True)
        client = Client(enforce_csrf_checks=True)
        client.cookies = self.client.cookies.copy()
        self.assertEqual(client.post(url, {"decision": "allow", "consent_nonce": page.context["nonce"]}, secure=True).status_code, 403)
        self.assertFalse(McpOAuthCode.objects.exists())

    def test_scopes_expiry_owner_change_and_no_browser_session_api_access(self):
        code = self.authorize(self.params | {"scope": "cards:read"})["code"][0]
        access_token = self.exchange(code=code).json()["access_token"]
        Word.objects.create(chunk="hello")
        params = {"name": "check_existing_cards", "arguments": {"phrases": ["hello", "new"]}}
        result = self.rpc("tools/call", params, access_token).json()["result"]
        self.assertEqual([match["exists"] for match in result["structuredContent"]["matches"]], [True, False])
        self.assertTrue(self.rpc("tools/call", {"name": "add_cards", "arguments": {}}, access_token).json()["result"]["isError"])
        self.assertTrue(self.rpc("tools/call", params).json()["result"]["isError"])
        with override_settings(ALLOWED_GOOGLE_EMAIL="changed@example.test"):
            self.assertEqual(self.rpc("tools/call", params, access_token).status_code, 401)
        McpOAuthToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.rpc("tools/call", params, access_token).status_code, 401)

    def test_refresh_rotation_replay_revokes_family_and_scope_cannot_expand(self):
        code = self.authorize(self.params | {"scope": "cards:read"})["code"][0]
        data = self.exchange(code=code).json()
        values = {"grant_type": "refresh_token", "refresh_token": data["refresh_token"]}
        denied = self.exchange(**values, scope="cards:read cards:add")
        self.assertEqual(denied.status_code, 400)
        refreshed = self.exchange(**values)
        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        self.assertNotEqual(refreshed.json()["refresh_token"], data["refresh_token"])
        self.assertEqual(self.exchange(**values).status_code, 400)
        self.assertEqual(self.rpc("tools/list", access_token=refreshed.json()["access_token"]).status_code, 401)

    def test_revocation_and_client_binding(self):
        code = self.authorize()["code"][0]
        other = McpOAuthClient.objects.create(client_id="other-client", client_name="Other",
            redirect_uris=[REDIRECT], registration_hash=digest("other-client"))
        self.assertEqual(self.exchange(code=code, client_id=other.client_id).status_code, 400)
        data = self.exchange(code=code).json()
        values = {"grant_type": "refresh_token", "refresh_token": data["refresh_token"]}
        self.assertEqual(self.exchange(**values, client_id=other.client_id).status_code, 400)
        wrong_client = self.client.post(reverse("oauth-revoke"), urlencode({"token": data["refresh_token"], "client_id": other.client_id}),
                                        content_type="application/x-www-form-urlencoded", secure=True)
        self.assertEqual(wrong_client.status_code, 200)
        self.assertEqual(self.rpc("tools/list", access_token=data["access_token"]).status_code, 200)
        revoked = self.client.post(reverse("oauth-revoke"), urlencode({"token": data["refresh_token"], "client_id": self.client_id}),
                                   content_type="application/x-www-form-urlencoded", secure=True)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.rpc("tools/list", access_token=data["access_token"]).status_code, 401)

    def test_code_and_refresh_expiration(self):
        expired_code = self.authorize()["code"][0]
        McpOAuthCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.exchange(code=expired_code).status_code, 400)
        fresh_code = self.authorize()["code"][0]
        data = self.exchange(code=fresh_code).json()
        McpOAuthToken.objects.update(refresh_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.exchange(grant_type="refresh_token", refresh_token=data["refresh_token"]).status_code, 400)

    def test_oauth_write_requires_approved_batch_and_skips_existing_cards(self):
        import uuid
        code = self.authorize()["code"][0]
        access_token = self.exchange(code=code).json()["access_token"]
        Word.objects.create(chunk="existing")
        batch = {"batch_id": str(uuid.uuid4()), "approved": False, "cards": [
            {"obverse": "existing", "reverse": "Already here", "extra": ""},
            {"obverse": "new phrase", "reverse": "Previously: original\nBetter: corrected", "extra": ""}]}
        rejected = self.rpc("tools/call", {"name": "add_cards", "arguments": batch}, access_token)
        self.assertIn("error", rejected.json())
        self.assertEqual(Word.objects.count(), 1)
        batch["approved"] = True
        added = self.rpc("tools/call", {"name": "add_cards", "arguments": batch}, access_token).json()["result"]
        self.assertFalse(added["isError"])
        self.assertEqual(Word.objects.count(), 2)
        self.assertFalse(Word.objects.get(chunk="new phrase").show_reverse_immediately)
        retried = self.rpc("tools/call", {"name": "add_cards", "arguments": batch}, access_token).json()["result"]
        self.assertEqual(added["structuredContent"]["added_count"], 1)
        self.assertEqual(added["structuredContent"]["skipped_count"], 1)
        self.assertEqual(retried["structuredContent"], added["structuredContent"] | {"replayed": True})
        self.assertEqual(Word.objects.count(), 2)

    def test_registration_rejects_untrusted_redirects_and_unbounded_metadata(self):
        for uri in ("https://evil.example/callback", "https://chatgpt.com.evil.example/connector_platform_oauth_redirect",
                    "https://chatgpt.com@evil.example/callback", "http://192.168.0.1/callback", REDIRECT + "#fragment"):
            response = self.client.post(reverse("oauth-register"), json.dumps({"redirect_uris": [uri]}),
                                        content_type="application/json", secure=True)
            self.assertEqual(response.status_code, 400)
        response = self.client.post(reverse("oauth-register"), json.dumps({"redirect_uris": ["http://127.0.0.1:54321/callback"]}),
                                    content_type="application/json", secure=True)
        self.assertEqual(response.status_code, 201)
        again = self.client.post(reverse("oauth-register"), json.dumps({"redirect_uris": [REDIRECT]}),
                                 content_type="application/json", secure=True)
        self.assertEqual(again.json()["client_id"], self.client_id)
        self.assertEqual(McpOAuthClient.objects.count(), 2)

    def test_google_callback_resumes_only_saved_authorization_request(self):
        session = self.client.session
        session["oauth_authorize_query"] = urlencode(self.params)
        session.save()
        with patch("cards.auth_views.google_client") as client:
            client.return_value.authorize_access_token.return_value = {"userinfo": {
                "sub": "owner", "email": "owner@example.test", "email_verified": True}}
            response = self.client.get(reverse("google-callback"), {"code": "disposable", "state": "test"}, secure=True)
        self.assertEqual(response["Location"], reverse("oauth-authorize") + "?" + urlencode(self.params))

    def test_oauth_disabled_does_not_expose_metadata_or_registration(self):
        with override_settings(CARDY_OAUTH_ENABLED=False):
            self.assertEqual(self.client.get("/.well-known/oauth-authorization-server", secure=True).status_code, 404)
            self.assertEqual(self.client.post(reverse("oauth-register"), "{}", content_type="application/json", secure=True).status_code, 404)
