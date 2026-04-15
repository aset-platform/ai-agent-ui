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
from langsmith.middleware import TracingMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from models import ChatRequest, ChatResponse
from slowapi.errors import RateLimitExceeded
from tools._ticker_linker import set_current_user

from auth.api import create_auth_router, get_ticker_router
from auth.dependencies import get_current_user
from auth.models import UserContext
from auth.rate_limit import limiter, rate_limit_exceeded_handler

_logger = logging.getLogger(__name__)


def create_app(
    agent_registry,
    executor,
    settings,
    token_budget=None,
    obs_collector=None,
    graph=None,
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
                warm_frequent_users,
                warm_shared,
                warm_tickers,
            )

            await warm_shared()

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
                target=lambda: asyncio.run(
                    warm_frequent_users(top_n)
                ),
                daemon=True,
                name="cache-warmup-users",
            ).start()
        except Exception:
            _logger.warning(
                "cache warm-up skipped",
                exc_info=True,
            )
        if not settings.jwt_secret_key:
            _logger.error(
                "JWT_SECRET_KEY not set" " — auth will fail",
            )

        # Start scheduler service
        if getattr(settings, "scheduler_enabled", True):
            try:
                from tools._stock_shared import (
                    _require_repo,
                )
                from jobs.scheduler_service import (
                    SchedulerService,
                )

                _sched_repo = _require_repo()
                max_w = getattr(
                    settings,
                    "scheduler_max_workers",
                    3,
                )
                svc = SchedulerService(
                    _sched_repo, max_workers=max_w,
                )
                svc.start()
                a.state.scheduler = svc
            except Exception:
                _logger.warning(
                    "Scheduler startup skipped",
                    exc_info=True,
                )

        yield

        # Shutdown: flush pending observability events
        # so no LLM usage data is lost on restart.
        if obs_collector is not None:
            obs_collector.flush_sync()

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
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' "
                "'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob: https:; "
                "connect-src 'self' "
                "http://localhost:* "
                "ws://localhost:*; "
                "font-src 'self';"
            )
            return response

    app.add_middleware(_SecurityHeadersMiddleware)

    # LangSmith: correlate HTTP requests with LLM traces.
    app.add_middleware(TracingMiddleware)

    # Rate limiting (slowapi).
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        rate_limit_exceeded_handler,
    )

    # ---------------------------------------------------------------
    # Usage tracking helper
    # ---------------------------------------------------------------

    async def _enforce_quota(user_id: str) -> None:
        """Raise 429 if user's monthly quota is used."""
        try:
            from usage_tracker import is_quota_exceeded

            if await is_quota_exceeded(user_id):
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Monthly analysis quota exceeded."
                        " Upgrade your plan for more."
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            _logger.error(
                "Quota check failed for %s",
                user_id,
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="Usage tracking unavailable",
            )

    async def _track_usage(user_id: str) -> None:
        """Increment monthly usage count."""
        try:
            from usage_tracker import increment_usage

            await increment_usage(user_id)
        except Exception:
            _logger.debug(
                "Usage tracking skipped for %s",
                user_id,
            )

    def _track_usage_sync(
        user_id: str,
        loop=None,
    ) -> None:
        """Sync wrapper for thread contexts.

        Uses ``run_coroutine_threadsafe`` on the uvicorn
        loop to avoid creating a separate event loop
        (which causes asyncpg connection pool errors).
        """
        import asyncio

        try:
            from usage_tracker import increment_usage

            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    increment_usage(user_id), loop,
                )
            else:
                asyncio.run(increment_usage(user_id))
        except Exception:
            _logger.debug(
                "Usage tracking skipped for %s",
                user_id,
            )

    # ---------------------------------------------------------------
    # Core route handlers — shared by root and /v1 mounts
    # ---------------------------------------------------------------

    async def _chat(
        req: ChatRequest,
        current_user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """Sync agent dispatch (POST /chat)."""
        req.user_id = current_user.user_id
        await _enforce_quota(req.user_id)

        # ── LangGraph path ────────────────────────
        if graph is not None and settings.use_langgraph:
            return await _chat_langgraph(req)

        # ── Legacy path ───────────────────────────
        from agents.router import route as _route

        resolved = _route(req.message)
        agent = agent_registry.get(resolved)
        if agent is None:
            raise HTTPException(
                status_code=404,
                detail=(f"Agent '{req.agent_id}' " "not found"),
            )
        try:

            def _run_with_user():
                set_current_user(req.user_id)
                return agent.run(
                    req.message, req.history,
                )

            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                executor, _run_with_user,
            )
            result = await asyncio.wait_for(
                future,
                timeout=(settings.agent_timeout_seconds),
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
        await _track_usage(req.user_id)
        return ChatResponse(
            response=result,
            agent_id=req.agent_id,
        )

    async def _chat_langgraph(req: ChatRequest):
        """Sync chat via LangGraph supervisor graph."""
        input_state = _build_graph_input(req)
        try:

            def _invoke_with_user():
                set_current_user(req.user_id)
                return graph.invoke(input_state)

            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                executor, _invoke_with_user,
            )
            result = await asyncio.wait_for(
                future,
                timeout=(settings.agent_timeout_seconds),
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail="Agent timed out",
            )
        except Exception as e:
            _logger.error(
                "LangGraph error: %s",
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Agent execution failed",
            )
        await _track_usage(req.user_id)
        return ChatResponse(
            response=result.get("final_response", ""),
            agent_id=result.get("current_agent", "graph"),
        )

    async def _chat_stream(
        req: ChatRequest,
        current_user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """NDJSON streaming (POST /chat/stream)."""
        req.user_id = current_user.user_id
        await _enforce_quota(req.user_id)
        _loop = asyncio.get_running_loop()

        # ── LangGraph path ────────────────────────
        if graph is not None and settings.use_langgraph:
            return _stream_langgraph(
                req, loop=_loop,
            )

        # ── Legacy path ───────────────────────────
        from agents.router import route as _route

        resolved = _route(req.message)
        agent = agent_registry.get(resolved)
        if agent is None:
            raise HTTPException(
                status_code=404,
                detail=(f"Agent '{req.agent_id}' " "not found"),
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
                    _track_usage_sync(
                        req.user_id, loop=_loop,
                    )
                except Exception as exc:
                    _logger.warning(
                        "Stream worker error: %s",
                        exc,
                        exc_info=True,
                    )
                    event_queue.put(
                        json.dumps(
                            {
                                "type": "error",
                                "message": str(exc),
                            }
                        )
                        + "\n"
                    )
                finally:
                    event_queue.put(None)

            worker = threading.Thread(
                target=run,
                daemon=True,
            )
            worker.start()
            yield from _drain_queue(
                event_queue,
                timeout,
            )
            worker.join(timeout=2)

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
        )

    # ---------------------------------------------------------------
    # Shared stream-queue drain
    # ---------------------------------------------------------------

    def _drain_queue(
        event_queue: queue.Queue,
        timeout: float,
    ):
        """Yield items from *event_queue* until done
        or *timeout* seconds elapse."""
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed >= timeout:
                yield json.dumps(
                    {
                        "type": "timeout",
                        "message": (f"Agent timed out" f" after {timeout}s"),
                    }
                ) + "\n"
                break
            remaining = timeout - elapsed
            try:
                item = event_queue.get(
                    timeout=min(remaining, 1.0),
                )
                if item is None:
                    break
                yield item
            except queue.Empty:
                continue

    # ---------------------------------------------------------------
    # LangGraph helpers
    # ---------------------------------------------------------------

    from user_context import build_user_context

    _build_user_context = build_user_context

    def _build_graph_input(req: ChatRequest) -> dict:
        """Build LangGraph AgentState input from req."""
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
        )

        msgs = []
        for h in req.history or []:
            role = h.get("role", "user")
            content = h.get("content", "")
            if role == "assistant":
                msgs.append(AIMessage(content=content))
            else:
                msgs.append(
                    HumanMessage(content=content),
                )
        msgs.append(
            HumanMessage(content=req.message),
        )

        user_ctx = _build_user_context(
            req.user_id or "",
        )

        return {
            "messages": msgs,
            "user_input": req.message,
            "user_id": req.user_id or "",
            "session_id": req.session_id or "",
            "history": req.history or [],
            "user_context": user_ctx,
            "intent": "",
            "next_agent": "",
            "current_agent": "",
            "tickers": [],
            "data_sources_used": [],
            "was_local_sufficient": True,
            "tool_events": [],
            "retrieved_memories": [],
            "final_response": "",
            "error": None,
            "start_time_ns": 0,
        }

    def _update_conversation_context(
        session_id: str,
        user_input: str,
        response: str,
        agent: str,
        intent: str,
        tickers: list[str],
        user_id: str,
    ) -> None:
        """Update or create conversation context."""
        if not session_id:
            return

        try:
            from agents.conversation_context import (
                ConversationContext,
                context_store,
                update_summary,
            )

            ctx = context_store.get(session_id)
            if ctx is None:
                # Try to resume from user's last session
                ctx = context_store.get_latest_for_user(
                    user_id,
                )
                if ctx is not None:
                    # Carry over context to new session
                    ctx.session_id = session_id
                    _logger.debug(
                        "Resumed context from prior "
                        "session for user %s",
                        user_id,
                    )
                else:
                    ctx = ConversationContext(
                        session_id=session_id,
                    )
                ctx.user_id = user_id
                # Populate user profile on first turn.
                try:
                    user_ctx = _build_user_context(
                        user_id,
                    )
                    ctx.user_tickers = user_ctx.get(
                        "tickers", [],
                    )
                    ctx.market_preference = user_ctx.get(
                        "market", "",
                    )
                    ctx.subscription_tier = user_ctx.get(
                        "tier", "",
                    )
                except Exception:
                    pass

            # Ensure user_id is always set
            if not ctx.user_id:
                ctx.user_id = user_id
            ctx.last_agent = agent
            ctx.last_intent = intent
            ctx.last_response = response[:500]
            ctx.current_topic = (
                f"{', '.join(tickers)} {intent}"
                if tickers else intent
            )
            for t in tickers:
                if t not in ctx.tickers_mentioned:
                    ctx.tickers_mentioned.append(t)

            # Update summary (non-blocking).
            try:
                update_summary(ctx, user_input, response)
            except Exception:
                ctx.turn_count += 1

            context_store.upsert(session_id, ctx)
        except Exception:
            _logger.debug(
                "Context update failed",
                exc_info=True,
            )

    def _stream_langgraph(
        req: ChatRequest, loop=None,
    ):
        """NDJSON streaming via LangGraph graph."""
        timeout = settings.agent_timeout_seconds
        input_state = _build_graph_input(req)

        def generate():
            event_queue: queue.Queue = queue.Queue()

            def run() -> None:
                set_current_user(req.user_id)
                try:
                    from agents.sub_agents import (
                        set_event_sink,
                    )

                    def _sink(ev):
                        event_queue.put(json.dumps(ev) + "\n")

                    set_event_sink(_sink)
                    try:
                        result = graph.invoke(
                            input_state,
                        )
                    finally:
                        set_event_sink(None)
                    # Update conversation context.
                    _update_conversation_context(
                        session_id=(
                            req.session_id or ""
                        ),
                        user_input=req.message,
                        response=result.get(
                            "final_response", "",
                        ),
                        agent=result.get(
                            "current_agent", "",
                        ),
                        intent=result.get(
                            "intent", "",
                        ),
                        tickers=result.get(
                            "tickers", [],
                        ),
                        user_id=req.user_id or "",
                    )
                    # Emit final
                    event_queue.put(
                        json.dumps(
                            {
                                "type": "final",
                                "response": result.get("final_response", ""),
                                "agent": result.get("current_agent", ""),
                            }
                        )
                        + "\n"
                    )
                    _track_usage_sync(
                        req.user_id, loop=loop,
                    )
                except Exception as exc:
                    event_queue.put(
                        json.dumps(
                            {
                                "type": "error",
                                "message": str(exc),
                            }
                        )
                        + "\n"
                    )
                finally:
                    event_queue.put(None)

            worker = threading.Thread(
                target=run,
                daemon=True,
            )
            worker.start()
            yield from _drain_queue(
                event_queue,
                timeout,
            )
            worker.join(timeout=2)

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
        )

    async def _pg_health() -> dict:
        """Check PostgreSQL connectivity."""
        try:
            from sqlalchemy import text

            from db.engine import get_engine

            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            return {"postgresql": "ok"}
        except Exception as exc:
            return {"postgresql": f"error: {exc}"}

    async def _health():
        """GET /health."""
        pg = await _pg_health()
        return {"status": "ok", **pg}

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
                user_id=None,
                days=30,
            )
            cascade_stats["requests_total"] = int(
                usage.get("total_requests", 0)
            )
        except Exception:
            _logger.debug(
                "Iceberg LLM usage fallback",
                exc_info=True,
            )

        result["cascade_stats"] = cascade_stats
        return result

    async def _admin_query_gaps(
        current_user=None,
    ):
        """GET /admin/query-gaps — data gap analysis."""
        try:
            from tools._stock_shared import (
                _require_repo,
            )

            repo = _require_repo()
        except Exception:
            return {
                "top_gap_tickers": [],
                "external_api_usage": {},
                "intent_distribution": {},
                "local_sufficiency_rate": 0,
            }

        # Top unresolved gaps
        gaps = repo.get_unfilled_data_gaps()
        top_gaps = sorted(
            gaps,
            key=lambda g: g.get(
                "query_count",
                0,
            ),
            reverse=True,
        )[:10]

        # Query log stats
        logs = []
        try:
            # Read recent logs (all users)
            tbl = repo._load_table(
                repo._QUERY_LOG,
            )
            scan = tbl.scan()
            df = scan.to_pandas()
            if not df.empty:
                logs = df.to_dict("records")
        except Exception:
            _logger.debug(
                "Query log scan failed",
                exc_info=True,
            )

        # External API usage
        yf_today = sum(
            1
            for rec in logs
            if "yfinance"
            in str(
                rec.get("data_sources_used", ""),
            )
        )
        serp_today = sum(
            1
            for rec in logs
            if "serpapi"
            in str(
                rec.get("data_sources_used", ""),
            )
        )

        # Intent distribution
        intents: dict[str, int] = {}
        for rec in logs:
            intent = rec.get(
                "classified_intent",
                "unknown",
            )
            intents[intent] = intents.get(intent, 0) + 1

        # Local sufficiency
        total = len(logs) or 1
        local_ok = sum(
            1 for rec in logs if rec.get("was_local_sufficient", False)
        )

        return {
            "top_gap_tickers": [
                {
                    "ticker": g.get("ticker", ""),
                    "query_count": g.get(
                        "query_count",
                        0,
                    ),
                    "data_type": g.get(
                        "data_type",
                        "",
                    ),
                }
                for g in top_gaps
            ],
            "external_api_usage": {
                "yfinance_calls": yf_today,
                "serpapi_calls": serp_today,
            },
            "intent_distribution": intents,
            "local_sufficiency_rate": round(
                local_ok / total,
                2,
            ),
        }

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

    async def _admin_daily_budget():
        """GET /admin/daily-budget — Groq token usage."""
        if token_budget is None:
            return {"error": "Token budget not available"}
        return token_budget.get_daily_budget()

    admin_router = APIRouter(prefix="/v1")
    admin_router.add_api_route(
        "/admin/daily-budget",
        _admin_daily_budget,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
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

    async def _admin_retention_selected(
        request: Request,
    ):
        """POST /admin/retention/selected."""
        body = await request.json()
        table_ids = body.get("table_ids", [])
        if not table_ids:
            return {"results": []}
        from stocks.retention import RetentionManager

        mgr = RetentionManager()
        results = mgr.run_cleanup_tables(
            table_ids,
            dry_run=False,
        )
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

    admin_router.add_api_route(
        "/admin/retention",
        _admin_retention,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/retention/selected",
        _admin_retention_selected,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/query-gaps",
        _admin_query_gaps,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )

    async def _admin_reset_usage():
        """POST /admin/reset-usage — zero monthly counts."""
        from usage_tracker import reset_monthly_usage

        count = await reset_monthly_usage()
        return {"reset_count": count}

    async def _admin_usage_stats():
        """GET /admin/usage-stats — all users + counts."""
        from usage_tracker import get_usage_stats

        return {"users": await get_usage_stats()}

    async def _admin_reset_selected(
        request: Request,
    ):
        """POST /admin/reset-usage/selected."""
        body = await request.json()
        user_ids = body.get("user_ids", [])
        if not user_ids:
            return {"reset_count": 0}
        from usage_tracker import reset_user_usage

        count = await reset_user_usage(user_ids)
        return {"reset_count": count}

    admin_router.add_api_route(
        "/admin/reset-usage",
        _admin_reset_usage,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/usage-stats",
        _admin_usage_stats,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/reset-usage/selected",
        _admin_reset_selected,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )

    async def _admin_usage_history(
        user_id: str | None = None,
        limit: int = 12,
    ):
        """GET /admin/usage-history."""
        from usage_tracker import get_usage_history

        return {
            "history": get_usage_history(
                user_id=user_id,
                limit=limit,
            ),
        }

    admin_router.add_api_route(
        "/admin/usage-history",
        _admin_usage_history,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )

    async def _admin_payment_txns(
        user_id: str | None = None,
        gateway: str | None = None,
        limit: int = 50,
    ):
        """GET /admin/payment-transactions."""
        from auth.endpoints.helpers import _get_repo
        from auth.repo import payment_repo
        from backend.db.engine import get_session_factory

        repo = _get_repo()

        # Build user_id → name/email lookup
        all_users = await repo.list_all()
        user_map: dict[str, dict] = {}
        for u in all_users:
            uid = u.get("user_id", "")
            user_map[uid] = {
                "name": u.get("full_name", ""),
                "email": u.get("email", ""),
            }

        # Read from PostgreSQL
        factory = get_session_factory()
        async with factory() as session:
            from sqlalchemy import select
            from backend.db.models.payment import (
                PaymentTransaction,
            )

            q = select(PaymentTransaction)
            if user_id:
                q = q.where(
                    PaymentTransaction.user_id == user_id
                )
            if gateway:
                q = q.where(
                    PaymentTransaction.gateway == gateway
                )
            q = q.order_by(
                PaymentTransaction.created_at.desc()
            ).limit(limit)
            result = await session.execute(q)
            txns = result.scalars().all()

        rows = []
        for t in txns:
            r = {
                c.name: getattr(t, c.name)
                for c in t.__table__.columns
            }
            if hasattr(r.get("created_at"), "isoformat"):
                r["created_at"] = (
                    r["created_at"].isoformat()
                )
            uid = r.get("user_id", "")
            info = user_map.get(uid, {})
            r["user_name"] = info.get("name", "")
            r["user_email"] = info.get("email", "")
            rows.append(r)
        return {"transactions": rows}

    admin_router.add_api_route(
        "/admin/payment-transactions",
        _admin_payment_txns,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )

    # ── Scheduler endpoints ─────────────────────────

    async def _scheduler_list_jobs(request: Request):
        """GET /admin/scheduler/jobs."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            return {"jobs": []}
        return {"jobs": svc.list_jobs()}

    async def _scheduler_create_job(
        request: Request,
    ):
        """POST /admin/scheduler/jobs."""
        body = await request.json()
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        cron_days = body.get("cron_days", [])
        if isinstance(cron_days, list):
            cron_days = ",".join(cron_days)
        cron_dates = body.get("cron_dates", [])
        if isinstance(cron_dates, list):
            cron_dates = ",".join(
                str(d) for d in cron_dates
            )
        job = {
            "name": body.get("name", "Untitled"),
            "job_type": body.get(
                "job_type", "data_refresh",
            ),
            "cron_days": cron_days,
            "cron_dates": cron_dates or "",
            "cron_time": body.get("cron_time", "18:00"),
            "scope": body.get("scope", "all"),
        }
        job_id = svc.add_job(job)
        return {"job_id": job_id, "detail": "created"}

    async def _scheduler_update_job(
        request: Request, job_id: str,
    ):
        """PATCH /admin/scheduler/jobs/{job_id}."""
        body = await request.json()
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        if "enabled" in body:
            svc.toggle_job(job_id, body["enabled"])
        else:
            updates = {}
            for k in (
                "name", "cron_days", "cron_dates",
                "cron_time", "scope",
            ):
                if k in body:
                    v = body[k]
                    if k == "cron_days" and isinstance(
                        v, list,
                    ):
                        v = ",".join(v)
                    if k == "cron_dates" and isinstance(
                        v, list,
                    ):
                        v = ",".join(
                            str(d) for d in v
                        )
                    updates[k] = v
            if updates:
                svc.update_job(job_id, updates)
        return {"detail": "updated"}

    async def _scheduler_delete_job(
        request: Request, job_id: str,
    ):
        """DELETE /admin/scheduler/jobs/{job_id}."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        svc.remove_job(job_id)
        return {"detail": "deleted"}

    async def _scheduler_trigger_job(
        request: Request, job_id: str,
    ):
        """POST /admin/scheduler/jobs/{job_id}/trigger."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        force = body.get("force", False)
        run_id = svc.trigger_now(
            job_id,
            trigger_type="manual",
            force=force,
        )
        if not run_id:
            raise HTTPException(
                404, "Job not found or no executor",
            )
        return {"run_id": run_id, "detail": "triggered"}

    async def _scheduler_cancel_run(
        request: Request, run_id: str,
    ):
        """POST /admin/scheduler/runs/{run_id}/cancel."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        ok = svc.cancel_run(run_id)
        if not ok:
            raise HTTPException(
                404, "Run not found or already finished",
            )
        return {"detail": "cancel signal sent"}

    async def _scheduler_list_runs(request: Request):
        """GET /admin/scheduler/runs."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            return {"runs": [], "total": 0}
        params = request.query_params
        days = int(params.get("days", "7"))
        job_type = params.get("job_type") or None
        status = params.get("status") or None
        p_run_id = (
            params.get("pipeline_run_id") or None
        )
        offset = int(params.get("offset", "0"))
        limit = int(params.get("limit", "50"))
        runs = svc._repo.get_scheduler_runs(
            days=days,
            job_type=job_type,
            status=status,
            pipeline_run_id=p_run_id,
            offset=offset,
            limit=limit,
        )
        total = (
            runs[0].get("_total", len(runs))
            if runs else 0
        )
        import math as _math

        for r in runs:
            r.pop("_total", None)
            for k, v in list(r.items()):
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif isinstance(v, float) and (
                    _math.isnan(v) or _math.isinf(v)
                ):
                    r[k] = None
        return {"runs": runs, "total": total}

    # ── Pipeline endpoints ─────────────────────

    async def _pipeline_list(request: Request):
        """GET /admin/scheduler/pipelines."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            return {"pipelines": []}
        return {"pipelines": svc.list_pipelines()}

    async def _pipeline_create(request: Request):
        """POST /admin/scheduler/pipelines."""
        body = await request.json()
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        cron_days = body.get("cron_days", [])
        if isinstance(cron_days, list):
            cron_days = ",".join(cron_days)
        cron_dates = body.get("cron_dates", [])
        if isinstance(cron_dates, list):
            cron_dates = ",".join(
                str(d) for d in cron_dates
            )
        data = {
            "name": body.get("name", "Untitled"),
            "scope": body.get("scope", "all"),
            "enabled": body.get("enabled", True),
            "cron_days": cron_days,
            "cron_dates": cron_dates or "",
            "cron_time": body.get(
                "cron_time", "18:00",
            ),
            "steps": body.get("steps", []),
        }
        pid = svc.add_pipeline(data)
        return {
            "pipeline_id": pid, "detail": "created",
        }

    async def _pipeline_update(
        request: Request, pipeline_id: str,
    ):
        """PATCH /admin/scheduler/pipelines/{id}."""
        body = await request.json()
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        updates = {}
        for k in (
            "name", "scope", "enabled",
            "cron_days", "cron_time", "cron_dates",
            "steps",
        ):
            if k in body:
                v = body[k]
                if k == "cron_days" and isinstance(
                    v, list,
                ):
                    v = ",".join(v)
                if k == "cron_dates" and isinstance(
                    v, list,
                ):
                    v = ",".join(str(d) for d in v)
                updates[k] = v
        if updates:
            svc.update_pipeline(pipeline_id, updates)
        return {"detail": "updated"}

    async def _pipeline_delete(
        request: Request, pipeline_id: str,
    ):
        """DELETE /admin/scheduler/pipelines/{id}."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        svc.remove_pipeline(pipeline_id)
        return {"detail": "deleted"}

    async def _pipeline_trigger(
        request: Request, pipeline_id: str,
    ):
        """POST /admin/scheduler/pipelines/{id}/trigger."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        force = body.get("force", False)
        tag = svc.trigger_pipeline_now(
            pipeline_id, force=force,
        )
        if not tag:
            raise HTTPException(
                404, "Pipeline not found",
            )
        return {"pipeline_id": pipeline_id, "tag": tag}

    async def _pipeline_resume(
        request: Request, pipeline_id: str,
    ):
        """POST .../pipelines/{id}/resume."""
        body = await request.json()
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            raise HTTPException(
                503, "Scheduler not available",
            )
        from_step = body.get("from_step", 1)
        tag = svc.resume_pipeline_now(
            pipeline_id, from_step,
        )
        if not tag:
            raise HTTPException(
                404, "Pipeline not found",
            )
        return {
            "pipeline_id": pipeline_id,
            "from_step": from_step,
            "tag": tag,
        }

    async def _scheduler_stats(request: Request):
        """GET /admin/scheduler/stats."""
        svc = getattr(
            request.app.state, "scheduler", None,
        )
        if not svc:
            return {
                "active_jobs": 0,
                "next_run_label": None,
                "next_run_seconds": None,
                "last_run_status": None,
                "last_run_ago": None,
                "last_run_tickers": None,
                "runs_today": 0,
                "runs_today_success": 0,
                "runs_today_failed": 0,
                "runs_today_running": 0,
            }
        stats = svc.get_stats()
        # Sanitise NaN for JSON
        import math as _math

        for k, v in list(stats.items()):
            if isinstance(v, float) and (
                _math.isnan(v) or _math.isinf(v)
            ):
                stats[k] = None
            elif hasattr(v, "isoformat"):
                stats[k] = v.isoformat()
        return stats

    admin_router.add_api_route(
        "/admin/scheduler/jobs",
        _scheduler_list_jobs,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/jobs",
        _scheduler_create_job,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/jobs/{job_id}",
        _scheduler_update_job,
        methods=["PATCH"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/jobs/{job_id}",
        _scheduler_delete_job,
        methods=["DELETE"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/jobs/{job_id}/trigger",
        _scheduler_trigger_job,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/runs/{run_id}/cancel",
        _scheduler_cancel_run,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/runs",
        _scheduler_list_runs,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/stats",
        _scheduler_stats,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )

    # ── Pipeline routes ───────────────────────
    admin_router.add_api_route(
        "/admin/scheduler/pipelines",
        _pipeline_list,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/pipelines",
        _pipeline_create,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/pipelines/{pipeline_id}",
        _pipeline_update,
        methods=["PATCH"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/pipelines/{pipeline_id}",
        _pipeline_delete,
        methods=["DELETE"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/pipelines"
        "/{pipeline_id}/trigger",
        _pipeline_trigger,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/scheduler/pipelines"
        "/{pipeline_id}/resume",
        _pipeline_resume,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )

    # ── Data health check ──────────────────────

    async def _admin_data_health():
        """GET /admin/data-health — data quality status."""
        from datetime import datetime, timedelta, timezone

        from db.duckdb_engine import query_iceberg_df
        from tools._stock_shared import _require_repo

        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        stale_3d = today - timedelta(days=3)
        stale_30d = today - timedelta(days=30)
        stale_7d = today - timedelta(days=7)

        # ── Invalidate DuckDB cache so health reads
        # pick up recent writes (e.g. fix runs). ──
        from db.duckdb_engine import (
            invalidate_metadata,
        )

        invalidate_metadata()

        # ── Registry baseline ────────────────────
        try:
            repo = _require_repo()
            registry = repo.get_all_registry()
            all_tickers = set(registry.keys())
            total_registry = len(all_tickers)
            # Analyzable: stocks + ETFs (for
            # analytics, sentiment, forecasts)
            analyzable_tickers = {
                t
                for t in all_tickers
                if registry[t].get(
                    "ticker_type", "stock",
                )
                in ("stock", "etf")
            }
            # Financials: stocks only (for Piotroski)
            financial_tickers = {
                t
                for t in all_tickers
                if registry[t].get(
                    "ticker_type", "stock",
                )
                == "stock"
            }
        except Exception:
            all_tickers = set()
            analyzable_tickers = set()
            financial_tickers = set()
            total_registry = 0

        result: dict = {
            "total_registry": total_registry,
            "total_analyzable": len(
                analyzable_tickers,
            ),
            "total_financial": len(
                financial_tickers,
            ),
            "timestamp": time.time(),
        }

        # ── Parallel DuckDB queries ──────────────
        # Run all 5 health checks concurrently via
        # ThreadPoolExecutor (DuckDB queries are
        # CPU-bound but release the GIL for I/O).
        from concurrent.futures import (
            ThreadPoolExecutor,
        )

        def _ohlcv_health():
            """Combined OHLCV: NaN + freshness."""
            o: dict = {
                "nan_close_count": 0,
                "nan_close_tickers": [],
                "missing_latest_count": 0,
                "stale_count": 0,
                "stale_tickers": [],
            }
            try:
                # Single query for NaN stats
                nan_df = query_iceberg_df(
                    "stocks.ohlcv",
                    "SELECT count(*) AS cnt, "
                    "count(DISTINCT ticker) "
                    "  AS tk_cnt "
                    "FROM ohlcv "
                    "WHERE close IS NULL "
                    "OR isnan(close)",
                )
                if not nan_df.empty:
                    o["nan_close_count"] = int(
                        nan_df["cnt"].iloc[0]
                    )
                if (
                    not nan_df.empty
                    and nan_df["tk_cnt"].iloc[0] > 0
                ):
                    tk = query_iceberg_df(
                        "stocks.ohlcv",
                        "SELECT DISTINCT ticker "
                        "FROM ohlcv "
                        "WHERE close IS NULL "
                        "OR isnan(close)",
                    )
                    if not tk.empty:
                        o["nan_close_tickers"] = (
                            sorted(
                                tk["ticker"].tolist()
                            )
                        )

                # Freshness per ticker
                latest = query_iceberg_df(
                    "stocks.ohlcv",
                    "SELECT ticker, "
                    "MAX(date) AS latest "
                    "FROM ohlcv "
                    "GROUP BY ticker",
                )
                if not latest.empty:
                    for _, row in (
                        latest.iterrows()
                    ):
                        d = row["latest"]
                        if hasattr(d, "date"):
                            d = d.date()
                        if d < yesterday:
                            o[
                                "missing_latest_count"
                            ] += 1
                        if d < stale_3d:
                            o["stale_count"] += 1
                            o[
                                "stale_tickers"
                            ].append(row["ticker"])
                    o["stale_tickers"] = sorted(
                        o["stale_tickers"]
                    )
            except Exception:
                _logger.debug(
                    "OHLCV health failed",
                    exc_info=True,
                )
            return o

        def _forecast_health():
            f: dict = {
                "total_tickers": 0,
                "missing_tickers": [],
                "extreme_predictions": 0,
                "high_mape": 0,
                "stale_count": 0,
            }
            try:
                # Use latest run per ticker (by computed_at)
                # to avoid old extreme values inflating counts.
                df = query_iceberg_df(
                    "stocks.forecast_runs",
                    "SELECT * FROM ("
                    "  SELECT ticker, run_date,"
                    "    target_3m_pct_change,"
                    "    mape, computed_at,"
                    "    ROW_NUMBER() OVER ("
                    "      PARTITION BY ticker"
                    "      ORDER BY computed_at DESC"
                    "    ) AS rn"
                    "  FROM forecast_runs"
                    ") WHERE rn = 1",
                )
                if not df.empty:
                    tks = set(
                        df["ticker"].tolist()
                    )
                    f["total_tickers"] = len(tks)
                    f["missing_tickers"] = sorted(
                        analyzable_tickers - tks
                    )
                    for _, r in df.iterrows():
                        pct = r.get(
                            "target_3m_pct_change",
                        )
                        if pct is not None and (
                            pct > 50 or pct < -50
                        ):
                            f[
                                "extreme_predictions"
                            ] += 1
                        mp = r.get("mape")
                        if (
                            mp is not None
                            and mp > 25
                        ):
                            f["high_mape"] += 1
                        d = r["run_date"]
                        if hasattr(d, "date"):
                            d = d.date()
                        if d < stale_30d:
                            f["stale_count"] += 1
            except Exception:
                _logger.debug(
                    "Forecast health failed",
                    exc_info=True,
                )
            return f

        def _sentiment_health():
            s: dict = {
                "total_tickers": 0,
                "missing_tickers": [],
                "stale_count": 0,
            }
            try:
                df = query_iceberg_df(
                    "stocks.sentiment_scores",
                    "SELECT ticker, "
                    "MAX(score_date) AS latest "
                    "FROM sentiment_scores "
                    "GROUP BY ticker",
                )
                if not df.empty:
                    tks = set(
                        df["ticker"].tolist()
                    )
                    s["total_tickers"] = len(tks)
                    s["missing_tickers"] = sorted(
                        analyzable_tickers - tks
                    )
                    for _, r in df.iterrows():
                        d = r["latest"]
                        if hasattr(d, "date"):
                            d = d.date()
                        if d < stale_7d:
                            s["stale_count"] += 1
            except Exception:
                _logger.debug(
                    "Sentiment health failed",
                    exc_info=True,
                )
            return s

        def _piotroski_health():
            p: dict = {
                "total_tickers": 0,
                "missing_tickers": [],
                "stale_count": 0,
            }
            try:
                df = query_iceberg_df(
                    "stocks.piotroski_scores",
                    "SELECT ticker, "
                    "MAX(score_date) AS latest "
                    "FROM piotroski_scores "
                    "GROUP BY ticker",
                )
                if not df.empty:
                    tks = set(
                        df["ticker"].tolist()
                    )
                    p["total_tickers"] = len(tks)
                    p["missing_tickers"] = sorted(
                        financial_tickers - tks
                    )
                    for _, r in df.iterrows():
                        d = r["latest"]
                        if hasattr(d, "date"):
                            d = d.date()
                        if d < stale_30d:
                            p["stale_count"] += 1
            except Exception:
                _logger.debug(
                    "Piotroski health failed",
                    exc_info=True,
                )
            return p

        def _analytics_health():
            a: dict = {
                "total_tickers": 0,
                "missing_tickers": [],
            }
            try:
                df = query_iceberg_df(
                    "stocks.analysis_summary",
                    "SELECT DISTINCT ticker "
                    "FROM analysis_summary",
                )
                if not df.empty:
                    tks = set(
                        df["ticker"].tolist()
                    )
                    a["total_tickers"] = len(tks)
                    a["missing_tickers"] = sorted(
                        analyzable_tickers - tks
                    )
            except Exception:
                _logger.debug(
                    "Analytics health failed",
                    exc_info=True,
                )
            return a

        with ThreadPoolExecutor(
            max_workers=5,
        ) as pool:
            f_ohlcv = pool.submit(_ohlcv_health)
            f_fc = pool.submit(_forecast_health)
            f_sent = pool.submit(_sentiment_health)
            f_pio = pool.submit(_piotroski_health)
            f_ana = pool.submit(_analytics_health)

        result["ohlcv"] = f_ohlcv.result()
        result["forecasts"] = f_fc.result()
        result["sentiment"] = f_sent.result()
        result["piotroski"] = f_pio.result()
        result["analytics"] = f_ana.result()

        return result

    async def _admin_fix_ohlcv(
        request: Request,
    ):
        """POST /admin/data-health/fix-ohlcv."""
        from datetime import datetime, timedelta, timezone

        body = await request.json()
        action = body.get("action")
        if action not in (
            "backfill_nan",
            "backfill_missing",
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "action must be "
                    "'backfill_nan' or "
                    "'backfill_missing'"
                ),
            )

        from tools._stock_shared import _require_repo

        repo = _require_repo()

        if action == "backfill_nan":
            from pyiceberg.expressions import (
                IsNaN,
                IsNull,
                Or,
            )

            expr = Or(
                IsNull("close"),
                IsNaN("close"),
            )
            repo.delete_rows("stocks.ohlcv", expr)
            return {
                "action": "backfill_nan",
                "status": "deleted",
                "detail": (
                    "Removed OHLCV rows with "
                    "NULL/NaN close values."
                ),
            }

        # backfill_missing
        from db.duckdb_engine import query_iceberg_df

        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        registry = repo.get_all_registry()

        latest_df = query_iceberg_df(
            "stocks.ohlcv",
            "SELECT ticker, MAX(date) AS latest "
            "FROM ohlcv GROUP BY ticker",
        )
        have_latest: set[str] = set()
        if not latest_df.empty:
            for _, row in latest_df.iterrows():
                d = row["latest"]
                if hasattr(d, "date"):
                    d = d.date()
                if d >= yesterday:
                    have_latest.add(row["ticker"])

        missing = sorted(
            set(registry.keys()) - have_latest
        )
        if not missing:
            return {
                "action": "backfill_missing",
                "status": "ok",
                "detail": "No missing tickers found.",
                "count": 0,
            }

        # Resolve yfinance tickers from registry
        yf_map: dict[str, str] = {}
        for tk in missing:
            meta = registry.get(tk, {})
            yf_tk = meta.get("yf_ticker", tk)
            yf_map[tk] = yf_tk

        import yfinance as yf

        yf_tickers = list(yf_map.values())
        # Batch download last 5 days to cover gaps
        start = yesterday - timedelta(days=5)
        try:
            raw = yf.download(
                yf_tickers,
                start=str(start),
                end=str(today + timedelta(days=1)),
                group_by="ticker",
                auto_adjust=True,
                threads=True,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"yfinance download failed: {exc}",
            )

        inserted = 0
        errors: list[str] = []
        for canon_tk, yf_tk in yf_map.items():
            try:
                if len(yf_tickers) == 1:
                    df = raw.copy()
                else:
                    df = raw[yf_tk].copy()
                df = df.dropna(subset=["Close"])
                if df.empty:
                    continue
                # Rename to match Iceberg schema
                df = df.rename(
                    columns={
                        "Open": "open",
                        "High": "high",
                        "Low": "low",
                        "Close": "close",
                        "Volume": "volume",
                    },
                )
                repo.insert_ohlcv(canon_tk, df)
                inserted += 1
            except Exception as exc:
                errors.append(
                    f"{canon_tk}: {exc}"
                )
        return {
            "action": "backfill_missing",
            "status": "done",
            "tickers_backfilled": inserted,
            "tickers_attempted": len(missing),
            "errors": errors[:20],
        }

    admin_router.add_api_route(
        "/admin/data-health",
        _admin_data_health,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/data-health/fix-ohlcv",
        _admin_fix_ohlcv,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )

    # ── Data Health Fix (unified) ─────────────

    _VALID_TARGETS = {
        "ohlcv",
        "analytics",
        "sentiment",
        "piotroski",
        "forecasts",
    }

    async def _admin_data_health_fix(
        request: Request,
    ):
        """POST /admin/data-health/fix.

        Triggers the same pipeline executors that the
        scheduler uses.  Returns a run_id immediately;
        caller polls /fix/{run_id}/status for progress.
        """
        import threading
        import uuid
        from datetime import datetime, timezone

        from tools._stock_shared import _require_repo

        body = await request.json()
        target = body.get("target")
        mode = body.get("mode", "stale_only")

        if target not in _VALID_TARGETS:
            raise HTTPException(
                400,
                "target must be one of: "
                + ", ".join(sorted(_VALID_TARGETS)),
            )
        if mode not in ("stale_only", "force_all"):
            raise HTTPException(
                400,
                "mode must be 'stale_only' or "
                "'force_all'",
            )

        repo = _require_repo()

        # Job type mapping
        job_type_map = {
            "ohlcv": "data_refresh",
            "analytics": "compute_analytics",
            "sentiment": "run_sentiment",
            "piotroski": "run_piotroski",
            "forecasts": "run_forecasts",
        }
        job_type = job_type_map[target]
        force = mode == "force_all"

        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        repo.append_scheduler_run(
            {
                "run_id": run_id,
                "job_id": f"fix-{target}",
                "job_name": f"Fix {target}",
                "job_type": job_type,
                "scope": "all",
                "status": "running",
                "started_at": now,
                "completed_at": None,
                "duration_secs": None,
                "tickers_total": 0,
                "tickers_done": 0,
                "error_message": None,
                "trigger_type": "fix_panel",
            }
        )

        def _run():
            started = datetime.now(timezone.utc)
            try:
                if target == "ohlcv":
                    _run_ohlcv_fix(
                        repo, run_id, force,
                    )
                else:
                    from backend.jobs.executor import (
                        JOB_EXECUTORS,
                    )

                    fn = JOB_EXECUTORS.get(job_type)
                    if fn:
                        fn(
                            "all",
                            run_id,
                            repo,
                            force=force,
                        )
            except Exception as exc:
                elapsed = (
                    datetime.now(timezone.utc) - started
                ).total_seconds()
                repo.update_scheduler_run(
                    run_id,
                    {
                        "status": "failed",
                        "completed_at": datetime.now(
                            timezone.utc,
                        ),
                        "duration_secs": elapsed,
                        "error_message": str(exc)[:500],
                    },
                )

        threading.Thread(
            target=_run, daemon=True,
        ).start()

        return {"run_id": run_id, "status": "running"}

    def _run_ohlcv_fix(
        repo, run_id: str, force: bool,
    ):
        """Run OHLCV fix using batch_data_refresh.

        In stale_only mode, queries Iceberg for stale
        tickers and passes only those.  In force mode,
        passes all registry tickers.
        """
        from datetime import date, timedelta

        from backend.jobs.batch_refresh import (
            batch_data_refresh,
        )
        from market_utils import is_indian_market

        registry = repo.get_all_registry()

        if not force:
            # Identify stale tickers from Iceberg
            from db.duckdb_engine import (
                query_iceberg_df,
            )

            today = date.today()
            stale_3d = today - timedelta(days=3)
            stale_tickers: list[str] = []
            try:
                df = query_iceberg_df(
                    "stocks.ohlcv",
                    "SELECT ticker, "
                    "MAX(date) AS latest "
                    "FROM ohlcv GROUP BY ticker",
                )
                if not df.empty:
                    for _, row in df.iterrows():
                        d = row["latest"]
                        if hasattr(d, "date"):
                            d = d.date()
                        if d < stale_3d:
                            stale_tickers.append(
                                row["ticker"]
                            )
            except Exception:
                pass

            # Also include tickers with no OHLCV at all
            ohlcv_set = set()
            if not df.empty:
                ohlcv_set = set(
                    df["ticker"].tolist()
                )
            reg_set = set(registry.keys())
            missing = reg_set - ohlcv_set

            all_targets = set(stale_tickers) | missing
            if not all_targets:
                # Nothing stale — mark done
                repo.update_scheduler_run(
                    run_id,
                    {
                        "status": "success",
                        "completed_at": (
                            __import__("datetime")
                            .datetime.now(
                                __import__("datetime")
                                .timezone.utc
                            )
                        ),
                        "tickers_total": 0,
                        "tickers_done": 0,
                    },
                )
                return
            tickers = sorted(all_targets)
        else:
            tickers = list(registry.keys())

        # Resolve to yfinance symbols
        yf_tickers: list[str] = []
        for t in tickers:
            if t.endswith((".NS", ".BO")):
                yf_tickers.append(t)
            else:
                meta = registry.get(t, {})
                mkt = meta.get("market", "")
                if mkt.upper() in (
                    "NSE", "BSE", "INDIA",
                ):
                    yf_tickers.append(f"{t}.NS")
                else:
                    yf_tickers.append(t)

        batch_data_refresh(
            yf_tickers,
            repo,
            run_id,
            max_workers=5,
            force=True,
        )

    async def _admin_fix_status(
        request: Request,
        run_id: str,
    ):
        """GET /admin/data-health/fix/{run_id}/status."""
        from tools._stock_shared import _require_repo

        repo = _require_repo()
        run = repo.get_scheduler_run_by_id(run_id)
        if not run:
            raise HTTPException(
                404, "Run not found",
            )
        return {
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "tickers_total": run.get(
                "tickers_total", 0,
            ),
            "tickers_done": run.get(
                "tickers_done", 0,
            ),
            "errors": run.get("error_message"),
            "elapsed_s": run.get("duration_secs"),
        }

    admin_router.add_api_route(
        "/admin/data-health/fix",
        _admin_data_health_fix,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/data-health/fix/{run_id}/status",
        _admin_fix_status,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )

    # ── Ollama local-LLM management ────────────

    async def _admin_ollama_status():
        """GET /admin/ollama/status."""
        from ollama_manager import (
            get_ollama_manager,
        )

        return get_ollama_manager().get_status()

    async def _admin_ollama_load(
        request: Request,
    ):
        """POST /admin/ollama/load."""
        from ollama_manager import (
            get_ollama_manager,
        )

        body = await request.json()
        profile = body.get(
            "profile",
            "reasoning",
        )
        mgr = get_ollama_manager()
        if not mgr.is_available():
            raise HTTPException(
                status_code=503,
                detail="Ollama server not reachable",
            )
        return mgr.load_profile(profile)

    async def _admin_ollama_unload():
        """POST /admin/ollama/unload."""
        from ollama_manager import (
            get_ollama_manager,
        )

        mgr = get_ollama_manager()
        if not mgr.is_available():
            raise HTTPException(
                status_code=503,
                detail="Ollama server not reachable",
            )
        return mgr.unload_all()

    admin_router.add_api_route(
        "/admin/ollama/status",
        _admin_ollama_status,
        methods=["GET"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/ollama/load",
        _admin_ollama_load,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )
    admin_router.add_api_route(
        "/admin/ollama/unload",
        _admin_ollama_unload,
        methods=["POST"],
        dependencies=[Depends(superuser_only)],
    )

    app.include_router(admin_router)

    # Dashboard + audit + insights + recommendation endpoints.
    from audit_routes import create_audit_router
    from dashboard_routes import create_dashboard_router
    from insights_routes import create_insights_router
    from recommendation_routes import (
        create_recommendation_router,
    )

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
    app.include_router(
        create_recommendation_router(),
        prefix="/v1",
    )
    from market_routes import create_market_router

    app.include_router(
        create_market_router(),
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

    register_ws_routes(
        app,
        agent_registry,
        executor,
        settings,
        graph=graph,
    )

    # Serve uploaded avatars.
    from paths import AVATARS_DIR, ensure_dirs

    ensure_dirs()
    app.mount(
        "/avatars",
        StaticFiles(directory=str(AVATARS_DIR)),
        name="avatars",
    )

    return app
