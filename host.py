"""Entrypoint for running as a Microsoft Foundry hosted agent.

Serves the Responses protocol on port 8088. The protocol library also exposes /readiness
for the platform's health checks, so there is nothing to implement for that.

build_agent is passed as a callable, not an instance: the host then constructs one agent
per request, which is what keeps per-user tool state from leaking between callers.

history_source="agent" is required, not a preference. The default ("agent_server") only
accepts a RawAgent so hosting can enforce downstream storage options, and raises at
startup for anything else. The trade-off is that the platform no longer replays
conversation history -- each turn receives only the current input, so multi-turn context
needs an explicit history provider on the agent.
"""

import logging

from agent_framework_foundry_hosting import ResponsesHostServer

from app.agent import build_agent

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    ResponsesHostServer(build_agent, history_source="agent").run()
