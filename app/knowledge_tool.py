import logging
from functools import lru_cache
from typing import Annotated

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from pydantic import Field

from app.config import settings
from app.user_context import get_user_context

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def _client() -> SearchClient:
    """Built on first use, not at import.

    A hosted agent whose module raises during import never starts, and the portal surfaces
    that only as a network error with no session. Failing here instead turns a missing
    setting into a message the model can relay.
    """
    missing = [
        name
        for name, value in (
            ("AZURE_SEARCH_ENDPOINT", settings.azure_search_endpoint),
            ("AZURE_SEARCH_API_KEY", settings.azure_search_api_key),
            ("AZURE_SEARCH_INDEX_NAME", settings.azure_search_index_name),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"knowledge base not configured; missing {', '.join(missing)}")

    return SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index_name,
        credential=AzureKeyCredential(settings.azure_search_api_key),
    )


def search_knowledge_base(
    query: Annotated[str, Field(description="The search query to look up in the knowledge base.")],
) -> str:
    """Search the deployed knowledge base (Azure AI Search index) for information relevant to
    the query and return the matching passages. Use this before answering any question that
    might depend on the knowledge base's content."""
    logger.info("search_knowledge_base called with query=%r", query)

    search_kwargs = {}
    if settings.azure_search_enforce_user_acl:
        user = get_user_context()
        if user is None or not user.token:
            logger.warning("ACL enforcement on but no user token; refusing search")
            return "Access denied: no verified user identity is available for this request."
        search_kwargs["headers"] = {
            "x-ms-query-source-authorization": f"Bearer {user.token}"
        }

    try:
        client = _client()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return f"The knowledge base is unavailable: {exc}"

    content_field = settings.azure_search_content_field
    source_field = settings.azure_search_source_field

    select = [content_field]
    if source_field:
        select.append(source_field)

    results = client.search(
        search_text=query,
        top=settings.azure_search_top_k,
        select=select,
        **search_kwargs,
    )

    passages = []
    for doc in results:
        text = doc.get(content_field)
        if not text:
            continue
        source = doc.get(source_field) if source_field else None
        passages.append(f"[source: {source}]\n{text}" if source else text)

    if not passages:
        return "No relevant results were found in the knowledge base."

    return "\n\n---\n\n".join(passages)
