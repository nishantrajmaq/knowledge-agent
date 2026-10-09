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

Per-user document access: a trusted backend (not Teams/Copilot directly) does the Entra
on-behalf-of exchange for an Azure AI Search token and forwards it as the request header
x-client-search-access-token (bare token, no "Bearer " prefix), together with
x-ms-user-identity. Foundry forwards x-client-* headers to this container but never the
caller's Authorization header. The token is request-scoped: it goes only into UserContext
for the search tool, never into prompts, history, logs or responses. See
https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/use-on-behalf-of-flow
"""

import logging

from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.core import get_request_context

from app.agent import build_agent
from app.user_context import UserContext, reset_user_context, set_user_context

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Header names are lowercase in ResponseContext.client_headers. The x-client- prefix is what
# makes Foundry forward the header to the container; the rest of the name is ours.
USER_TOKEN_HEADER = "x-client-search-access-token"


class UserTokenResponsesHostServer(ResponsesHostServer):
    """Expose the forwarded user token to tools for the duration of one request.

    _handle_response is a private hook of the hosting library, so re-check it when upgrading
    agent-framework-foundry-hosting.
    """

    async def _handle_response(self, request, context, cancellation_signal):
        token = context.client_headers.get(USER_TOKEN_HEADER) or None
        # Names only, never values: shows which x-client-* headers the caller (e.g. the Foundry
        # bot bridge) actually forwards, so a missing token can be told apart from a wrong name.
        logger.info(
            "request user token present=%s; forwarded x-client headers=%s",
            token is not None,
            sorted(context.client_headers.keys()),
        )

        reset_token = set_user_context(
            UserContext(
                object_id=get_request_context().user_id,
                tenant_id=None,
                name=None,
                token=token,
            )
        )
        try:
            async for event in super()._handle_response(request, context, cancellation_signal):
                yield event
        finally:
            try:
                reset_user_context(reset_token)
            except ValueError:
                # The generator may be finalised from a different context; the value is
                # request-scoped and replaced on the next request, so this is safe to skip.
                pass


if __name__ == "__main__":
    UserTokenResponsesHostServer(build_agent, history_source="agent").run()
