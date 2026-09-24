"""Entrypoint for running as a Microsoft Foundry hosted agent.

Serves the Responses protocol on port 8088. The protocol library also exposes /readiness
for the platform's health checks, so there is nothing to implement for that.

build_agent is passed as a callable, not an instance: the host then constructs one agent
per request, which is what keeps per-user tool state from leaking between callers.
"""

import logging

from agent_framework_foundry_hosting import ResponsesHostServer

from app.agent import build_agent

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    ResponsesHostServer(build_agent).run()
