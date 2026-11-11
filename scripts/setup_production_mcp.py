"""Configure only Cardy's production MCP digests and private client connections."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tomllib

from apply_railway_config import railway_api

PROJECT = "01debade-17e6-4ad3-a9dd-ddbd14373e24"
SERVICE = "8e816988-cec0-4d63-89d5-ecd745a0ba81"
ENVIRONMENT = "550023b3-036b-4e9d-aca5-aa1c32478ac4"
URL = "https://english-environment-production.up.railway.app/mcp/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-railway", action="store_true")
    parser.add_argument("--configure-codex", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    config_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    source = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    current = tomllib.loads(source)
    blocks = []
    for role, name, tool in (("read", "cardy", "check_existing_cards"), ("write", "cardy-add", "add_cards")):
        command = [sys.executable, str(root / "scripts" / "mcp_production_headers.py")]
        if role == "write":
            command.append("--write")
        helper = subprocess.list2cmdline(command)
        expected = {"url": URL, "http_headers_helper": helper, "startup_timeout_sec": 30,
                    "tool_timeout_sec": 30, "enabled_tools": [tool],
                    "default_tools_approval_mode": "auto" if role == "read" else "prompt"}
        entry = current.get("mcp_servers", {}).get(name)
        if entry and any(entry.get(key) != value for key, value in expected.items()):
            raise SystemExit(f"Existing {name} settings differ. No connection settings were replaced.")
        if not entry:
            blocks.append(f"\n[mcp_servers.{name}]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in expected.items()))
    updated = source.rstrip() + "\n" + "".join(blocks)
    tomllib.loads(updated)
    private = root / ".local"
    private.mkdir(exist_ok=True)
    digests = {}
    for role, key in (("read", "CARDY_MCP_TOKEN_SHA256"), ("write", "CARDY_MCP_WRITE_TOKEN_SHA256")):
        path = private / f"mcp-production-{role}.token"
        token = path.read_text(encoding="utf-8").strip() if path.exists() else secrets.token_urlsafe(32)
        if len(token) < 32 or "\n" in token or "\r" in token:
            raise SystemExit("Existing production credential is invalid; nothing was sent.")
        path.write_text(token, encoding="utf-8")
        digests[key] = hashlib.sha256(token.encode()).hexdigest()
    if len(set(digests.values())) != 2:
        raise SystemExit("Read and write credentials must be different; nothing was sent.")
    print("Separate production MCP credentials prepared privately; local test credentials unchanged.")
    if args.apply_railway:
        actual = railway_api("query($id: String!) { service(id: $id) { projectId } }", {"id": SERVICE})
        if actual["service"]["projectId"] != PROJECT:
            raise SystemExit("Unexpected Railway service project. No variables were changed.")
        cli = shutil.which("railway")
        if not cli:
            raise SystemExit("Railway CLI is required.")
        command = [cli, "variables", "--service", SERVICE, "--environment", ENVIRONMENT, "--skip-deploys"]
        for key, value in digests.items():
            command.extend(["--set", f"{key}={value}"])
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise SystemExit("Could not apply the two MCP digests. Railway output was suppressed to protect private settings.")
        print("Configured only the two MCP digests on the verified Cardy production service; no deployment triggered.")
    if args.configure_codex:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(updated, encoding="utf-8")
        print("Configured production connections: cardy (read-only), cardy-add (approval required).")


if __name__ == "__main__":
    main()
