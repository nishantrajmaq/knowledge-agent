from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Model access, resolved in one of two modes:
    #
    # Foundry hosted  - the platform injects FOUNDRY_PROJECT_ENDPOINT and gives the container
    #                   a dedicated Entra identity, so no key is involved. Declare
    #                   MODEL_DEPLOYMENT_NAME yourself on the agent version.
    # Key auth        - local runs and Container Apps, using the three AZURE_OPENAI_* values.
    #
    # build_agent() picks the mode from whether foundry_project_endpoint is set.
    foundry_project_endpoint: str | None = None
    # azd's generated azure.yaml uses AZURE_AI_MODEL_DEPLOYMENT_NAME; accept either spelling.
    model_deployment_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MODEL_DEPLOYMENT_NAME", "AZURE_AI_MODEL_DEPLOYMENT_NAME"
        ),
    )

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_deployment_name: str | None = None

    # Azure AI Search index used as the knowledge base (key auth).
    # Optional so a missing value cannot kill the process at import: on a hosted agent that
    # reads as a dead container and an opaque network error in the portal. The tool reports
    # what is missing instead.
    azure_search_endpoint: str | None = None
    azure_search_api_key: str | None = None
    azure_search_index_name: str | None = None
    azure_search_content_field: str = "snippet"
    azure_search_source_field: str | None = "blob_url"
    azure_search_top_k: int = 5

    # When true, every search passes the caller's token as x-ms-query-source-authorization
    # so Azure AI Search trims results to that user, and requests without a token are
    # refused. When false there is NO per-user trimming — the service key sees everything.
    azure_search_enforce_user_acl: bool = False

    # POST /chat is a debug surface for testing without the bot channel. When enabled it
    # requires debug_chat_api_key and checks it on every call — an open /chat on public
    # ingress would expose the whole knowledge base and bypass any per-user ACL.
    enable_debug_chat_endpoint: bool = False
    debug_chat_api_key: str | None = None

    agent_name: str = "KnowledgeAgent"
    agent_instructions: str = (
        "You are a helpful assistant. Always use the search_knowledge_base tool to look up "
        "relevant information before answering questions about the knowledge base's subject "
        "matter. Answer using the passages the tool returns; each passage may be prefixed with "
        "a [source: ...] line you should cite. If the tool returns no relevant results, say you "
        "don't know rather than guessing."
    )


settings = Settings()
