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
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException
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
    token_budget=None,
    obs_collector=None,
):
    """Build and return the configured FastAPI application.

    Args:
        agent_registry: Populated
            :class:`~agents.registry.AgentRegistry`.
        executor: :class:`~concurrent.futures.ThreadPoolExecutor`
            for agent execution.
        settings: :class:`~config.Settings` instance.
        token_budget: Optional shared
            :class:`~token_budget.TokenBudget`.
        obs_collector: Optional
            :class:`~observability.ObservabilityCollector`.

    Returns:
        A fully configured :class:`~fastapi.FastAPI` instance.
    """
    @asynccontextmanager
    async def _lifespan(a):
        """Startup: warm Redis cache."""
        try:
            from cache_warmup import (
                warm_shared,
                warm_tickers,
                warm_frequent_users,
            )

            warm_shared()

            top_n = getattr(
                settings,
                "cache_warm_top_users",
                5,
            )
            threading.Thread(
                target=warm_tickers,
                daemon=True,
                name="cache-warmup-tickers",
            ).start()
            threading.Thread(
                target=warm_frequent_users,
                args=(top_n,),
                daemon=True,
                name="cache-warmup-users",
            ).start()
        except Exception:
            _logger.warning(
                "cache warm-up skipped",
                exc_info=True,
            )
        yield

    app = FastAPI(
        title="AI Agent API",
        lifespan=_lifespan,
    )

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
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
    # Admin observability endpoint (superuser only)
    # ---------------------------------------------------------------

    async def _admin_metrics(
        current_user=None,
    ):
        """GET /admin/metrics — LLM observability data.

        Combines real-time cascade stats from the
        in-memory collector with persistent request
        totals from the Iceberg ``llm_usage`` table
        so the count matches the dashboard widget.
        """
        result: dict = {"timestamp": time.time()}
        if token_budget is not None:
            result["models"] = token_budget.get_status()
        else:
            result["models"] = {}

        cascade_stats: dict = {}
        if obs_collector is not None:
            cascade_stats = obs_collector.get_stats()

        # Override ephemeral request count with
        # persistent Iceberg total (last 30 days)
        # so it matches the dashboard LLM widget.
        try:
            from tools._stock_shared import (
                _require_repo,
            )

            repo = _require_repo()
            usage = repo.get_dashboard_llm_usage(
                user_id=None, days=30,
            )
            cascade_stats["requests_total"] = int(
                usage.get("total_requests", 0)
            )
        except Exception:
            pass  # keep in-memory count as fallback

        result["cascade_stats"] = cascade_stats
        return result

    # ---------------------------------------------------------------
    # All API endpoints under /v1 (ASETPLTFRM-20)
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

    # Versioned mount — /v1/chat, /v1/agents, etc.
    v1_router = APIRouter(prefix="/v1")
    _register_core_routes(v1_router)
    app.include_router(v1_router)

    # Auth + user management routers under /v1.
    app.include_router(
        create_auth_router(),
        prefix="/v1",
    )
    app.include_router(
        get_ticker_router(),
        prefix="/v1",
    )

    # Admin observability (superuser only).
    from auth.dependencies import superuser_only

    async def _admin_retention(
        dry_run: bool = True,
    ):
        """POST /admin/retention — run data retention cleanup.

        Args:
            dry_run: If True (default), report only.
        """
        from stocks.retention import RetentionManager

        mgr = RetentionManager()
        results = mgr.run_cleanup(dry_run=dry_run)
        return {
            "results": [
                {
                    "table": r.table_id,
                    "cutoff_date": str(r.cutoff_date),
                    "rows_before": r.rows_before,
                    "rows_deleted": r.rows_deleted,
                    "dry_run": r.dry_run,
                    "error": r.error,
                }
                for r in results
            ],
        }

    async def _admin_tier_health():
        """GET /admin/tier-health — per-tier health status."""
        result: dict = {"timestamp": time.time()}
        if obs_collector is not None:
            # Parse configured tiers from settings.
            # groq_model_tiers is a CSV string on Settings.
            raw_tiers = getattr(
                settings,
                "groq_model_tiers",
                "",
            )
            if isinstance(raw_tiers, list):
                tier_models = raw_tiers or None
            elif isinstance(raw_tiers, str) and raw_tiers:
                tier_models = [
                    t.strip() for t in raw_tiers.split(",") if t.strip()
                ]
            else:
                tier_models = None
            result["health"] = obs_collector.get_tier_health(tier_models)
        else:
            result["health"] = {
                "tiers": [],
                "summary": {
                    "total": 0,
                    "healthy": 0,
                    "degraded": 0,
                    "down": 0,
                    "disabled": 0,
                },
            }
        return result

    async def _admin_tier_toggle(
        model: str,
        enabled: bool = True,
    ):
        """POST /admin/tier-toggle — enable/disable a tier.

        Args:
            model: Groq model identifier.
            enabled: True to enable, False to disable.
        """
        if obs_collector is None:
            raise HTTPException(
                status_code=503,
                detail="Observability not available",
            )
        if enabled:
            obs_collector.enable_tier(model)
        else:
            obs_collector.disable_tier(model)
        return {
            "model": model,
            "enabled": enabled,
        }

    admin_router = APIRouter(prefix="/v1")
    admin_router.add_api_route(
        "/admin/metrics",
        _admin_metrics,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/tier-health",
        _admin_tier_health,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/tier-toggle",
        _admin_tier_toggle,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/retention",
        _admin_retention,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    app.include_router(admin_router)

    # Dashboard + audit + insights endpoints.
    from dashboard_routes import create_dashboard_router
    from audit_routes import create_audit_router
    from insights_routes import create_insights_router

    app.include_router(
        create_dashboard_router(),
        prefix="/v1",
    )
    app.include_router(
        create_audit_router(),
        prefix="/v1",
    )
    app.include_router(
        create_insights_router(),
        prefix="/v1",
    )

    # Bulk data import/export endpoints.
    from bulk_data import create_bulk_router

    app.include_router(
        create_bulk_router(),
        prefix="/v1",
    )

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
