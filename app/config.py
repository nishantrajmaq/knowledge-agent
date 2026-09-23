from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Azure OpenAI / Azure AI Foundry model deployment (key auth)
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment_name: str

    # Azure AI Search index used as the knowledge base (key auth)
    azure_search_endpoint: str
    azure_search_api_key: str
    azure_search_index_name: str
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
