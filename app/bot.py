import logging
import os

from microsoft_agents.activity import ActivityTypes, load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import CloudAdapter

from app.agent import build_agent
from app.config import settings
from app.user_context import UserContext, reset_user_context, set_user_context

logger = logging.getLogger(__name__)

AGENTS_CONFIG = load_configuration_from_env(os.environ)

BOT_CONFIGURED = bool(
    AGENTS_CONFIG.get("CONNECTIONS", {})
    .get("SERVICE_CONNECTION", {})
    .get("SETTINGS", {})
    .get("CLIENTID")
)


# Name of the user-authorization handler. It is configured entirely from the environment:
#   AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__SEARCH__SETTINGS__AZUREBOTOAUTHCONNECTIONNAME
#   AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__SEARCH__SETTINGS__OBOCONNECTIONNAME
#   AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__SEARCH__SETTINGS__SCOPES
# The first is the Azure Bot OAuth connection that performs Teams/Copilot SSO; the second is the
# MSAL connection (client id + secret) used for the on-behalf-of exchange. See README.
AUTH_HANDLER = "SEARCH"
SEARCH_SCOPE = "https://search.azure.com/.default"


def _user_from_activity(context: TurnContext, token: str | None) -> UserContext:
    """Identity asserted by the channel, plus the user's Search token when signed in.

    object_id/tenant_id/name identify the user but do not authorize them; only `token` does.
    """
    activity = context.activity
    channel_data = activity.channel_data or {}
    tenant = channel_data.get("tenant") or {}
    return UserContext(
        object_id=getattr(activity.from_property, "aad_object_id", None),
        tenant_id=tenant.get("id"),
        name=getattr(activity.from_property, "name", None),
        token=token,
    )


async def _search_token(agent_app, context: TurnContext) -> str | None:
    """Exchange the signed-in user's SSO token (on-behalf-of) for an Azure AI Search token.

    The SDK has already run the sign-in flow before this handler, because the route is
    registered with auth_handlers. Returns None if the exchange yields nothing, in which case
    the search tool refuses rather than searching without the user's identity.
    """
    response = await agent_app.auth.exchange_token(
        context, scopes=[SEARCH_SCOPE], auth_handler_id=AUTH_HANDLER
    )
    return response.token if response and response.token else None


def _build_agent_app():
    connection_manager = MsalConnectionManager(**AGENTS_CONFIG)
    adapter = CloudAdapter(connection_manager=connection_manager)

    # MemoryStorage is per-replica and lost on restart. Swap for Blob/Cosmos storage
    # before scaling past one replica, or conversation state will vary between requests.
    agent_app = AgentApplication[TurnState](
        storage=MemoryStorage(),
        adapter=adapter,
        connection_manager=connection_manager,
        **AGENTS_CONFIG,
    )

    # With ACL enforcement on, the SDK signs the user in (SSO, or a sign-in card) before the
    # handler runs. Without it there is no auth handler and no user token is requested.
    auth_handlers = [AUTH_HANDLER] if settings.azure_search_enforce_user_acl else None

    @agent_app.activity(ActivityTypes.message, auth_handlers=auth_handlers)
    async def on_message(context: TurnContext, state: TurnState) -> None:
        token = None
        if settings.azure_search_enforce_user_acl:
            try:
                token = await _search_token(agent_app, context)
            except Exception:
                logger.exception("on-behalf-of token exchange failed")
            if token is None:
                await context.send_activity(
                    "I couldn't verify your access to the knowledge base. Please sign in "
                    "when prompted, or try again in a moment."
                )
                return

        user = _user_from_activity(context, token)
        # Log only that a token is present, never the value.
        logger.info(
            "turn from user oid=%s tenant=%s token=%s",
            user.object_id,
            user.tenant_id,
            "present" if token else "absent",
        )

        reset_token = set_user_context(user)
        try:
            result = await build_agent().run(context.activity.text or "")
            await context.send_activity(result.text)
        except Exception:
            logger.exception("agent turn failed")
            await context.send_activity("Sorry, something went wrong handling that request.")
        finally:
            reset_user_context(reset_token)

    @agent_app.conversation_update("membersAdded")
    async def on_members_added(context: TurnContext, state: TurnState) -> None:
        await context.send_activity(
            "Hi — ask me anything about the knowledge base and I'll look it up."
        )

    return connection_manager, adapter, agent_app


if BOT_CONFIGURED:
    CONNECTION_MANAGER, ADAPTER, AGENT_APP = _build_agent_app()
else:
    CONNECTION_MANAGER = ADAPTER = AGENT_APP = None
    logger.warning(
        "Bot credentials not configured — /api/messages is disabled. "
        "Set CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID/CLIENTSECRET/TENANTID "
        "to enable the Microsoft 365 Copilot channel."
    )
