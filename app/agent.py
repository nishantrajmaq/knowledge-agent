import logging
from functools import lru_cache

from agent_framework import Agent

from app.config import settings
from app.knowledge_tool import search_knowledge_base

logger = logging.getLogger(__name__)


def _openai_base_url() -> str:
    """Normalize the configured endpoint to the OpenAI-compatible /openai/v1 base URL.

    Passing this as base_url (rather than azure_endpoint) keeps the SDK on the plain
    OpenAI client, which does not append the ?api-version query parameter that the
    /openai/v1 path rejects with a 400.
    """
    endpoint = settings.azure_openai_endpoint.rstrip("/")
    if endpoint.endswith("/openai/v1"):
        return f"{endpoint}/"
    return f"{endpoint}/openai/v1/"


@lru_cache(maxsize=1)
def _chat_client():
    """Shared across requests — stateless per user, and it owns the HTTP pool."""
    if settings.foundry_project_endpoint:
        # Running as a Foundry hosted agent: authenticate as the platform-assigned
        # agent identity, so there is no model API key anywhere in the deployment.
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        if not settings.model_deployment_name:
            raise RuntimeError(
                "FOUNDRY_PROJECT_ENDPOINT is set but MODEL_DEPLOYMENT_NAME is not. "
                "Declare it in environment_variables on the agent version."
            )

        logger.info("using Foundry identity auth, model=%s", settings.model_deployment_name)
        return FoundryChatClient(
            project_endpoint=settings.foundry_project_endpoint,
            model=settings.model_deployment_name,
            credential=DefaultAzureCredential(),
        )

    from agent_framework.openai import OpenAIChatClient

    missing = [
        name
        for name, value in (
            ("AZURE_OPENAI_ENDPOINT", settings.azure_openai_endpoint),
            ("AZURE_OPENAI_API_KEY", settings.azure_openai_api_key),
            ("AZURE_OPENAI_DEPLOYMENT_NAME", settings.azure_openai_deployment_name),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing model configuration: {', '.join(missing)}. "
            "Set these for key auth, or set FOUNDRY_PROJECT_ENDPOINT to use Foundry identity."
        )

    logger.info("using key auth, deployment=%s", settings.azure_openai_deployment_name)
    return OpenAIChatClient(
        model=settings.azure_openai_deployment_name,
        api_key=settings.azure_openai_api_key,
        base_url=_openai_base_url(),
    )


def build_agent() -> Agent:
    """Build a fresh Agent for a single turn.

    Also used directly as the request-scoped factory for the Foundry host servers, which
    accept a zero-argument callable and scope the returned agent to one request.

    Per-turn construction is required once MCP tools use header_provider: the SDK binds a
    session to one immutable header identity and serializes calls on a shared instance, so
    a process-wide agent would leak one user's session into another's request.
    """
    return Agent(
        client=_chat_client(),
        instructions=settings.agent_instructions,
        name=settings.agent_name,
        tools=[search_knowledge_base],
    )
