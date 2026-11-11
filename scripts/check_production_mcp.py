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
                anonymous = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                assert anonymous.status_code == 401, "Production MCP must reject anonymous calls."
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
            print("Production: health and rejection of anonymous/incorrect credentials verified.")
    anyio.run(exercise)


if __name__ == "__main__":
    main()
