"""Paper-trading routes.

Slice 8b: GET /events (events timeline).
Slice 8c: POST /runs (start), DELETE /runs/{strategy_id} (stop),
          GET /runs (list active).
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
)
from pydantic import BaseModel, Field
from typing import Literal

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)


class StartRunRequest(BaseModel):
    strategy_id: UUID
    fixture_path: str = Field(
        default="", max_length=200,
    )
    """Replay fixture filename. Required when source='replay'."""
    source: Literal["replay", "live-ws"] = "replay"
    """Tick source: 'replay' uses a JSONL fixture; 'live-ws' streams
    from the user's connected Kite WebSocket multiplexer."""
    mode: Literal["paper", "live"] = "paper"
    """Runtime selector — 'paper' → PaperRuntime; 'live' →
    LiveRuntime (requires algo.live_caps.live_orders_enabled
    for the strategy). The ALGO_LIVE_DRY_RUN env variable then
    decides whether KiteAdapter short-circuits to synthetic
    responses (dry-run) or hits real Kite (real money)."""
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"), ge=Decimal("1000.00"),
    )


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


async def _build_live_ws_source(
    *,
    user: UserContext,
    strategy,  # Strategy AST
    session_factory,
) -> Any:
    """Build a LiveWsTickSource for the user's Kite WS multiplexer.

    Steps:
    1. Load Kite credentials (api_key + access_token) from DB.
    2. Verify the token is not expired.
    3. Resolve tickers from the user's PG portfolio / watchlist.
    4. Look up instrument tokens for those tickers.
    5. Get-or-create the per-user KiteWsMultiplexer.
    6. Subscribe the strategy; return a LiveWsTickSource.

    Raises HTTPException(400/503) on any failure.
    """
    from backend.algo.broker.credentials_repo import (
        BrokerCredentialsRepo,
    )
    from backend.algo.broker.ws_registry import (
        get_or_create_multiplexer,
    )
    from backend.algo.instruments.repo import InstrumentsRepo
    from backend.algo.stream.sources import LiveWsTickSource

    user_id = UUID(user.user_id)

    # --- 1. Credentials ------------------------------------------------
    creds_repo = BrokerCredentialsRepo()
    async with session_factory() as session:
        creds = await creds_repo.load(session, user_id)
    if creds is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Kite credentials found. "
                "Please connect Zerodha first."
            ),
        )
    if creds.get("access_token_expired"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Kite access token expired. "
                "Please re-authenticate with Zerodha."
            ),
        )
    api_key = creds["api_key"]
    access_token = creds["access_token"]
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Kite access token. "
                "Please complete the OAuth handshake."
            ),
        )

    # --- 2. Resolve tickers from user scope ---------------------------
    # Reuse the v1 ``_scoped_tickers`` helper (same one Insights
    # tabs use) — gives us watchlist ∪ holdings without the
    # raw-SQL detour.  Even if the strategy AST asks for
    # ``scope=discovery``, live-WS is capped at the user's curated
    # set: subscribing to thousands of NSE tokens is impractical.
    from backend.insights_routes import _scoped_tickers
    tickers = await _scoped_tickers(user, "watchlist")
    if not tickers:
        raise HTTPException(
            status_code=400,
            detail=(
                "No instruments found for live-WS source. "
                "Add tickers to your portfolio or watchlist."
            ),
        )

    # --- 3. Token lookup ----------------------------------------------
    instr_repo = InstrumentsRepo()
    async with session_factory() as session:
        token_to_ticker = await instr_repo.get_tokens_for_tickers(
            session, tickers,
        )
    if not token_to_ticker:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Kite instrument tokens found for your tickers. "
                "Ensure the instruments master is loaded."
            ),
        )
    tokens = list(token_to_ticker.keys())

    # --- 4. Multiplexer -----------------------------------------------
    try:
        mux = await get_or_create_multiplexer(
            user_id=user_id,
            api_key=api_key,
            access_token=access_token,
        )
    except Exception as exc:
        _logger.exception(
            "live-ws mux init failed user=%s", user_id,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Failed to start live tick source: {exc}",
        )

    # --- 5. Subscribe strategy ----------------------------------------
    queue = mux.subscribe(
        strategy.id, tokens, token_to_ticker,
    )
    return LiveWsTickSource(
        user_id=user_id,
        strategy_id=strategy.id,
        queue=queue,
        mux=mux,
    )




def create_paper_router() -> APIRouter:
    router = APIRouter(prefix="/algo/paper", tags=["algo-trading"])

    @router.get("/events")
    async def list_events(
        response: Response,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        type: str | None = Query(
            None,
            description=(
                "Filter by event type "
                "(e.g. position_drift_detected)"
            ),
        ),
        mode: str | None = Query(
            None,
            description=(
                "Filter by run mode: 'paper', 'live', "
                "or 'backtest'. Combines with type."
            ),
        ),
        dry_run: bool | None = Query(
            None,
            description=(
                "Filter by payload.dry_run: pass true to see "
                "only dry-run orders (LiveRuntime stamps the "
                "flag on every event); false for real-money "
                "live orders. Omit for both."
            ),
        ),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Recent algo events for the caller (newest first).

        Returns both ``mode='paper'`` and ``mode='live'``
        events so the reconciliation drift panel can poll
        for ``type=position_drift_detected`` regardless of
        which mode emitted them.

        ``X-Total-Count`` response header carries the unfiltered-
        by-pagination total so the frontend pager can render
        ``Page N / M``.
        """
        from backend.db.duckdb_engine import query_iceberg_table
        user_id_str = str(UUID(user.user_id))

        clauses = ["user_id = ?"]
        base_params: list = [user_id_str]
        if type is not None:
            clauses.append("type = ?")
            base_params.append(type)
        if mode is not None:
            clauses.append("mode = ?")
            base_params.append(mode)
        if dry_run is not None:
            # ``payload_json`` is stored as text JSON; DuckDB's
            # JSON funcs read string-typed members. LiveRuntime
            # stamps ``dry_run`` as a JSON bool so we extract a
            # string and compare against 'true'/'false'.
            wanted = "true" if dry_run else "false"
            clauses.append(
                "json_extract_string(payload_json, "
                "'$.dry_run') = ?"
            )
            base_params.append(wanted)
        where = " AND ".join(clauses)

        sql = (
            f"SELECT event_id, ts_ns, ts_date, "
            f"       strategy_id, type, payload_json "
            f"FROM events WHERE {where} "
            f"ORDER BY ts_ns DESC "
            f"LIMIT ? OFFSET ?"
        )
        params = base_params + [limit, offset]
        count_sql = f"SELECT COUNT(*) AS n FROM events WHERE {where}"
        count_params = base_params

        try:
            rows = query_iceberg_table(
                "algo.events", sql, params,
            )
        except FileNotFoundError:
            response.headers["X-Total-Count"] = "0"
            response.headers["Access-Control-Expose-Headers"] = (
                "X-Total-Count"
            )
            return []

        # Total respects the type filter but ignores limit/offset
        # so the pager can render Page N / M correctly.
        try:
            count_rows = query_iceberg_table(
                "algo.events", count_sql, count_params,
            )
            total = int(count_rows[0]["n"]) if count_rows else 0
        except Exception:  # noqa: BLE001
            total = 0

        response.headers["X-Total-Count"] = str(total)
        response.headers["Access-Control-Expose-Headers"] = (
            "X-Total-Count"
        )

        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:  # noqa: BLE001
                payload = {}
            out.append({
                "event_id": r["event_id"],
                "ts_ns": int(r["ts_ns"]),
                "ts_date": r["ts_date"],
                "strategy_id": r.get("strategy_id"),
                "type": r["type"],
                "payload": payload,
            })
        return out

    @router.post("/runs", status_code=201)
    async def start_run(
        body: StartRunRequest,
        user: UserContext = Depends(pro_or_superuser),
    ):
        from backend.algo.paper.kill_switch_repo import (
            KillSwitchRepo,
        )
        from backend.algo.paper.supervisor import (
            build_replay_source, get_supervisor,
        )
        from backend.algo.redis_async import get_async_redis
        from backend.algo.strategy.repo import get_strategy

        user_id = UUID(user.user_id)
        factory = _get_session_factory()
        async with factory() as session:
            strategy = await get_strategy(
                session, user_id, body.strategy_id,
            )
        if strategy is None:
            raise HTTPException(
                status_code=404, detail="Strategy not found",
            )

        ks_repo = KillSwitchRepo(redis_client=get_async_redis())
        kill_active = await ks_repo.is_active(user_id)

        if body.source == "live-ws":
            # Resolve Kite credentials and get/create the user's
            # WS multiplexer.  The strategy universe tokens must
            # already be subscribed before starting the runtime.
            source = await _build_live_ws_source(
                user=user,
                strategy=strategy,
                session_factory=factory,
            )
        else:
            # Replay fixture source (default / backward-compatible).
            if not body.fixture_path:
                raise HTTPException(
                    status_code=400,
                    detail="fixture_path required for source=replay",
                )
            try:
                source = build_replay_source(body.fixture_path)
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=400, detail=str(exc),
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=str(exc),
                )

        sv = get_supervisor()

        # Route explicitly on the request's ``mode`` field. v2
        # earlier sniffed live_caps.live_orders_enabled to decide,
        # but that conflated UX intent with config state. Now the
        # frontend sends mode=paper or mode=live based on which
        # view the user is in (Paper / Dry run / Live segments).
        from backend.algo.live.caps_repo import CapsRepo
        from backend.algo.live.runtime import LiveNotEnabledError

        if body.mode == "live":
            caps_repo = CapsRepo()
            caps = await caps_repo.get(user_id, body.strategy_id)
            live_enabled = bool(
                caps and caps.get("live_orders_enabled"),
            )
            if not live_enabled:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Live trading not enabled for this "
                        "strategy. Configure caps + flip the "
                        "Live mode toggle first."
                    ),
                )
            from datetime import date as _date
            from backend.algo.broker.credentials_repo import (
                BrokerCredentialsRepo,
            )
            from backend.algo.broker.kite_client import KiteClient
            from backend.algo.backtest.runs_repo import (
                BacktestRunsRepo,
            )
            from backend.algo.paper.kill_switch_repo import (
                KillSwitchRepo,
            )

            # Load Kite credentials.
            creds_repo = BrokerCredentialsRepo()
            async with factory() as session:
                creds = await creds_repo.load(session, user_id)
            if not creds or not creds.get("access_token") \
                    or creds.get("access_token_expired"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Kite not connected or token expired. "
                        "Reconnect Zerodha first."
                    ),
                )

            # KiteClient picks up ALGO_LIVE_DRY_RUN automatically
            # when no explicit dry_run is passed.
            kite = KiteClient(
                api_key=creds["api_key"],
                access_token=creds["access_token"],
            )

            # Create algo.runs row up-front so LiveRuntime has
            # a run_id for in-flight tracking. Live mode has no
            # meaningful period boundaries — use today as a
            # placeholder; meaningful values are populated as
            # orders fire.
            runs_repo = BacktestRunsRepo()
            today = _date.today()
            async with factory() as session:
                run = await runs_repo.create_pending(
                    session,
                    user_id=user_id,
                    strategy_id=body.strategy_id,
                    period_start=today,
                    period_end=today,
                    mode="live",
                )
                await runs_repo.mark_running(
                    session, run_id=run.run_id,
                )
                await session.commit()
            run_id = run.run_id

            ks_for_runtime = KillSwitchRepo(
                redis_client=get_async_redis(),
            )
            _logger.info(
                "start_run: dispatching LiveRuntime user=%s "
                "strat=%s source=%s",
                user_id, body.strategy_id, body.source,
            )
            try:
                row = await sv.start_live_run(
                    user_id=user_id,
                    strategy=strategy,
                    source=source,
                    initial_capital_inr=body.initial_capital_inr,
                    kite=kite,
                    caps=caps,
                    run_id=run_id,
                    caps_repo=caps_repo,
                    kill_switch_repo=ks_for_runtime,
                )
            except LiveNotEnabledError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=409, detail=str(exc),
                )
            return row

        try:
            row = await sv.start_run(
                user_id=user_id,
                strategy=strategy,
                source=source,
                initial_capital_inr=body.initial_capital_inr,
                kill_switch_active=kill_active,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return row

    @router.delete("/runs/{strategy_id}")
    async def stop_run(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ):
        from backend.algo.paper.supervisor import get_supervisor

        sv = get_supervisor()
        stopped = await sv.stop_run(
            user_id=UUID(user.user_id), strategy_id=strategy_id,
        )
        if not stopped:
            raise HTTPException(
                status_code=404, detail="No active run found",
            )
        return {"stopped": True}

    @router.get("/runs")
    async def list_runs(
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        from backend.algo.paper.supervisor import get_supervisor

        sv = get_supervisor()
        return sv.list_active(user_id=UUID(user.user_id))

    @router.get("/fixtures")
    async def list_fixtures(
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Replay fixtures available to the start-run form.

        Reads the same directory build_replay_source validates
        against, so the dropdown can never offer a path that
        the start-run endpoint would later reject.
        """
        from backend.algo.paper.supervisor import (
            list_replay_fixtures,
        )
        return list_replay_fixtures()

    return router
