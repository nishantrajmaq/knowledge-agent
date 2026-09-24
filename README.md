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

## Deploy to Azure Container Apps via ACR

No local Docker required — `az acr build` builds the image inside Azure.

### 0. Variables

```bash
RG=rg-itron-ekg-search
ENV=cae-itron-ekg
APP=ca-itron-ekg
ACR=acritronekg          # registry name: alphanumeric only, globally unique
TAG=v1
```

### 1. Create the registry (once)

```bash
az acr create -g $RG -n $ACR --sku Basic      # testing
az acr create -g $RG -n $ACR --sku Premium    # production (private endpoints)
```

`Basic` is enough for testing — `az acr build` works on every SKU and 10 GiB holds this image
many times over. Private endpoints are `Premium`-only, so the VNet-integrated topology needs
Premium. Upgrading is in-place (`az acr update -n $ACR --sku Premium`); no recreate, no
re-push, so start on Basic.

### 2. Build the image in Azure

```bash
cd knowledge-agent
az acr build --registry $ACR --image $APP:$TAG .
```

This uploads the build context, runs the `Dockerfile` on ACR Tasks, and pushes the result.
`.dockerignore` keeps `.env` out of the context — verify that before the first build.

### 3. Create the container app

Pull with a managed identity rather than registry admin credentials, so there is no registry
password to store or rotate:

```bash
az containerapp create \
  --name $APP --resource-group $RG --environment $ENV \
  --image $ACR.azurecr.io/$APP:$TAG \
  --system-assigned \
  --target-port 8000 --ingress external --min-replicas 1

PRINCIPAL=$(az containerapp show -n $APP -g $RG --query identity.principalId -o tsv)
ACR_ID=$(az acr show -n $ACR --query id -o tsv)

az role assignment create \
  --assignee $PRINCIPAL --role AcrPull --scope $ACR_ID

az containerapp registry set \
  -n $APP -g $RG --server $ACR.azurecr.io --identity system
```

**Ingress:** `external` is correct for testing and for a bot with no gateway in front — Azure
Bot Service calls in from the internet and cannot reach a private endpoint. Switch to
`internal` only once Application Gateway is the public frontend:

```bash
az containerapp ingress enable -n $APP -g $RG --type internal --target-port 8000
```

External ingress is not a security gap here: `/api/messages` is protected by Bot Framework JWT
validation, and `/chat` by its API key. The FQDN being public is expected.

### 4. Configure secrets and environment

Secrets first, then reference them — never put a key directly in `--env-vars`, where it is
readable from the app's configuration:

```bash
az containerapp secret set -n $APP -g $RG --secrets \
  openai-key=<model-key> \
  search-key=<search-key> \
  bot-client-secret=<bot-secret>

az containerapp update -n $APP -g $RG --set-env-vars \
  AZURE_OPENAI_ENDPOINT=<foundry-project-endpoint> \
  AZURE_OPENAI_API_KEY=secretref:openai-key \
  AZURE_OPENAI_DEPLOYMENT_NAME=<deployment> \
  AZURE_SEARCH_ENDPOINT=<search-endpoint> \
  AZURE_SEARCH_API_KEY=secretref:search-key \
  AZURE_SEARCH_INDEX_NAME=<index> \
  AZURE_SEARCH_CONTENT_FIELD=snippet \
  AZURE_SEARCH_SOURCE_FIELD=blob_url \
  CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=<bot-app-id> \
  CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=secretref:bot-client-secret \
  CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=<tenant-id> \
  ENABLE_DEBUG_CHAT_ENDPOINT=false
```

`--min-replicas 1` matters: conversation state uses `MemoryStorage`, which is per-replica and
lost on scale-to-zero. Move it to Cosmos before scaling out.

### 5. Verify

```bash
az containerapp logs show -n $APP -g $RG --follow
```

Look for `/api/messages` being registered. If you instead see *"Bot credentials not
configured"*, the three `CONNECTIONS__*` variables did not land.

With `external` ingress you can test straight from your machine:

```bash
FQDN=$(az containerapp show -n $APP -g $RG --query properties.configuration.ingress.fqdn -o tsv)
curl https://$FQDN/healthz

# only if ENABLE_DEBUG_CHAT_ENDPOINT=true
curl -X POST https://$FQDN/chat -H "Content-Type: application/json" \
  -H "X-API-Key: <your-key>" -d '{"message":"what is the remote work policy?"}'
```

Once ingress is `internal`, the FQDN resolves only inside the VNet — test from the Application
Gateway frontend or a jumpbox instead.

### 6. Redeploy after code changes

```bash
az acr build --registry $ACR --image $APP:v2 .
az containerapp update -n $APP -g $RG --image $ACR.azurecr.io/$APP:v2
```

Use a new tag each time. Re-pushing `:v1` leaves the running revision on the cached digest, so
the deploy silently does nothing.

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
- For the VNet-integrated topology, add private endpoints for ACR, Azure AI Search, and the
  Foundry project, and confirm Application Gateway forwards the `Authorization` header
  unmodified — stripping it makes every `/api/messages` request 401.
