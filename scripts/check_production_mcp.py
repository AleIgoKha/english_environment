"""Verify production MCP authentication and discovery without invoking writes."""
from pathlib import Path
import site


def main():
    root = Path(__file__).resolve().parent.parent
    site.addsitedir(str(root / ".local" / "mcp-test-deps"))
    import anyio
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = "https://english-environment-production.up.railway.app/mcp/"

    async def exercise():
        with anyio.fail_after(45):
            async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
                headers = {"Accept": "application/json, text/event-stream"}
                metadata = await client.get(url.replace("/mcp/", "/.well-known/oauth-protected-resource"))
                assert metadata.status_code == 200 and metadata.json()["resource"] == url
                anonymous = await client.post(url, headers=headers,
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                assert anonymous.status_code == 200
                assert {tool["name"] for tool in anonymous.json()["result"]["tools"]} == {"check_existing_cards", "add_cards"}
                denied = await client.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 2,
                    "method": "tools/call", "params": {"name": "check_existing_cards", "arguments": {"phrases": ["hello"]}}})
                assert denied.json()["result"]["isError"]
                assert "mcp/www_authenticate" in denied.json()["result"]["_meta"]
                wrong = await client.post(url, headers={"Authorization": "Bearer disposable-invalid-production-check-credential"},
                                          json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                assert wrong.status_code == 401, "Production MCP must reject incorrect credentials."
                health = await client.get("https://english-environment-production.up.railway.app/health/")
                assert health.status_code == 200 and health.json() == {"status": "ok"}
            for role, tool in (("read", "check_existing_cards"), ("write", "add_cards")):
                token = (root / ".local" / f"mcp-production-{role}.token").read_text().strip()
                async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=15,
                                             trust_env=False, follow_redirects=False) as client:
                    async with streamable_http_client(url, http_client=client) as (reader, writer, _):
                        async with ClientSession(reader, writer) as session:
                            await session.initialize()
                            inventory = await session.list_tools()
                            assert [entry.name for entry in inventory.tools] == [tool]
                            if role == "read":
                                result = await session.call_tool(tool, {"phrases": ["with a focus on clarity", "take your time"]})
                                assert not result.isError and result.structuredContent["includes_learned"]
                                assert len(result.structuredContent["matches"]) == 2
                                assert all(isinstance(match["exists"], bool) for match in result.structuredContent["matches"])
                                print("Production: authenticated duplicate lookup verified.")
                            else:
                                assert not inventory.tools[0].annotations.readOnlyHint
                                print("Production: separate Add cards connection verified by discovery; no write invoked.")
            print("Production: health, OAuth discovery, public catalog, and rejection of anonymous data access/incorrect credentials verified.")
    anyio.run(exercise)


if __name__ == "__main__":
    main()
