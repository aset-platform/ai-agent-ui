"""HTTP route handlers extracted from :class:`~main.ChatServer`.

All route handlers are plain ``async`` functions that receive
their dependencies (registries, executor, settings) via closure
over the :func:`create_app` arguments.

Functions
---------
- :func:`create_app` — build the configured FastAPI ASGI app
"""

import asyncio
import json
import logging
import queue
import threading
import time

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from models import ChatRequest, ChatResponse
from slowapi.errors import RateLimitExceeded
from tools._ticker_linker import set_current_user

from auth.api import create_auth_router, get_ticker_router
from auth.rate_limit import limiter, rate_limit_exceeded_handler

_logger = logging.getLogger(__name__)


def create_app(
    agent_registry,
    executor,
    settings,
):
    """Build and return the configured FastAPI application.

    Args:
        agent_registry: Populated
            :class:`~agents.registry.AgentRegistry`.
        executor: :class:`~concurrent.futures.ThreadPoolExecutor`
            for agent execution.
        settings: :class:`~config.Settings` instance.

    Returns:
        A fully configured :class:`~fastapi.FastAPI` instance.
    """
    app = FastAPI(title="AI Agent API")

    # CORS: whitelist known front-end origins.
    _allowed_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8050",
        "http://127.0.0.1:8050",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
        ],
    )

    # Security response headers.
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
        """Add security headers to every response."""

        async def dispatch(self, request: Request, call_next):
            """Add security headers after upstream handling."""
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = (
                "strict-origin-when-cross-origin"
            )
            return response

    app.add_middleware(_SecurityHeadersMiddleware)

    # Rate limiting (slowapi).
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        rate_limit_exceeded_handler,
    )

    # ---------------------------------------------------------------
    # Core route handlers — shared by root and /v1 mounts
    # ---------------------------------------------------------------

    async def _chat(req: ChatRequest):
        """Sync agent dispatch (POST /chat)."""
        agent = agent_registry.get(req.agent_id)
        if agent is None:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{req.agent_id}' not found",
            )
        set_current_user(req.user_id)
        try:
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(
                executor,
                agent.run,
                req.message,
                req.history,
            )
            result = await asyncio.wait_for(
                future,
                timeout=settings.agent_timeout_seconds,
            )
        except asyncio.TimeoutError:
            _logger.warning(
                "Agent '%s' timed out after %ds",
                req.agent_id,
                settings.agent_timeout_seconds,
            )
            raise HTTPException(
                status_code=504,
                detail="Agent timed out",
            )
        except Exception as e:
            _logger.error(
                "Chat handler error: %s",
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Agent execution failed",
            )
        return ChatResponse(
            response=result,
            agent_id=req.agent_id,
        )

    async def _chat_stream(req: ChatRequest):
        """NDJSON streaming (POST /chat/stream)."""
        agent = agent_registry.get(req.agent_id)
        if agent is None:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{req.agent_id}' not found",
            )

        timeout = settings.agent_timeout_seconds

        def generate():
            """Run agent.stream() in a thread."""
            event_queue: queue.Queue = queue.Queue()

            def run() -> None:
                set_current_user(req.user_id)
                try:
                    for event in agent.stream(
                        req.message,
                        req.history,
                    ):
                        event_queue.put(event)
                except Exception:
                    pass
                finally:
                    event_queue.put(None)

            worker = threading.Thread(
                target=run,
                daemon=True,
            )
            worker.start()

            start = time.time()
            while True:
                elapsed = time.time() - start
                if elapsed >= timeout:
                    yield json.dumps(
                        {
                            "type": "timeout",
                            "message": (
                                "Agent timed out" f" after {timeout}s"
                            ),
                        }
                    ) + "\n"
                    break
                remaining = timeout - elapsed
                try:
                    item = event_queue.get(
                        timeout=remaining,
                    )
                    if item is None:
                        break
                    yield item
                except queue.Empty:
                    if time.time() - start >= timeout:
                        yield json.dumps(
                            {
                                "type": "timeout",
                                "message": (
                                    "Agent timed out" f" after {timeout}s"
                                ),
                            }
                        ) + "\n"
                        break

            worker.join(timeout=2)

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
        )

    async def _health():
        """GET /health."""
        return {"status": "ok"}

    async def _list_agents():
        """GET /agents."""
        return {
            "agents": agent_registry.list_agents(),
        }

    # ---------------------------------------------------------------
    # Mount at root (backward compat) and /v1 (versioned)
    # ---------------------------------------------------------------

    def _register_core_routes(router: APIRouter) -> None:
        """Attach core endpoints to *router*."""
        router.add_api_route(
            "/chat",
            _chat,
            methods=["POST"],
            response_model=ChatResponse,
        )
        router.add_api_route(
            "/chat/stream",
            _chat_stream,
            methods=["POST"],
        )
        router.add_api_route(
            "/health",
            _health,
            methods=["GET"],
        )
        router.add_api_route(
            "/agents",
            _list_agents,
            methods=["GET"],
        )

    # Root mount — backward compatibility.
    root_router = APIRouter()
    _register_core_routes(root_router)
    app.include_router(root_router)

    # Versioned mount — /v1/chat, /v1/agents, etc.
    v1_router = APIRouter(prefix="/v1")
    _register_core_routes(v1_router)
    app.include_router(v1_router)

    # Auth + user management routers.
    app.include_router(create_auth_router())
    app.include_router(get_ticker_router())

    # WebSocket streaming endpoint.
    from ws import register_ws_routes

    register_ws_routes(app, agent_registry, executor, settings)

    # Serve uploaded avatars.
    from paths import AVATARS_DIR, ensure_dirs

    ensure_dirs()
    app.mount(
        "/avatars",
        StaticFiles(directory=str(AVATARS_DIR)),
        name="avatars",
    )

    return app
