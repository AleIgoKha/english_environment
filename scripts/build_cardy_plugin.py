"""Build a private plugin from an explicit allowlist; never package credentials."""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    root = Path(__file__).resolve().parent.parent
    source = root / "integrations"
    plugin = source / "cardy-plugin"
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "cardy"
    assert len(manifest["extensions"]["com.openai"]["interface"]["shortDescription"]) <= 30
    mcp = json.loads((plugin / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["cardy"]["extensions"]["com.openai"]["auth"]["type"] == "oauth"
    files = {
        "plugin.json": plugin / "plugin.json",
        "mcp.json": plugin / "mcp.json",
        "assets/icon.svg": root / "cards" / "static" / "cards" / "icon.svg",
    }
    for name in ("cardy-daily-writing", "cardy-add-cards"):
        files[f"skills/{name}/SKILL.md"] = source / name / "SKILL.md"
    destination = root / ".local" / f"cardy-plugin-{manifest['version']}.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for relative, path in files.items():
            if path.is_symlink():
                raise ValueError("Plugin source must not be a symlink.")
            archive.write(path, "cardy/" + relative)
    with ZipFile(destination) as archive:
        assert set(archive.namelist()) == {"cardy/" + name for name in files}
        assert archive.testzip() is None
    print(f"Built private plugin: {destination} ({len(files)} files; no credential files)")


if __name__ == "__main__":
    main()
