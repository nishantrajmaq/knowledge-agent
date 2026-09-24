"""Inspect a Foundry project: connections, agents, and each version's configuration.

Shows whether the hosted agent and the knowledge source live in the same project, and
prints the exact ${{connections....}} placeholder to use for a connection's key.

    python check_foundry.py

Reads FOUNDRY_PROJECT_ENDPOINT (or AZURE_OPENAI_ENDPOINT) from .env.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or os.environ.get(
        "AZURE_OPENAI_ENDPOINT", ""
    )
    if not endpoint:
        print("Set FOUNDRY_PROJECT_ENDPOINT or AZURE_OPENAI_ENDPOINT.", file=sys.stderr)
        return 1

    print(f"project: {endpoint}\n")
    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    print("=== CONNECTIONS (the knowledge source must be here) ===")
    found_any = False
    for conn in project.connections.list():
        found_any = True
        name = getattr(conn, "name", "?")
        ctype = getattr(conn, "type", None) or getattr(conn, "category", "?")
        target = getattr(conn, "target", "")
        print(f"  {name:<35} {str(ctype):<22} {target}")
        # ApiKey-category connections always expose the secret as credentials.key
        if "search" in str(ctype).lower() or "search" in str(target).lower():
            print(f"      -> AZURE_SEARCH_API_KEY=${{{{connections.{name}.credentials.key}}}}")
    if not found_any:
        print("  (none — the knowledge source is not connected to this project)")

    print("\n=== AGENTS ===")
    try:
        for agent in project.agents.list():
            agent_name = getattr(agent, "name", "?")
            print(f"  {agent_name}")
            for version in project.agents.list_versions(agent_name=agent_name):
                vnum = version.get("version") if isinstance(version, dict) else version.version
                status = version.get("status") if isinstance(version, dict) else version.status
                print(f"    version {vnum}: {status}")
                detail = project.agents.get_version(agent_name=agent_name, agent_version=vnum)
                definition = detail.get("definition", {}) if isinstance(detail, dict) else {}
                env_vars = definition.get("environment_variables") or {}
                print(f"      env: {sorted(env_vars) or '(none set)'}")
                if isinstance(detail, dict) and detail.get("error"):
                    print(f"      error: {detail['error']}")
    except Exception as exc:  # noqa: BLE001 - surface whatever the service said
        print(f"  could not list agents: {type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
