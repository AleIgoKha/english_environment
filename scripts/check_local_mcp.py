"""Verify configured local connections without invoking any write tool."""
from pathlib import Path
import site
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    site.addsitedir(str(root / ".local" / "mcp-test-deps"))
    import anyio
    import httpx
    from dotenv import dotenv_values
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    values = dotenv_values(root / ".env")
    if values.get("DJANGO_LOCAL_TEST_LOGIN", "").lower() != "true" or values.get("DATABASE_URL"):
        raise SystemExit("This checker is only for the local test deck.")
    url = values["LOCAL_DEV_URL"].rstrip("/") + "/mcp/"

    async def exercise():
        with anyio.fail_after(30):
            for role, expected in (("read", "check_existing_cards"), ("write", "add_cards")):
                token = (root / ".local" / f"mcp-{role}.token").read_text().strip()
                async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                             timeout=10, trust_env=False) as client:
                    async with streamable_http_client(url, http_client=client) as (reader, writer, _):
                        async with ClientSession(reader, writer) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            assert [tool.name for tool in tools.tools] == [expected]
                            if role == "read":
                                result = await session.call_tool(expected, {"phrases": ["with a focus on clarity"]})
                                assert not result.isError
                                assert isinstance(result.structuredContent["matches"][0]["exists"], bool)
                                print("Local test deck: read-only duplicate lookup verified.")
                            else:
                                print("Local test deck: separate Add cards tool discovered; no write invoked.")
    anyio.run(exercise)


if __name__ == "__main__":
    main()
