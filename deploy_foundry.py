"""Deploy this agent to Microsoft Foundry as a hosted agent.

Default mode packages the source as a .zip and lets Foundry build it, which needs no
container registry, no Docker, and no ACR Tasks -- only the Foundry Project Manager role.
Pass --image to register a prebuilt container image instead.

    python deploy_foundry.py
    python deploy_foundry.py --image acritronekg.azurecr.io/knowledge-agent-foundry:v1

Reads FOUNDRY_PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME and the AZURE_SEARCH_* values from
the environment.
"""

import argparse
import hashlib
import io
import os
import sys
import time
import zipfile
from pathlib import Path

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

    # Passed to the container as-is. Values may be ${{connections.<name>.credentials.key}}
    # placeholders, which Foundry resolves at session start so no key is stored here.
    env_vars = {
        "MODEL_DEPLOYMENT_NAME": os.environ["MODEL_DEPLOYMENT_NAME"],
        "AZURE_SEARCH_ENDPOINT": os.environ["AZURE_SEARCH_ENDPOINT"],
        "AZURE_SEARCH_API_KEY": os.environ["AZURE_SEARCH_API_KEY"],
        "AZURE_SEARCH_INDEX_NAME": os.environ["AZURE_SEARCH_INDEX_NAME"],
        "AZURE_SEARCH_CONTENT_FIELD": os.environ.get("AZURE_SEARCH_CONTENT_FIELD", "snippet"),
        "AZURE_SEARCH_SOURCE_FIELD": os.environ.get("AZURE_SEARCH_SOURCE_FIELD", "blob_url"),
    }

    project = AIProjectClient(
        endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
    )
    definition = _definition(args, env_vars)

    if args.image:
        print(f"registering image {args.image}")
        created = project.agents.create_version(agent_name=args.name, definition=definition)
    else:
        payload = build_zip()
        digest = hashlib.sha256(payload).hexdigest()
        print(f"uploading source zip ({len(payload) / 1024:.1f} KiB, sha256 {digest[:12]}...)")
        created = project.agents.create_version_from_code(
            agent_name=args.name,
            definition=definition,
            code=io.BytesIO(payload),
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
