from functools import lru_cache

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient

from app.config import settings
from app.knowledge_tool import search_knowledge_base


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
def _chat_client() -> OpenAIChatClient:
    """Shared across requests — it is stateless per user and owns the HTTP pool."""
    return OpenAIChatClient(
        model=settings.azure_openai_deployment_name,
        api_key=settings.azure_openai_api_key,
        base_url=_openai_base_url(),
    )


def build_agent() -> Agent:
    """Build a fresh Agent for a single turn.

    Per-turn construction is required once MCP tools use header_provider: the SDK binds
    a session to one immutable header identity and serializes calls on a shared instance,
    so a process-wide agent would leak one user's session into another's request.
    """
    return Agent(
        client=_chat_client(),
        instructions=settings.agent_instructions,
        name=settings.agent_name,
        tools=[search_knowledge_base],
    )
