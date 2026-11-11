"""Run official-client interoperability against a disposable Django test server."""
import os
from pathlib import Path
import site
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    dependencies = root / ".local" / "mcp-test-deps"
    if not dependencies.is_dir():
        raise SystemExit("Install requirements-mcp-test.txt into .local/mcp-test-deps first; see MCP.md.")
    # Process package .pth files too (required by the SDK's pywin32 dependency).
    site.addsitedir(str(dependencies))
    import mcp  # noqa: F401 -- fail explicitly rather than silently skipping verification
    sys.path.insert(0, str(root))
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.test_settings"
    from django.core.management import execute_from_command_line
    execute_from_command_line([sys.argv[0], "test", "cards.test_mcp_client", "--settings=config.test_settings"])


if __name__ == "__main__":
    main()
