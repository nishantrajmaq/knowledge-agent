# Knowledge Agent (Microsoft Agent Framework sample)

A minimal agent built with [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)
that answers questions grounded in a knowledge base already deployed as an **Azure AI Search
index** (e.g. connected to an Azure AI Foundry project). Authentication to both the model
deployment and the search index uses **API keys**.

## How it works

- `app/agent.py` builds a MAF `Agent` backed by `OpenAIChatClient`, pointed at the endpoint's
  OpenAI-compatible `/openai/v1/` surface via `base_url` + `api_key`. This works against both a
  Foundry project endpoint and a classic Azure OpenAI resource.

  > Note: `base_url` is used deliberately instead of `azure_endpoint`. The `azure_endpoint`
  > route builds an `AsyncAzureOpenAI` client that always appends `?api-version=...`, which the
  > `/openai/v1` path rejects with `400 BadRequest: api-version query parameter is not allowed
  > when using /v1 path`. There is no way to suppress it (an empty value falls back to a
  > default), so `base_url` is the working path.
- `app/knowledge_tool.py` defines `search_knowledge_base`, a plain Python function that queries
  the Azure AI Search index via `azure-search-documents` using an `AzureKeyCredential`. It is
  registered as a tool on the agent, so the model decides when to call it during a conversation.
- `app/bot.py` wires the Microsoft 365 Agents SDK (`AgentApplication` + `CloudAdapter`) for the
  M365 Copilot / Teams channel. It is **optional**: with no bot credentials set, `/api/messages`
  is not registered and the app still runs for local or container testing.
- `app/user_context.py` holds the per-request user identity in a `ContextVar`. Tools read it
  from there rather than taking it as a parameter — the model fills tool parameters, so a
  credential placed there would be model-controlled.
- `app/main.py` exposes the FastAPI app.

## Endpoints

| Endpoint | Auth | When registered |
|---|---|---|
| `GET /healthz` | none | always (Container Apps probe) |
| `POST /api/messages` | Bot Framework channel JWT | only when bot credentials are set |
| `POST /chat` | `X-API-Key` header | only when `ENABLE_DEBUG_CHAT_ENDPOINT=true` |

`/chat` is a testing surface that bypasses the channel entirely. It refuses to start without
`DEBUG_CHAT_API_KEY`, because an open `/chat` on public ingress would expose the whole knowledge
base and bypass any per-user ACL. Turn it off once the Copilot channel is live.

## Setup

```bash
cd knowledge-agent
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env` with your real values:

| Variable | Description |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Your Azure OpenAI / Foundry project chat endpoint |
| `AZURE_OPENAI_API_KEY` | API key for that endpoint |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Name of the chat model deployment |
| `AZURE_SEARCH_ENDPOINT` | Your Azure AI Search service endpoint |
| `AZURE_SEARCH_API_KEY` | A query (or admin) key for the search service |
| `AZURE_SEARCH_INDEX_NAME` | Name of the index that holds the knowledge base |
| `AZURE_SEARCH_CONTENT_FIELD` | Searchable field holding the chunk text — **must match your index schema** |
| `AZURE_SEARCH_SOURCE_FIELD` | Optional field holding a URL/filename, surfaced to the model as a citation |

To find the right field names, open the index in the Azure Portal (**Search service → Indexes →
your index → Fields**), or run a query in **Search explorer** and look at which field holds the
actual passage text. Indexes built by Foundry's knowledge/ingestion flow commonly use `snippet`
or `chunk` rather than `content`.

## Run locally (no HTTP)

```bash
python test_local.py
```

Ask a question that should be answerable from the knowledge base. You'll see an
`INFO ... search_knowledge_base called with query=...` log line when the agent invokes the tool.

## Run the API locally

```bash
uvicorn app.main:app --reload
```

```bash
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"message\": \"What does the knowledge base say about X?\"}"
```

## Build and run in Docker

```bash
docker build -t knowledge-agent .
docker run -p 8000:8000 --env-file .env knowledge-agent
```

## Deploy to Azure Container Apps (agent testing, no bot service)

No local Docker needed — `az acr build` builds in Azure:

```bash
RG=<your-rg>; ACR=<your-acr>; APP=knowledge-agent; ENV=<your-containerapp-env>
KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

az acr build --registry $ACR --image $APP:v1 .

az containerapp create \
  --name $APP --resource-group $RG --environment $ENV \
  --image $ACR.azurecr.io/$APP:v1 \
  --target-port 8000 --ingress external --min-replicas 1 \
  --registry-server $ACR.azurecr.io \
  --secrets openai-key=<model-key> search-key=<search-key> chat-key=$KEY \
  --env-vars \
    AZURE_OPENAI_ENDPOINT=<endpoint> \
    AZURE_OPENAI_API_KEY=secretref:openai-key \
    AZURE_OPENAI_DEPLOYMENT_NAME=<deployment> \
    AZURE_SEARCH_ENDPOINT=<search-endpoint> \
    AZURE_SEARCH_API_KEY=secretref:search-key \
    AZURE_SEARCH_INDEX_NAME=<index> \
    AZURE_SEARCH_CONTENT_FIELD=snippet \
    AZURE_SEARCH_SOURCE_FIELD=blob_url \
    ENABLE_DEBUG_CHAT_ENDPOINT=true \
    DEBUG_CHAT_API_KEY=secretref:chat-key

echo "your test key: $KEY"
```

Test it:

```bash
FQDN=$(az containerapp show -n $APP -g $RG --query properties.configuration.ingress.fqdn -o tsv)
curl -X POST "https://$FQDN/chat" -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" -d '{"message":"..."}'
```

`--min-replicas 1` matters: conversation state uses `MemoryStorage`, which is per-replica and
lost on scale-to-zero. Swap it for Blob/Cosmos storage before scaling out.

When you later add the bot, set the three `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__*` vars
and set `ENABLE_DEBUG_CHAT_ENDPOINT=false`.

## Notes / next steps

- Built against **agent-framework 1.19** (GA 1.x API: `Agent`, not the older preview
  `ChatAgent`; `agent_framework.openai.OpenAIChatClient`, not `AzureOpenAIChatClient`). If you
  follow older blog posts/docs you'll hit `ImportError` on those old names.
- This sample keeps the conversation **stateless** — each `/chat` call is independent. For a
  multi-turn session, create an `AgentSession` per user/session (e.g. keyed by a session id in
  the request) and pass it to `agent.run(message, session=session)`.
- Auth is key-based everywhere per current requirements. To move to Microsoft Entra ID /
  managed identity later: swap `api_key=...` for `credential=DefaultAzureCredential()` in
  `app/agent.py`, and swap `AzureKeyCredential(...)` for the same credential in
  `app/knowledge_tool.py` (`azure-identity` is already in `requirements.txt` for this).
- Deploying to Azure Container Apps is not covered here — build the image, push it to a
  registry (e.g. ACR), and create/update the Container App with your endpoints/keys as secrets
  and env vars.
