"""Optional real-client transport check; see MCP.md for isolated SDK setup."""
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Word
from .test_mcp import TEST_TOKEN, TOKEN_HASH
from .test_mcp_write import WRITE_TOKEN, WRITE_HASH
import uuid

try:
    import anyio
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


@skipUnless(SDK_AVAILABLE, "Optional official MCP client is not installed; see MCP.md")
@override_settings(CARDY_MCP_TOKEN_SHA256=TOKEN_HASH)
class McpClientTests(LiveServerTestCase):
    def test_official_client_initializes_discovers_and_checks_phrases(self):
        Word.objects.create(chunk="Take your time", stage=5, archived_at=timezone.now())

        async def exercise():
            with anyio.fail_after(20):
                async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TEST_TOKEN}"},
                                             timeout=10, trust_env=False) as http_client:
                    async with streamable_http_client(self.live_server_url + reverse("mcp"),
                                                      http_client=http_client) as (reader, writer, _):
                        async with ClientSession(reader, writer) as session:
                            initialized = await session.initialize()
                            self.assertEqual(initialized.serverInfo.name, "cardy")
                            tools = await session.list_tools()
                            self.assertEqual([tool.name for tool in tools.tools], ["check_existing_cards"])
                            self.assertTrue(tools.tools[0].annotations.readOnlyHint)
                            result = await session.call_tool("check_existing_cards", {
                                "phrases": [" TAKE   YOUR TIME ", "new phrase"],
                            })
                            self.assertFalse(result.isError)
                            self.assertEqual(result.structuredContent, {"matches": [
                                {"phrase": " TAKE   YOUR TIME ", "exists": True},
                                {"phrase": "new phrase", "exists": False},
                            ], "includes_learned": True})
        anyio.run(exercise)
        self.assertEqual(Word.objects.count(), 1)

    @override_settings(CARDY_MCP_WRITE_TOKEN_SHA256=WRITE_HASH)
    def test_official_client_adds_an_approved_disposable_batch_once(self):
        async def exercise():
            with anyio.fail_after(20):
                async with httpx.AsyncClient(headers={"Authorization": f"Bearer {WRITE_TOKEN}"},
                                             timeout=10, trust_env=False) as http_client:
                    async with streamable_http_client(self.live_server_url + reverse("mcp"),
                                                      http_client=http_client) as (reader, writer, _):
                        async with ClientSession(reader, writer) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            self.assertEqual([tool.name for tool in tools.tools], ["add_cards"])
                            args = {"batch_id": str(uuid.uuid4()), "approved": True, "cards": [
                                {"obverse": "disposable client test", "reverse": "example", "extra": ""},
                            ]}
                            first = await session.call_tool("add_cards", args)
                            self.assertFalse(first.isError)
                            self.assertEqual(first.structuredContent["added_count"], 1)
                            replay = await session.call_tool("add_cards", args)
                            self.assertFalse(replay.isError)
                            self.assertTrue(replay.structuredContent["replayed"])
        anyio.run(exercise)
        self.assertEqual(Word.objects.count(), 1)
