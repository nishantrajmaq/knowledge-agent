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
from app.user_context import UserContext, reset_user_context, set_user_context

logger = logging.getLogger(__name__)

AGENTS_CONFIG = load_configuration_from_env(os.environ)

BOT_CONFIGURED = bool(
    AGENTS_CONFIG.get("CONNECTIONS", {})
    .get("SERVICE_CONNECTION", {})
    .get("SETTINGS", {})
    .get("CLIENTID")
)


def _user_from_activity(context: TurnContext) -> UserContext:
    """Identity claims asserted by the channel.

    These identify the user but do not authorize them. `token` stays None until an SSO
    OAuth connection is configured; wire the OBO exchange here and populate it before
    enabling azure_search_enforce_user_acl.
    """
    activity = context.activity
    channel_data = activity.channel_data or {}
    tenant = channel_data.get("tenant") or {}
    return UserContext(
        object_id=getattr(activity.from_property, "aad_object_id", None),
        tenant_id=tenant.get("id"),
        name=getattr(activity.from_property, "name", None),
        token=None,
    )


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

    @agent_app.activity(ActivityTypes.message)
    async def on_message(context: TurnContext, state: TurnState) -> None:
        user = _user_from_activity(context)
        logger.info("turn from user oid=%s tenant=%s", user.object_id, user.tenant_id)

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
