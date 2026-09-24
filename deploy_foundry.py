"""Register a container image as a Foundry hosted agent version and wait for it.

Usage:
    python deploy_foundry.py --image acritronekg.azurecr.io/knowledge-agent:v1

Reads FOUNDRY_PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME from the environment.
Search credentials are passed through as environment variables on the agent version;
prefer a Foundry project connection placeholder over a literal key (see README).
"""

import argparse
import os
import sys
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointProtocol,
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.identity import DefaultAzureCredential


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Full ACR image URL with tag")
    parser.add_argument("--name", default="knowledge-agent")
    parser.add_argument("--cpu", default="1")
    parser.add_argument("--memory", default="2Gi")
    args = parser.parse_args()

    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model_deployment = os.environ["MODEL_DEPLOYMENT_NAME"]

    # Passed to the container as-is. Values may be ${{connections.<name>.credentials.key}}
    # placeholders, which Foundry resolves at sandbox start so no key is stored here.
    env_vars = {
        "MODEL_DEPLOYMENT_NAME": model_deployment,
        "AZURE_SEARCH_ENDPOINT": os.environ["AZURE_SEARCH_ENDPOINT"],
        "AZURE_SEARCH_API_KEY": os.environ["AZURE_SEARCH_API_KEY"],
        "AZURE_SEARCH_INDEX_NAME": os.environ["AZURE_SEARCH_INDEX_NAME"],
        "AZURE_SEARCH_CONTENT_FIELD": os.environ.get("AZURE_SEARCH_CONTENT_FIELD", "snippet"),
        "AZURE_SEARCH_SOURCE_FIELD": os.environ.get("AZURE_SEARCH_SOURCE_FIELD", "blob_url"),
    }

    project = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())

    print(f"creating version of {args.name!r} from {args.image}")
    agent = project.agents.create_version(
        agent_name=args.name,
        definition=HostedAgentDefinition(
            protocol_versions=[
                ProtocolVersionRecord(protocol=AgentEndpointProtocol.RESPONSES, version="2.0.0")
            ],
            cpu=args.cpu,
            memory=args.memory,
            container_configuration=ContainerConfiguration(image=args.image),
            environment_variables=env_vars,
        ),
    )
    print(f"created version {agent.version}")

    while True:
        info = project.agents.get_version(agent_name=args.name, agent_version=agent.version)
        status = info["status"]
        print(f"status: {status}")
        if status == "active":
            print(f"\nready. invoke with agent_name={args.name!r}")
            return 0
        if status == "failed":
            print(f"\nprovisioning failed: {info.get('error')}", file=sys.stderr)
            return 1
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
