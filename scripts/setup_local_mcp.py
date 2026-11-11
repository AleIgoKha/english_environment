"""Configure only the separate local test deck; never prints credentials."""
import argparse
import hashlib
from ipaddress import ip_address, ip_network
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure-codex", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    values = dotenv_values(env_path)
    if (values.get("DJANGO_DEBUG", "").lower() != "true"
            or values.get("DJANGO_LOCAL_TEST_LOGIN", "").lower() != "true"
            or values.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
            or values.get("RAILWAY_ENVIRONMENT_ID") or os.environ.get("RAILWAY_ENVIRONMENT_ID")):
        raise SystemExit("This setup requires the separate local test SQLite deck, debug mode, and no Railway environment.")
    codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config_path = codex_dir / "config.toml"
    source = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config = tomllib.loads(source)
    local_url = values.get("LOCAL_DEV_URL", "")
    parsed = urlsplit(local_url)
    try:
        network = ip_network(values.get("LOCAL_TEST_NETWORK", ""), strict=True)
        address = ip_address(parsed.hostname or "")
        valid_local = address.is_private and address in network and network.is_private
    except ValueError:
        valid_local = False
    if (not valid_local or parsed.scheme != "http" or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise SystemExit("LOCAL_DEV_URL must be the saved private Wi-Fi address in LOCAL_TEST_NETWORK.")
    entries = []
    for role, name in (("read", "cardy-local"), ("write", "cardy-local-add")):
        helper = [sys.executable, str(root / "scripts" / "mcp_local_headers.py")]
        if role == "write":
            helper.append("--write")
        command = subprocess.list2cmdline(helper)
        entry = config.get("mcp_servers", {}).get(name)
        url = local_url.rstrip("/") + "/mcp/"
        if entry and (entry.get("url") != url or entry.get("http_headers_helper") != command):
            raise SystemExit(f"Existing {name} settings differ. No connection settings were replaced.")
        if not entry:
            block = f"\n[mcp_servers.{name}]\nurl = {json.dumps(url)}\nhttp_headers_helper = {json.dumps(command)}\n"
            block += 'startup_timeout_sec = 20\ntool_timeout_sec = 30\n'
            block += 'default_tools_approval_mode = "auto"\n' if role == "read" else 'default_tools_approval_mode = "prompt"\n'
            block += 'enabled_tools = ["check_existing_cards"]\n' if role == "read" else 'enabled_tools = ["add_cards"]\n'
            entries.append(block)
    private = root / ".local"
    private.mkdir(exist_ok=True)
    for role, setting in (("read", "CARDY_MCP_TOKEN_SHA256"), ("write", "CARDY_MCP_WRITE_TOKEN_SHA256")):
        token_path = private / f"mcp-{role}.token"
        token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        previous = values.get(setting, "")
        if previous and previous != digest:
            raise SystemExit(f"Existing {setting} does not match local credentials; refusing to rotate it.")
        token_path.write_text(token, encoding="utf-8")
        set_key(env_path, setting, digest)
    print("Local test-deck MCP credentials configured privately.")
    if args.configure_codex:
        updated = source.rstrip() + "\n" + "".join(entries)
        tomllib.loads(updated)
        codex_dir.mkdir(exist_ok=True)
        config_path.write_text(updated, encoding="utf-8")
        print("Codex connections configured: cardy-local (read-only), cardy-local-add (approval required).")


if __name__ == "__main__":
    main()
