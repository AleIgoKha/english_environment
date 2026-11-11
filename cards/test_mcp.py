"""MCP authentication, protocol handling, validation, and read-only matching."""
import hashlib
import json
from unittest.mock import patch

from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .mcp import MAX_BYTES
from .models import Word, WordImport, StudySession, StudyReview

TEST_TOKEN = "disposable-mcp-test-token-not-a-real-credential"
TOKEN_HASH = hashlib.sha256(TEST_TOKEN.encode()).hexdigest()


@override_settings(CARDY_MCP_TOKEN_SHA256=TOKEN_HASH)
class McpTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {TEST_TOKEN}",
                        "HTTP_ACCEPT": "application/json, text/event-stream"}

    def rpc(self, method, params=None, **headers):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            payload["params"] = params
        return self.client.post(reverse("mcp"), json.dumps(payload), content_type="application/json",
                                **(self.headers | headers))

    def call(self, phrases):
        return self.rpc("tools/call", {"name": "check_existing_cards", "arguments": {"phrases": phrases}})

    def test_disabled_and_authentication_fail_closed_without_database_queries(self):
        for value in ("", "bad-config"):
            with override_settings(CARDY_MCP_TOKEN_SHA256=value), self.assertNumQueries(0):
                self.assertEqual(self.rpc("tools/list").status_code, 404)
        for header in ("", "Bearer wrong", f"Basic {TEST_TOKEN}", f"Bearer {TEST_TOKEN}x"):
            with self.assertNumQueries(0):
                response = self.rpc("tools/list", HTTP_AUTHORIZATION=header)
            self.assertEqual(response.status_code, 401)
            self.assertIn("Bearer", response["WWW-Authenticate"])
            self.assertIn("no-store", response["Cache-Control"])
        # A valid browser login must not grant MCP access without its separate token.
        session = self.client.session
        session["auth_kind"] = "local-test"
        session.save()
        self.assertEqual(self.rpc("tools/list", HTTP_AUTHORIZATION="").status_code, 401)

    def test_origins_hosts_methods_content_type_and_accept_are_checked(self):
        self.assertEqual(self.rpc("ping", HTTP_ORIGIN="https://evil.example").status_code, 403)
        self.assertEqual(self.rpc("ping", HTTP_ORIGIN="null").status_code, 403)
        self.assertEqual(self.rpc("ping", HTTP_ORIGIN="http://testserver").status_code, 200)
        self.assertEqual(self.rpc("ping", HTTP_HOST="evil.example").status_code, 400)
        self.assertEqual(self.rpc("ping", HTTP_HOST="evil.example", HTTP_ORIGIN="http://evil.example").status_code, 400)
        for method in ("get", "delete", "put"):
            response = getattr(self.client, method)(reverse("mcp"), **self.headers)
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "POST")
        self.assertEqual(self.client.post(reverse("mcp"), "{}", content_type="text/plain", **self.headers).status_code, 415)
        self.assertEqual(self.rpc("ping", HTTP_ACCEPT="application/json").status_code, 406)
        self.assertEqual(self.rpc("ping", HTTP_MCP_PROTOCOL_VERSION="unknown").status_code, 400)

    def test_initialization_discovery_ping_and_notifications(self):
        params = {"protocolVersion": "2025-11-25", "capabilities": {},
                  "clientInfo": {"name": "test", "version": "1"}}
        result = self.rpc("initialize", params).json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-11-25")
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        params["protocolVersion"] = "future-version"
        self.assertEqual(self.rpc("initialize", params).json()["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(self.rpc("initialize", {}).json()["error"]["code"], -32602)
        self.assertEqual(self.rpc("ping").json()["result"], {})
        tools = self.rpc("tools/list").json()["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["check_existing_cards"])
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
        notification = self.client.post(reverse("mcp"), json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                                        content_type="application/json", **self.headers)
        self.assertEqual(notification.status_code, 202)
        self.assertEqual(notification.content, b"")

    def test_matches_include_learned_cards_without_exposing_private_card_details_or_writing(self):
        Word.objects.create(chunk="Take your time", meaning="private reverse", example="private extra")
        Word.objects.create(chunk="hello", archived_at=timezone.now(), stage=5)
        Word.objects.create(chunk="unrequested private card")
        phrases = [" TAKE  YOUR\u00a0TIME ", "ＨＥＬＬＯ", "new phrase", "a paraphrase"]
        before = list(Word.objects.values())
        with self.assertNumQueries(1):
            response = self.call(phrases)
        result = response.json()["result"]
        data = result["structuredContent"]
        self.assertEqual(data, {"matches": [
            {"phrase": phrase, "exists": exists}
            for phrase, exists in zip(phrases, [True, True, False, False])
        ], "includes_learned": True})
        self.assertEqual(json.loads(result["content"][0]["text"]), data)
        for private in ("private reverse", "private extra", "unrequested private card", TEST_TOKEN):
            self.assertNotContains(response, private)
        self.assertEqual(list(Word.objects.values()), before)
        for model in (WordImport, StudySession, StudyReview):
            self.assertEqual(model.objects.count(), 0)
        self.assertEqual(self.call(phrases).json()["result"], result)

    def test_input_bounds_and_errors(self):
        for phrases in ([], [""], ["   "], [None], [1], "hello", ["x" * 301], ["hello"] * 101):
            with self.subTest(phrases=str(phrases)[:40]), self.assertNumQueries(0):
                self.assertEqual(self.call(phrases).json()["error"]["code"], -32602)
        self.assertEqual(self.call(["x" * 300] * 100).status_code, 200)
        self.assertEqual(self.rpc("tools/call", {"name": "delete_card"}).json()["error"]["code"], -32602)
        self.assertEqual(self.rpc("tools/call", {"name": "check_existing_cards", "arguments": {
            "phrases": ["hello"], "sql": "SELECT * FROM cards_word"}}).json()["error"]["code"], -32602)
        self.assertEqual(self.rpc("unknown").json()["error"]["code"], -32601)
        self.assertEqual(self.rpc("ping", []).json()["error"]["code"], -32602)
        for payload, code in (("broken JSON", -32700), ("[]", -32600),
                              ('{"jsonrpc":"2.0","id":true,"method":"ping"}', -32600)):
            response = self.client.post(reverse("mcp"), payload, content_type="application/json", **self.headers)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], code)
        response = self.client.post(reverse("mcp"), "x" * (MAX_BYTES + 1),
                                    content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 413)

    def test_database_failure_cannot_be_mistaken_for_no_duplicates(self):
        with patch("cards.mcp.existing_obverses", side_effect=DatabaseError("private database password")):
            response = self.call(["hello"])
        self.assertTrue(response.json()["result"]["isError"])
        self.assertNotContains(response, "private database password")
        self.assertNotIn("structuredContent", response.json()["result"])

    def test_rotating_the_token_revokes_previous_credentials(self):
        with override_settings(CARDY_MCP_TOKEN_SHA256=hashlib.sha256(b"another-disposable-test-credential-123").hexdigest()):
            self.assertEqual(self.rpc("tools/list").status_code, 401)
