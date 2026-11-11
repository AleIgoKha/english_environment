"""Codex-only secret header helper. Never run this to display its output."""
import json
from pathlib import Path
import sys


def main():
    role = "write" if sys.argv[1:] == ["--write"] else "read"
    if sys.argv[1:] not in ([], ["--write"]):
        raise SystemExit("Unsupported credential role.")
    path = Path(__file__).resolve().parent.parent / ".local" / f"mcp-{role}.token"
    token = path.read_text(encoding="utf-8").strip()
    if not token or "\n" in token or "\r" in token:
        raise SystemExit("Local MCP credential is unavailable.")
    print(json.dumps({"Authorization": f"Bearer {token}"}))


if __name__ == "__main__":
    main()
