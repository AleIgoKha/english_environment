"""Private production header helper for Codex; never display its output."""
import json
from pathlib import Path
import sys


def main():
    if sys.argv[1:] not in ([], ["--write"]):
        raise SystemExit("Unsupported credential role.")
    role = "write" if sys.argv[1:] else "read"
    path = Path(__file__).resolve().parent.parent / ".local" / f"mcp-production-{role}.token"
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32 or "\n" in token or "\r" in token:
        raise SystemExit("Production MCP credential is unavailable.")
    print(json.dumps({"Authorization": f"Bearer {token}"}))


if __name__ == "__main__":
    main()
