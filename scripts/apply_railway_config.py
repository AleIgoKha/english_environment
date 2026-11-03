"""Apply the non-secret service settings without legacy Config as Code files."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, help="Path to Railway CLI v5")
    parser.add_argument("--project", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--environment", default="production")
    arguments = parser.parse_args()
    profile = json.loads((Path(__file__).parent / "railway_service.json").read_text(encoding="utf-8"))
    command = [arguments.cli, "environment", "edit", "--project", arguments.project,
               "--environment", arguments.environment, "--message", "Configure Django production deployment", "--json"]
    for key, value in profile.items():
        encoded = value if isinstance(value, str) else json.dumps(value)
        command += ["--service-config", arguments.service, key, encoded]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        # Profile values contain no secrets; avoid echoing unrelated server state.
        raise SystemExit("Could not apply the Railway service settings. Inspect this service's configuration in Railway.")
    print("Applied Dockerfile build, pre-deploy migrations, startup, readiness, and restart settings to the selected service.")


if __name__ == "__main__":
    main()
