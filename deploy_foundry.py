"""Deploy this agent to Microsoft Foundry as a hosted agent.

Default mode packages the source as a .zip and lets Foundry build it, which needs no
container registry, no Docker, and no ACR Tasks -- only the Foundry Project Manager role.
Pass --image to register a prebuilt container image instead.

    python deploy_foundry.py
    python deploy_foundry.py --image acritronekg.azurecr.io/knowledge-agent-foundry:v1

Configuration is read from .env, so there is nothing to export first.
"""

import argparse
import hashlib
import io
import os
import sys
import time
import zipfile
from pathlib import Path

from dotenv import load_dotenv

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointProtocol,
    CodeConfiguration,
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.identity import DefaultAzureCredential

ROOT = Path(__file__).parent

# The zip must be flat at the root: the entry point sits at the top level, not inside a
# wrapper folder. app/ is a package beside it, which is fine.
ENTRY_POINT = "host.py"

# main.py and bot.py serve the Copilot bot channel and import the Agents SDK, which is not
# in requirements-foundry.txt. Excluding them keeps an accidental import from turning into
# a ModuleNotFoundError at session start.
EXCLUDED_APP_MODULES = {"main.py", "bot.py"}


def build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / ENTRY_POINT, ENTRY_POINT)
        # remote_build installs from requirements.txt, so the slim list is written there.
        z.writestr("requirements.txt", (ROOT / "requirements-foundry.txt").read_text())
        for path in sorted((ROOT / "app").glob("*.py")):
            if path.name in EXCLUDED_APP_MODULES:
                continue
            z.write(path, f"app/{path.name}")
    return buf.getvalue()


def _definition(args, env_vars: dict[str, str]) -> HostedAgentDefinition:
    common = dict(
        cpu=args.cpu,
        memory=args.memory,
        protocol_versions=[
            ProtocolVersionRecord(protocol=AgentEndpointProtocol.RESPONSES, version="2.0.0")
        ],
        environment_variables=env_vars,
    )
    if args.image:
        return HostedAgentDefinition(
            container_configuration=ContainerConfiguration(image=args.image), **common
        )
    return HostedAgentDefinition(
        code_configuration=CodeConfiguration(
            runtime=args.runtime,
            entry_point=["python", ENTRY_POINT],
            dependency_resolution="remote_build",
        ),
        **common,
    )


def main() -> int:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="knowledge-agent")
    parser.add_argument("--image", help="Deploy this prebuilt image instead of source")
    parser.add_argument("--runtime", default="python_3_13")
    parser.add_argument("--cpu", default="1")
    parser.add_argument("--memory", default="2Gi")
    parser.add_argument("--save-zip", help="Write the packaged zip here and exit")
    args = parser.parse_args()

    if args.save_zip:
        Path(args.save_zip).write_bytes(build_zip())
        print(f"wrote {args.save_zip}")
        return 0

    # A Foundry project endpoint and its OpenAI-compatible chat endpoint are the same URL,
    # so fall back to the AZURE_OPENAI_* values rather than requiring them twice in .env.
    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or os.environ.get(
        "AZURE_OPENAI_ENDPOINT", ""
    )
    model_deployment = os.environ.get("MODEL_DEPLOYMENT_NAME") or os.environ.get(
        "AZURE_OPENAI_DEPLOYMENT_NAME", ""
    )
    if not project_endpoint or not model_deployment:
        print(
            "Set FOUNDRY_PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME (or the AZURE_OPENAI_\n"
            "equivalents) in .env or the environment.",
            file=sys.stderr,
        )
        return 1

    print(f"project: {project_endpoint}")
    print(f"model:   {model_deployment}")

    # Prefer a connection placeholder over the literal key. Foundry resolves it at session
    # start, so the secret is never stored on the agent version -- while .env keeps the real
    # key for local runs, where there is nothing to resolve a placeholder.
    #
    #   AZURE_SEARCH_API_KEY_REF=${{connections.<connection-name>.credentials.key}}
    #
    # Run check_foundry.py to list the project's connections and print this value.
    search_key = os.environ.get("AZURE_SEARCH_API_KEY_REF") or os.environ["AZURE_SEARCH_API_KEY"]
    if search_key.startswith("${{"):
        print(f"search key: {search_key} (resolved by Foundry at session start)")
    else:
        print("search key: literal value from .env -- prefer AZURE_SEARCH_API_KEY_REF")

    env_vars = {
        "MODEL_DEPLOYMENT_NAME": model_deployment,
        "AZURE_SEARCH_ENDPOINT": os.environ["AZURE_SEARCH_ENDPOINT"],
        "AZURE_SEARCH_API_KEY": search_key,
        "AZURE_SEARCH_INDEX_NAME": os.environ["AZURE_SEARCH_INDEX_NAME"],
        "AZURE_SEARCH_CONTENT_FIELD": os.environ.get("AZURE_SEARCH_CONTENT_FIELD", "snippet"),
        "AZURE_SEARCH_SOURCE_FIELD": os.environ.get("AZURE_SEARCH_SOURCE_FIELD", "blob_url"),
    }

    project = AIProjectClient(
        endpoint=project_endpoint, credential=DefaultAzureCredential()
    )
    definition = _definition(args, env_vars)

    if args.image:
        print(f"registering image {args.image}")
        created = project.agents.create_version(agent_name=args.name, definition=definition)
    else:
        payload = build_zip()
        digest = hashlib.sha256(payload).hexdigest()
        print(f"uploading source zip ({len(payload) / 1024:.1f} KiB, sha256 {digest[:12]}...)")
        # The SDK reads the multipart filename off the stream's .name, and the service
        # rejects anything not ending in .zip.
        stream = io.BytesIO(payload)
        stream.name = f"{args.name}.zip"
        created = project.agents.create_version_from_code(
            agent_name=args.name,
            definition=definition,
            code=stream,
            code_zip_sha256=digest,
        )

    print(f"created version {created.version}; waiting for active")
    while True:
        info = project.agents.get_version(agent_name=args.name, agent_version=created.version)
        status = info["status"]
        print(f"  status: {status}")
        if status == "active":
            print(f"\nready. invoke with agent_name={args.name!r}")
            return 0
        if status == "failed":
            # For remote_build the container never starts, so the error object -- not the
            # log stream -- carries the pip failure.
            print(f"\nprovisioning failed: {info.get('error')}", file=sys.stderr)
            return 1
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
