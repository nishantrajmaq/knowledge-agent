import logging
import secrets

import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from microsoft_agents.hosting.fastapi import jwt_authorization_decorator, start_agent_process
from pydantic import BaseModel

from app import bot
from app.agent import build_agent
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Knowledge Agent")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


if bot.BOT_CONFIGURED:
    # Read by jwt_authorization_decorator to validate the Bot Framework channel JWT.
    app.state.agent_configuration = (
        bot.CONNECTION_MANAGER.get_default_connection_configuration()
    )

    # Auth is per-route, not global middleware: /healthz must stay reachable for the
    # Container Apps probe. Without the decorator this would accept forged activities.
    @jwt_authorization_decorator
    async def _authorized_messages(request: Request):
        return await start_agent_process(request, bot.AGENT_APP, bot.ADAPTER)

    def _token_is_well_formed(auth_header: str | None) -> bool:
        """Reject tokens the SDK validator cannot parse.

        Its signing-key lookup indexes header['kid'] directly, so a token without that
        claim (e.g. a forged alg=none) escapes as KeyError -> HTTP 500. Rejecting here
        keeps such attempts logged as 401s. This only refuses; the SDK still validates.
        """
        if not auth_header or not auth_header.startswith("Bearer "):
            return True  # let the SDK emit its own 401 for missing/garbage headers
        try:
            return "kid" in jwt.get_unverified_header(
                auth_header[len("Bearer ") :].strip()
            )
        except Exception:
            return False

    @app.post("/api/messages")
    async def messages(request: Request):
        if not _token_is_well_formed(request.headers.get("Authorization")):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await _authorized_messages(request)


if settings.enable_debug_chat_endpoint:
    if not settings.debug_chat_api_key:
        raise RuntimeError(
            "ENABLE_DEBUG_CHAT_ENDPOINT is true but DEBUG_CHAT_API_KEY is not set. "
            "Refusing to start an unauthenticated /chat endpoint."
        )

    logger.warning("POST /chat is enabled (API-key protected). Disable it in production.")

    class ChatRequest(BaseModel):
        message: str

    class ChatResponse(BaseModel):
        response: str

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest, x_api_key: str = Header(default="")) -> ChatResponse:
        if not secrets.compare_digest(x_api_key, settings.debug_chat_api_key):
            raise HTTPException(status_code=401, detail="Unauthorized")
        result = await build_agent().run(request.message)
        return ChatResponse(response=result.text)
