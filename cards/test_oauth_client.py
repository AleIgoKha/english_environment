"""Full OAuth discovery, DCR, PKCE, consent, and MCP call with the official SDK."""
import re
from unittest import skipUnless
from urllib.parse import parse_qs, urlsplit

from django.core.asgi import get_asgi_application
from django.test import Client, TransactionTestCase, override_settings

from .models import Word
from .security import auth_version
from .test_mcp_client import SDK_AVAILABLE
from .tests import GOOGLE_SETTINGS

if SDK_AVAILABLE:
    import anyio
    import httpx
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.auth import OAuthClientMetadata


@skipUnless(SDK_AVAILABLE, "Optional official MCP client is not installed; see MCP.md")
@override_settings(**GOOGLE_SETTINGS, CARDY_OAUTH_ENABLED=True, CARDY_PUBLIC_URL="https://testserver")
class OAuthClientTests(TransactionTestCase):
    def test_official_sdk_completes_oauth_and_reads_the_owner_deck(self):
        from .models import LearningSettings
        LearningSettings.objects.get_or_create(pk=1)
        Word.objects.create(chunk="hello")
        browser = Client()
        session = browser.session
        session.update({"auth_kind": "google", "google_email": "owner@example.test", "google_subject": "sdk-owner",
                        "auth_version": auth_version()})
        session.save()
        cookies = {key: morsel.value for key, morsel in browser.cookies.items()}
        app = get_asgi_application()

        class Storage:
            tokens = None
            client_info = None

            async def get_tokens(self): return self.tokens
            async def set_tokens(self, value): self.tokens = value
            async def get_client_info(self): return self.client_info
            async def set_client_info(self, value): self.client_info = value

        async def exercise():
            callback = {}
            async def redirect_handler(url):
                # Simulate an already signed-in owner approving only the connection.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), cookies=cookies) as owner_browser:
                    page = await owner_browser.get(url)
                    self.assertEqual(page.status_code, 200, page.text[:200])
                    nonce = re.search(r'name="consent_nonce" value="([^"]+)"', page.text).group(1)
                    consent = await owner_browser.post(url, data={"decision": "allow", "consent_nonce": nonce,
                        "csrfmiddlewaretoken": owner_browser.cookies.get("csrftoken")},
                        headers={"Origin": "https://testserver", "Referer": url})
                    self.assertEqual(consent.status_code, 302, consent.text[:200])
                    callback.update(parse_qs(urlsplit(consent.headers["Location"]).query))

            async def callback_handler():
                return callback["code"][0], callback["state"][0]

            storage = Storage()
            auth = OAuthClientProvider("https://testserver/mcp/", OAuthClientMetadata(
                client_name="Official MCP client", redirect_uris=["https://chatgpt.com/connector_platform_oauth_redirect"],
                token_endpoint_auth_method="none", scope="cards:read cards:add"), storage,
                redirect_handler=redirect_handler, callback_handler=callback_handler, timeout=20)
            with anyio.fail_after(30):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), auth=auth, timeout=20) as client:
                    # GET challenges trigger OAuth; successful authenticated GET is deliberately 405.
                    probe = await client.get("https://testserver/mcp/")
                    self.assertEqual(probe.status_code, 405, probe.text[:200])
                    self.assertIsNotNone(storage.tokens)
                    async with streamable_http_client("https://testserver/mcp/", http_client=client) as (reader, writer, _):
                        async with ClientSession(reader, writer) as connection:
                            await connection.initialize()
                            inventory = await connection.list_tools()
                            self.assertEqual([tool.name for tool in inventory.tools], ["check_existing_cards", "add_cards"])
                            result = await connection.call_tool("check_existing_cards", {"phrases": ["hello", "new"]})
                            self.assertFalse(result.isError)
                            self.assertEqual([match["exists"] for match in result.structuredContent["matches"]], [True, False])
        anyio.run(exercise)
        self.assertEqual(Word.objects.count(), 1)
