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

from fastapi import APIRouter, Depends, HTTPException, Query
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
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        type: str | None = Query(
            None,
            description=(
                "Filter by event type "
                "(e.g. position_drift_detected)"
            ),
        ),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Recent algo events for the caller (newest first).

        Returns both ``mode='paper'`` and ``mode='live'``
        events so the reconciliation drift panel can poll
        for ``type=position_drift_detected`` regardless of
        which mode emitted them.
        """
        from backend.db.duckdb_engine import query_iceberg_table
        if type is not None:
            sql = (
                "SELECT event_id, ts_ns, ts_date, "
                "       strategy_id, type, payload_json "
                "FROM events "
                "WHERE user_id = ? AND type = ? "
                "ORDER BY ts_ns DESC "
                "LIMIT ? OFFSET ?"
            )
            params: list = [
                str(UUID(user.user_id)), type, limit, offset,
            ]
        else:
            sql = (
                "SELECT event_id, ts_ns, ts_date, "
                "       strategy_id, type, payload_json "
                "FROM events "
                "WHERE user_id = ? "
                "ORDER BY ts_ns DESC "
                "LIMIT ? OFFSET ?"
            )
            params = [str(UUID(user.user_id)), limit, offset]
        try:
            rows = query_iceberg_table(
                "algo.events", sql, params,
            )
        except FileNotFoundError:
            # No events yet — algo.events table empty.
            return []

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
