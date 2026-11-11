"""Owner-controlled generation and rotation, with disposable credentials only."""
import hashlib
import json

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import McpBearerCredential
from .security import auth_version
from .test_mcp import TEST_TOKEN, TOKEN_HASH
from .test_mcp_write import WRITE_TOKEN, WRITE_HASH
from .tests import GOOGLE_SETTINGS


@override_settings(**GOOGLE_SETTINGS, CARDY_MCP_TOKEN_SHA256=TOKEN_HASH,
                   CARDY_MCP_WRITE_TOKEN_SHA256=WRITE_HASH)
class McpCredentialTests(TestCase):
    def setUp(self):
        self.login_owner(self.client)

    def login_owner(self, client):
        session = client.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test",
                        "google_subject": "owner", "auth_version": auth_version()})
        session.save()

    def generate(self, role, client=None, **kwargs):
        return (client or self.client).post(reverse("mcp-token-generate", args=[role]), secure=True, **kwargs)

    def catalog(self, authorization):
        return self.client.post(reverse("mcp"), json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            content_type="application/json", secure=True, HTTP_AUTHORIZATION=authorization,
            HTTP_ACCEPT="application/json, text/event-stream")

    def test_generation_is_owner_only_csrf_protected_and_post_only(self):
        self.assertEqual(self.generate("read", Client()).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        self.login_owner(client)
        self.assertEqual(self.generate("read", client).status_code, 403)
        page = client.get(reverse("learning-settings"), secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.generate("read", client, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
            HTTP_ORIGIN="https://testserver").status_code, 200)
        self.assertEqual(self.client.get(reverse("mcp-token-generate", args=["read"]), secure=True).status_code, 405)
        self.assertEqual(self.generate("admin").status_code, 404)

    def test_real_owner_requires_https_and_invalid_owner_cannot_generate(self):
        self.assertEqual(self.client.post(reverse("mcp-token-generate", args=["read"])).status_code, 403)
        with override_settings(ALLOWED_GOOGLE_EMAIL="someone-else@example.test"):
            self.assertEqual(self.generate("read").status_code, 302)
        self.assertFalse(McpBearerCredential.objects.exists())

    def test_read_token_is_hash_only_shown_once_and_replaces_legacy_read_only(self):
        response = self.generate("read")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Pragma"], "no-cache")
        authorization = response.json()["authorization"]
        self.assertTrue(authorization.startswith("Bearer "))
        raw = authorization.removeprefix("Bearer ")
        credential = McpBearerCredential.objects.get(role="read")
        self.assertEqual(credential.token_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertNotIn(raw, str(list(McpBearerCredential.objects.values())))
        self.assertNotIn(raw, str(dict(self.client.session)))
        self.assertEqual(self.catalog("Bearer " + TEST_TOKEN).status_code, 401)
        self.assertEqual(self.catalog("Bearer " + WRITE_TOKEN).status_code, 200)
        self.assertEqual([tool["name"] for tool in self.catalog(authorization).json()["result"]["tools"]], ["check_existing_cards"])
        settings_page = self.client.get(reverse("learning-settings"), secure=True)
        self.assertNotContains(settings_page, raw)
        self.assertContains(settings_page, "Regenerate read token")
        self.assertContains(settings_page, "Generate write token")

    def test_regeneration_revokes_previous_role_without_restoring_environment_token(self):
        read = self.generate("read").json()["authorization"]
        write = self.generate("write").json()["authorization"]
        replacement = self.generate("write").json()["authorization"]
        self.assertNotEqual(write, replacement)
        self.assertEqual(self.catalog(write).status_code, 401)
        self.assertEqual(self.catalog("Bearer " + WRITE_TOKEN).status_code, 401)
        self.assertEqual(self.catalog(read).status_code, 200)
        tools = self.catalog(replacement).json()["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["add_cards"])
        self.assertEqual(McpBearerCredential.objects.count(), 2)
        with override_settings(GOOGLE_CLIENT_SECRET="changed-test-secret"):
            self.assertEqual(self.catalog(read).status_code, 404)
            self.assertEqual(self.catalog(replacement).status_code, 404)
            self.assertEqual(self.catalog("Bearer " + TEST_TOKEN).status_code, 404)

    @override_settings(DEBUG=True, LOCAL_TEST_LOGIN=True, RAILWAY_ENVIRONMENT_ID="", LOCAL_TEST_NETWORK="192.168.0.0/24")
    def test_local_preview_uses_test_deck_url_and_stays_unavailable_in_production(self):
        session = self.client.session
        session.clear()
        session["auth_kind"] = "local-test"
        session.save()
        page = self.client.get(reverse("learning-settings"), HTTP_HOST="localhost")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'value="http://localhost/mcp/"')
        self.assertContains(page, 'value="cardy-local"')
        local = self.client.post(reverse("mcp-token-generate", args=["read"]), HTTP_HOST="localhost")
        self.assertEqual(local.status_code, 200)
        with override_settings(RAILWAY_ENVIRONMENT_ID="production", DEBUG=False):
            self.assertEqual(self.generate("write").status_code, 302)
