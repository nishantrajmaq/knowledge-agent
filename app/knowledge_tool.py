import logging
from functools import lru_cache
from typing import Annotated
from urllib.parse import quote, unquote

import requests

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from pydantic import Field

from app.config import settings
from app.user_context import get_user_context

logger = logging.getLogger(__name__)

SEARCH_SCOPE = "https://search.azure.com/.default"


@lru_cache(maxsize=1)
def _client() -> SearchClient:
    """Key-auth client for an index without document-level ACLs.

    Built on first use, not at import. A hosted agent whose module raises during import never
    starts, and the portal surfaces that only as a network error with no session. Failing here
    instead turns a missing setting into a message the model can relay.
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


@lru_cache(maxsize=1)
def _credential():
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def _acl_search(query: str, select: list[str], user_token: str) -> list[dict]:
    """Query an ACL-enabled index so Azure AI Search trims results to the signed-in user.

    Two tokens travel on one request: Authorization carries this service's own identity (the
    managed identity when hosted; it needs Search Index Data Reader), and
    x-ms-query-source-authorization carries the end user's token, which Search matches against
    each chunk's UserIds/GroupIds. The user header takes the bare token -- a "Bearer " prefix
    is rejected with BearerReadAccessTokenFailed. The SDK's stable api-version predates this
    feature, hence REST with a preview version.
    """
    missing = [
        name
        for name, value in (
            ("AZURE_SEARCH_ENDPOINT", settings.azure_search_endpoint),
            ("AZURE_SEARCH_INDEX_NAME", settings.azure_search_index_name),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"knowledge base not configured; missing {', '.join(missing)}")

    service_token = _credential().get_token(SEARCH_SCOPE).token
    url = (
        f"{settings.azure_search_endpoint.rstrip('/')}/indexes/"
        f"{settings.azure_search_index_name}/docs/search"
    )
    response = requests.post(
        url,
        params={"api-version": settings.azure_search_api_version},
        headers={
            "Authorization": f"Bearer {service_token}",
            "x-ms-query-source-authorization": user_token,
        },
        json={
            "search": query,
            "top": settings.azure_search_top_k,
            "select": ",".join(select),
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"search returned HTTP {response.status_code}: {response.text[:200]}")
    return response.json()["value"]



def _source_ref(raw: str) -> tuple[str, str | None]:
    """Turn the index's source value into (file name, clickable link or None).

    The SharePoint indexer stores a Graph drive path, /drives/<id>/root:/<path>, which is not
    a browsable URL. The path after "root:" is relative to the library root, so it is appended
    to SHAREPOINT_LIBRARY_URL. Values that are already http(s) URLs pass through unchanged.
    """
    if raw.startswith(("http://", "https://")):
        return unquote(raw.rstrip("/").rsplit("/", 1)[-1]), raw
    path = raw.split("root:", 1)[-1].lstrip("/")
    label = path.rsplit("/", 1)[-1] or raw
    base = settings.sharepoint_library_url
    return label, f"{base.rstrip('/')}/{quote(path)}" if base else None


def search_knowledge_base(
    query: Annotated[str, Field(description="The search query to look up in the knowledge base.")],
) -> str:
    """Search the deployed knowledge base (Azure AI Search index) for information relevant to
    the query and return the matching passages. Use this before answering any question that
    might depend on the knowledge base's content."""
    logger.info("search_knowledge_base called with query=%r", query)

    content_field = settings.azure_search_content_field
    source_field = settings.azure_search_source_field

    select = [content_field]
    if source_field:
        select.append(source_field)

    try:
        if settings.azure_search_enforce_user_acl:
            user = get_user_context()
            if user is None or not user.token:
                logger.warning("ACL enforcement on but no user token; refusing search")
                return (
                    "ACCESS DENIED. The knowledge base was NOT searched, so you know nothing "
                    "about the user's question. Reply with exactly this sentence and nothing "
                    "else: \"I can't verify your access to the knowledge base from this channel "
                    "yet, so I can't answer questions from it.\" Do not say the information "
                    "does not exist, do not ask for more context, and do not use general "
                    "knowledge."
                )
            results = _acl_search(query, select, user.token)
        else:
            results = _client().search(
                search_text=query,
                top=settings.azure_search_top_k,
                select=select,
            )
    except RuntimeError as exc:
        logger.error("%s", exc)
        return f"The knowledge base is unavailable: {exc}"

    passages = []
    numbers: dict[str, int] = {}  # one number per document, shared by its passages
    references: dict[int, str] = {}  # number -> "label: url" (or just label), each once
    for doc in results:
        text = doc.get(content_field)
        if not text:
            continue
        source = doc.get(source_field) if source_field else None
        if not source:
            passages.append(text)
            continue
        label, url = _source_ref(source)
        n = numbers.setdefault(source, len(numbers) + 1)
        references.setdefault(n, f"{label}: {url}" if url else label)
        header = f"[{n}] {label}" + (f" ({url})" if url else "")
        passages.append(f"{header}\n{text}")

    if not passages:
        return "No relevant results were found in the knowledge base."

    body = "\n\n---\n\n".join(passages)
    if not references:
        return body

    # Built here, not by the model: the model otherwise skips the section or invents URLs.
    block = "\n".join(f"[{n}] {ref}" for n, ref in sorted(references.items()))
    return (
        f"{body}\n\n=====\nREFERENCES -- end your answer with a section headed 'References' "
        "that copies exactly these lines, and nothing else. Use no other links; if a line has "
        "no URL, give no URL:\n"
        f"{block}"
    )
