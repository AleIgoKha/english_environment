"""Apply and verify one service's settings using the existing Railway login."""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


def railway_api(query, variables):
    config = json.loads((Path.home() / ".railway" / "config.json").read_text(encoding="utf-8"))
    token = config.get("user", {}).get("token")
    if not token:
        raise SystemExit("Sign in with Railway CLI first. No settings were changed.")
    request = urllib.request.Request(
        "https://backboard.railway.com/graphql/v2",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "railway-cli/5.71.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        print(f"Railway API status: HTTP {error.code}.")
        raise SystemExit("Railway API request failed; credential values were not printed.") from None
    except (urllib.error.URLError, ValueError):
        raise SystemExit("Railway API request failed; credentials and response details were not printed.") from None
    if result.get("errors"):
        for error in result["errors"]:
            print("Railway:", str(error.get("message", "Request rejected")).replace(token, "[redacted]")[:300])
        raise SystemExit("Railway rejected the configuration request. No credential values were printed.")
    return result["data"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--environment", required=True, help="Exact Railway environment ID")
    parser.add_argument("--repository", help="Optional owner/repo for the native GitHub push trigger")
    parser.add_argument("--branch", default="main")
    arguments = parser.parse_args()
    profile = json.loads((Path(__file__).parent / "railway_service.json").read_text(encoding="utf-8"))
    # Use IDs for both selectors to avoid any ambiguous project/environment name.
    print("Verifying the selected service belongs to the project.")
    service = railway_api("query($id: String!) { service(id: $id) { projectId } }", {"id": arguments.service})["service"]
    if service["projectId"] != arguments.project:
        raise SystemExit("Service does not belong to the selected project; no settings were changed.")
    expected = {key.split(".", 1)[1]: value for key, value in profile.items()}
    variables = {"serviceId": arguments.service, "environmentId": arguments.environment}
    mutation = "mutation($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) { serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: $input) }"
    print("Applying the selected service's public deployment settings.")
    railway_api(mutation, {**variables, "input": expected})
    fields = " ".join(expected)
    query = "query($serviceId: String!, $environmentId: String!) { serviceInstance(serviceId: $serviceId, environmentId: $environmentId) { " + fields + " } }"
    print("Reading back the service settings.")
    actual = railway_api(query, variables)["serviceInstance"]
    if any(actual.get(key) != value for key, value in expected.items()):
        raise SystemExit("Railway's saved configuration does not match the requested profile. Check the selected service before deploying.")
    print("Applied and verified Dockerfile build, pre-deploy migrations, startup, readiness, and restart settings for the selected service.")
    if arguments.repository:
        print("Checking the requested repository's push trigger.")
        query = "query($id: String!) { service(id: $id) { repoTriggers { edges { node { id branch repository environmentId } } } } }"
        edges = railway_api(query, {"id": arguments.service})["service"]["repoTriggers"]["edges"]
        current = [edge["node"] for edge in edges if edge["node"]["environmentId"] == arguments.environment]
        if current:
            if not any(trigger["repository"] == arguments.repository and trigger["branch"] == arguments.branch for trigger in current):
                raise SystemExit("An unexpected repository trigger already exists; no triggers were changed.")
            print("Verified the existing native GitHub push trigger.")
        else:
            print("Creating the requested repository's push trigger.")
            mutation = "mutation($input: DeploymentTriggerCreateInput!) { deploymentTriggerCreate(input: $input) { id branch repository environmentId } }"
            trigger = railway_api(mutation, {"input": {
                "projectId": arguments.project, "environmentId": arguments.environment,
                "serviceId": arguments.service, "provider": "github",
                "repository": arguments.repository, "branch": arguments.branch,
                "checkSuites": False,
            }})["deploymentTriggerCreate"]
            if trigger["repository"] != arguments.repository or trigger["branch"] != arguments.branch or trigger["environmentId"] != arguments.environment:
                raise SystemExit("The created GitHub trigger does not match the requested repository/branch.")
            print("Created the native GitHub push trigger for the requested repository and branch.")


if __name__ == "__main__":
    main()
