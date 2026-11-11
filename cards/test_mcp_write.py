"""Separate write credentials, explicit approval flag, and retry-safe batches."""
import hashlib
import json
import uuid
from unittest.mock import patch

from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import McpCardBatch, Word
from .test_mcp import TEST_TOKEN, TOKEN_HASH

WRITE_TOKEN = "disposable-add-cards-test-token-not-a-real-secret"
WRITE_HASH = hashlib.sha256(WRITE_TOKEN.encode()).hexdigest()


@override_settings(CARDY_MCP_TOKEN_SHA256=TOKEN_HASH, CARDY_MCP_WRITE_TOKEN_SHA256=WRITE_HASH)
class McpWriteTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.arguments = {"batch_id": str(uuid.uuid4()), "approved": True, "cards": [
            {"obverse": "take your time", "reverse": "Previously: old wording\nBetter: Take your time.", "extra": ""},
        ]}

    def rpc(self, method="tools/call", params=None, token=WRITE_TOKEN):
        return self.client.post(reverse("mcp"), json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                "params": params if params is not None else {"name": "add_cards", "arguments": self.arguments}}),
                                content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}",
                                HTTP_ACCEPT="application/json, text/event-stream")

    def test_read_and_write_connections_have_separate_capabilities(self):
        for token, names in ((TEST_TOKEN, ["check_existing_cards"]), (WRITE_TOKEN, ["add_cards"])):
            tools = self.rpc("tools/list", {}, token).json()["result"]["tools"]
            self.assertEqual([tool["name"] for tool in tools], names)
            self.assertEqual(tools[0]["annotations"]["readOnlyHint"], token == TEST_TOKEN)
        self.assertIn("error", self.rpc(token=TEST_TOKEN).json())
        self.assertIn("error", self.rpc(params={"name": "check_existing_cards", "arguments": {"phrases": ["hello"]}}).json())
        self.assertFalse(Word.objects.exists())

    def test_approval_is_required_and_write_access_can_be_disabled(self):
        for approval in (False, "true", 1, None):
            self.arguments["approved"] = approval
            self.assertEqual(self.rpc().json()["error"]["code"], -32602)
        self.arguments.pop("approved")
        self.assertIn("error", self.rpc().json())
        self.assertFalse(Word.objects.exists())
        with override_settings(CARDY_MCP_WRITE_TOKEN_SHA256=""):
            self.assertEqual(self.rpc().status_code, 401)

    def test_add_preserves_existing_progress_and_skips_duplicates(self):
        existing = Word.objects.create(chunk="TAKE  YOUR TIME", stage=5, archived_at=timezone.now(), meaning="keep")
        self.arguments["cards"] += [
            {"obverse": "new phrase", "reverse": "first", "extra": ""},
            {"obverse": "NEW PHRASE", "reverse": "second", "extra": ""},
        ]
        data = self.rpc().json()["result"]["structuredContent"]
        self.assertEqual((data["added_count"], data["skipped_count"], data["replayed"]), (1, 2, False))
        existing.refresh_from_db()
        self.assertEqual((existing.stage, existing.meaning), (5, "keep"))
        added = Word.objects.get(chunk="new phrase")
        self.assertEqual(added.meaning, "first")
        self.assertFalse(added.show_reverse_immediately)
        self.assertEqual(added.stage, 0)

    def test_retry_after_edit_or_delete_does_not_add_again(self):
        first = self.rpc().json()["result"]["structuredContent"]
        Word.objects.all().delete()
        replay = self.rpc().json()["result"]["structuredContent"]
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["added_count"], first["added_count"])
        self.assertFalse(Word.objects.exists())
        self.arguments["cards"][0]["reverse"] = "changed after approval"
        self.assertIn("error", self.rpc().json())
        self.assertEqual(McpCardBatch.objects.count(), 1)

    def test_invalid_batch_is_rejected_without_partial_writes(self):
        for cards in ([], None, {}, [{"obverse": ""}],
                      [{"obverse": "valid", "reverse": "x" * 2001, "extra": ""}]):
            self.arguments["cards"] = cards
            self.assertIn("error", self.rpc().json())
        self.assertFalse(Word.objects.exists())
        self.assertFalse(McpCardBatch.objects.exists())

    def test_more_than_ten_cards_are_added_with_duplicate_checks_and_safe_retries(self):
        Word.objects.create(chunk="existing phrase", meaning="preserve")
        self.arguments["cards"] = [
            {"obverse": f"approved phrase {index}", "reverse": "Example", "extra": ""}
            for index in range(25)
        ] + [{"obverse": "EXISTING PHRASE", "reverse": "replacement", "extra": ""}]
        first = self.rpc().json()["result"]["structuredContent"]
        self.assertEqual((first["added_count"], first["skipped_count"]), (25, 1))
        self.assertEqual(Word.objects.count(), 26)
        self.assertEqual(Word.objects.get(chunk="existing phrase").meaning, "preserve")
        retry = self.rpc().json()["result"]["structuredContent"]
        self.assertEqual(retry, first | {"replayed": True})
        self.assertEqual(Word.objects.count(), 26)
        inventory = self.rpc("tools/list", {}).json()["result"]["tools"][0]
        self.assertEqual(inventory["inputSchema"]["properties"]["cards"]["maxItems"], 100)

    def test_one_hundred_cards_succeed_but_one_hundred_one_are_rejected_atomically(self):
        self.arguments["cards"] = [
            {"obverse": f"boundary phrase {index}", "reverse": "", "extra": ""}
            for index in range(100)
        ]
        result = self.rpc().json()["result"]["structuredContent"]
        self.assertEqual(result["added_count"], 100)
        self.assertEqual(Word.objects.count(), 100)
        self.arguments["batch_id"] = str(uuid.uuid4())
        self.arguments["cards"].append({"obverse": "must not be added", "reverse": "", "extra": ""})
        response = self.rpc().json()
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("more than 100", response["error"]["message"])
        self.assertEqual(Word.objects.count(), 100)
        self.assertFalse(Word.objects.filter(chunk="must not be added").exists())
        self.assertEqual(McpCardBatch.objects.count(), 1)

    def test_database_failure_rolls_back_and_retry_uses_same_batch(self):
        def fail_after_first(words):
            words[0].save()
            raise DatabaseError("private database details")
        with patch("cards.mcp_cards.Word.objects.bulk_create", side_effect=fail_after_first):
            response = self.rpc()
        self.assertTrue(response.json()["result"]["isError"])
        self.assertNotContains(response, "private database details")
        self.assertFalse(Word.objects.exists())
        self.assertFalse(McpCardBatch.objects.exists())
        self.assertEqual(self.rpc().json()["result"]["structuredContent"]["added_count"], 1)
