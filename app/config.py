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
    azure_search_top_k: int = 3
    # api-version for ACL-trimmed queries (azure_search_enforce_user_acl). The permission feature
    # is preview-only, so these queries go over REST rather than the SDK.
    azure_search_api_version: str = "2025-11-01-preview"
    # Web URL of the SharePoint document library the index was built from, e.g.
    # https://<tenant>.sharepoint.com/sites/<site>/Shared%20Documents. The index only stores a
    # Graph drive path (/drives/<id>/root:/<file>); appending that path to this URL gives the
    # clickable link shown in citations. Unset, citations show the file name without a link.
    sharepoint_library_url: str | None = None

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
        "matter. Be concise: answer in a short paragraph or a few bullets, about 120 words at "
        "most unless the user asks for detail. Answer using only the passages the tool returns. "
        "Each passage starts with a "
        "numbered reference such as [1] File name.pdf (https://link). Cite inline with those "
        "numbers, e.g. [1]. The tool result ends with a block that starts 'REFERENCES'. "
        "End every answer that used the knowledge base with a section headed 'References' on "
        "its own line, after a blank line, that copies those listed lines exactly -- each "
        "document once, with the URL as plain text and never as a markdown link. Never write a "
        "link or file name that is not in that block, never invent a URL, and never show raw "
        "paths or ids. If the tool returns no relevant results, say you don't know rather than "
        "guessing, and do not add a References section. Never fill gaps with general or typical "
        "policies from your own knowledge: if the tool is unavailable, denied access, or "
        "returned nothing relevant, say so plainly and stop."
    )


settings = Settings()
