"""Live-trading routes — V2-5.

Endpoints
---------
GET  /v1/algo/live/caps/{strategy_id}        — get caps for a strategy
PUT  /v1/algo/live/caps/{strategy_id}        — upsert caps
POST /v1/algo/live/enable/{strategy_id}      — enable live orders
                                               (4-gate validated)
POST /v1/algo/live/disable/{strategy_id}     — disable live orders
GET  /v1/algo/live/status/{strategy_id}      — all gates status
GET  /v1/algo/live/orders/{strategy_id}      — in-flight orders list

Notes
-----
- ``enable`` requires ALL 4 gates to pass server-side; the frontend
  toggle is a convenience — we never trust UI-side gate state.
- ``disable`` is always allowed (no gate restriction).
- Gate validation is stateless: reads PG every time (no cache).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)

UTC = timezone.utc
_30_DAYS = timedelta(days=30)


# ---------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------


class UpsertCapsRequest(BaseModel):
    max_inr: Decimal = Field(ge=Decimal("0"))
    max_orders_per_day: int = Field(ge=0, le=50)
    allowed_tickers: list[str] = Field(default_factory=list)
    last_walkforward_run_id: UUID | None = None


class CapsResponse(BaseModel):
    user_id: UUID
    strategy_id: UUID
    max_inr: Decimal
    max_orders_per_day: int
    allowed_tickers: list[str]
    live_orders_enabled: bool
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    last_walkforward_run_id: UUID | None = None
    cumulative_inr_today: Decimal
    orders_count_today: int


class GatesStatus(BaseModel):
    """Frontend uses this to show per-gate tooltips on the toggle."""

    kite_connected: bool
    caps_set: bool
    kill_switch_disarmed: bool
    walkforward_recent: bool
    drift_within_limit: bool
    all_pass: bool
    live_orders_enabled: bool
    dry_run: bool = False


class EnableRequest(BaseModel):
    """Must include strategy_name for the retype-confirm check."""

    confirmed_strategy_name: str


class WsHealth(BaseModel):
    """OBS-1 — KiteWsMultiplexer health view for the dashboard dot.

    Read-only snapshot served by GET /v1/algo/live/ws-health. All
    fields default to their disconnected values when no
    multiplexer is registered for the user.
    """

    connected: bool = False
    subscriber_count: int = 0
    subscribed_tokens: int = 0
    last_tick_at: str | None = None
    tick_age_seconds: int | None = None
    tick_count_today: int = 0


# ---------------------------------------------------------------
# Helper: 4-gate validation
# ---------------------------------------------------------------


async def _check_gates(
    user_id: UUID,
    strategy_id: UUID,
    session_factory: Any,
    redis_client: Any,
) -> GatesStatus:
    """Evaluate all 4 live-mode gates from PG/Redis."""
    from backend.algo.broker.credentials_repo import (
        BrokerCredentialsRepo,
    )
    from backend.algo.live.caps_repo import CapsRepo
    from backend.algo.live.drift_repo import DriftRepo
    from backend.algo.paper.kill_switch_repo import KillSwitchRepo

    # Gate 1: Kite connected (access_token present + not expired).
    # ``BrokerCredentialsRepo.load`` requires an AsyncSession and
    # returns a dict with ``access_token`` (already-decrypted) +
    # ``access_token_expired`` boolean; mirror v1's call shape from
    # ``backend/algo/routes/paper.py::_build_live_ws_source``.
    creds_repo = BrokerCredentialsRepo()
    async with session_factory() as session:
        creds = await creds_repo.load(session, user_id)
    kite_connected = bool(
        creds
        and creds.get("access_token")
        and not creds.get("access_token_expired"),
    )

    # Gate 2: caps row exists with max_inr > 0
    caps_repo = CapsRepo()
    caps = await caps_repo.get(user_id, strategy_id)
    caps_set = bool(
        caps
        and Decimal(str(caps.get("max_inr", 0))) > 0
        and caps.get("allowed_tickers")
    )

    # Gate 3: kill switch DISARMED
    ks_repo = KillSwitchRepo(redis_client=redis_client)
    kill_active = await ks_repo.is_active(user_id)
    kill_switch_disarmed = not kill_active

    # Gate 4: most recent walkforward run for THIS strategy
    # is < 30 days old AND has a positive aggregate PnL.
    #
    # We auto-discover the run instead of requiring caps to
    # carry ``last_walkforward_run_id`` — saves the user from a
    # separate "link walkforward" UX step. We always pick the
    # latest by ``started_at`` for the (user, strategy) pair.
    #
    # V2-2 stores aggregate fields as ``avg_pnl_pct`` /
    # ``avg_win_rate_pct`` on ``algo.runs.summary_json``.
    # ``avg_pnl_pct > 0`` is the meaningful gate; raw win-rate
    # alone is misleading (e.g. 60% win-rate can still bleed
    # under 1:2 R:R).
    walkforward_recent = False
    async with session_factory() as session:
        from sqlalchemy import text

        row = (
            (
                await session.execute(
                    text(
                        "SELECT started_at, summary_json "
                        "FROM algo.runs "
                        "WHERE user_id = :uid "
                        "  AND strategy_id = :sid "
                        "  AND parent_walkforward_id IS NULL "
                        "  AND window_start IS NULL "
                        "  AND status = 'completed' "
                        "  AND summary_json IS NOT NULL "
                        "  AND ((summary_json->'aggregate'->>"
                        "        'window_count') IS NOT NULL) "
                        "ORDER BY started_at DESC "
                        "LIMIT 1"
                    ),
                    {"uid": user_id, "sid": strategy_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row:
        started_at = row["started_at"]
        if started_at:
            age = datetime.now(UTC) - started_at.replace(
                tzinfo=UTC,
            )
            if age < _30_DAYS:
                import json as _json

                summary = row.get("summary_json") or {}
                if isinstance(summary, str):
                    summary = _json.loads(summary)
                # V2-2 stores fields nested under ``aggregate``,
                # not at the top level. ``avg_pnl_pct > 0`` is
                # the meaningful gate (raw win-rate alone can
                # mislead under bad R:R).
                aggregate = summary.get("aggregate") or {}
                avg_pnl = aggregate.get("avg_pnl_pct", 0)
                walkforward_recent = float(avg_pnl) > 0

    # Drift gate (bonus gate — spec §2.2 mentions drift > 3 runs)
    drift_within_limit = True
    drift_repo = DriftRepo()
    open_drifts = await drift_repo.get_open_drifts(user_id)
    for drift_row in open_drifts:
        if int(drift_row.get("consecutive_runs", 0)) > 3:
            drift_within_limit = False
            break

    all_pass = (
        kite_connected
        and caps_set
        and kill_switch_disarmed
        and walkforward_recent
        and drift_within_limit
    )

    # Dry-run flag: per-user Redis state (set via the
    # /v1/algo/live/dry-run/{arm,disarm} endpoints when the
    # frontend toggle flips). Falls back to the
    # ALGO_LIVE_DRY_RUN env var if Redis state is absent so
    # legacy deployments keep working.
    from backend.algo.live.dry_run_flag import is_armed

    dry_run = await is_armed(user_id, redis_client)

    return GatesStatus(
        kite_connected=kite_connected,
        caps_set=caps_set,
        kill_switch_disarmed=kill_switch_disarmed,
        walkforward_recent=walkforward_recent,
        drift_within_limit=drift_within_limit,
        all_pass=all_pass,
        live_orders_enabled=bool(
            caps and caps.get("live_orders_enabled"),
        ),
        dry_run=dry_run,
    )


# ---------------------------------------------------------------
# Postback read models (OBS-2 companion)
# ---------------------------------------------------------------


class PostbackEvent(BaseModel):
    """Single postback event row for the frontend panel.

    ``event_ts`` (ISO 8601 UTC, with Z suffix) is what the frontend
    KitePostback type consumes; ``ts_ns`` and ``ts_date`` are kept
    for backend forensics.
    """

    event_id: str
    event_ts: str
    ts_ns: int
    ts_date: str
    guid: str
    order_id: str
    status: str
    tradingsymbol: str
    filled_quantity: int
    average_price: float
    our_user_id: str | None = None
    raw: dict = Field(default_factory=dict)


def _query_postback_events(
    user_id: str,
    limit: int,
) -> list[dict]:
    """Query algo.events for kite_postback_received rows.

    Args:
        user_id: Our internal user UUID string.
        limit: Max rows to return (default 50).

    Returns:
        List of raw event dicts ordered by ts_ns DESC.
    """
    # Use the canonical query_iceberg_table helper which auto-creates
    # a DuckDB view from the Iceberg metadata. The previous direct
    # call to ``StockRepository._iceberg_table_path`` referenced a
    # method that doesn't exist on the repo class — the AttributeError
    # turned every browser poll of /postbacks into a 500 that the
    # frontend rendered as "NetworkError".
    from backend.db.duckdb_engine import query_iceberg_table

    try:
        rows = query_iceberg_table(
            "algo.events",
            "SELECT event_id, ts_ns, ts_date, payload_json "
            "FROM events "
            "WHERE user_id = ? "
            "  AND type = 'kite_postback_received' "
            "ORDER BY ts_ns DESC "
            "LIMIT ?",
            [user_id, limit],
        )
        return rows
    except Exception:
        _logger.warning(
            "postback query failed for user=%s",
            user_id,
            exc_info=True,
        )
        return []


# ---------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------


def create_live_router() -> APIRouter:
    router = APIRouter(prefix="/algo/live", tags=["algo-live"])

    def _sf():
        from backend.db.engine import get_session_factory

        return get_session_factory()

    def _redis():
        from backend.algo.redis_async import get_async_redis

        return get_async_redis()

    # ----------------------------------------------------------
    @router.get(
        "/caps/{strategy_id}",
        response_model=CapsResponse,
    )
    async def get_caps(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> CapsResponse:
        from backend.algo.live.caps_repo import CapsRepo

        repo = CapsRepo()
        row = await repo.get_or_default(
            UUID(user.user_id),
            strategy_id,
        )
        return CapsResponse(
            **{k: row[k] for k in CapsResponse.model_fields if k in row}
        )

    # ----------------------------------------------------------
    @router.put(
        "/caps/{strategy_id}",
        response_model=CapsResponse,
    )
    async def upsert_caps(
        strategy_id: UUID,
        body: UpsertCapsRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> CapsResponse:
        from backend.algo.live.caps_repo import CapsRepo

        repo = CapsRepo()
        row = await repo.upsert(
            UUID(user.user_id),
            strategy_id,
            max_inr=body.max_inr,
            max_orders_per_day=body.max_orders_per_day,
            allowed_tickers=body.allowed_tickers,
            last_walkforward_run_id=body.last_walkforward_run_id,
        )
        return CapsResponse(
            **{k: row[k] for k in CapsResponse.model_fields if k in row}
        )

    # ----------------------------------------------------------
    @router.get(
        "/status/{strategy_id}",
        response_model=GatesStatus,
    )
    async def get_status(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> GatesStatus:
        return await _check_gates(
            UUID(user.user_id),
            strategy_id,
            _sf(),
            _redis(),
        )

    # ----------------------------------------------------------
    @router.post(
        "/enable/{strategy_id}",
        response_model=CapsResponse,
    )
    async def enable_live(
        strategy_id: UUID,
        body: EnableRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> CapsResponse:
        """Enable live orders after verifying all 4 gates pass.

        The frontend also sends the confirmed strategy name so the
        server can double-check the retype-confirm was for the right
        strategy.
        """
        from backend.algo.live.caps_repo import CapsRepo
        from backend.algo.strategy.repo import get_strategy

        uid = UUID(user.user_id)

        # Verify the strategy exists + name matches
        factory = _sf()
        async with factory() as session:
            strategy = await get_strategy(session, uid, strategy_id)
        if strategy is None:
            raise HTTPException(
                status_code=404,
                detail="Strategy not found.",
            )
        if strategy.name != body.confirmed_strategy_name:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Strategy name mismatch. "
                    f"Expected {strategy.name!r}, "
                    f"got {body.confirmed_strategy_name!r}."
                ),
            )

        # Server-side 4-gate check
        gates = await _check_gates(
            uid,
            strategy_id,
            _sf(),
            _redis(),
        )
        if not gates.all_pass:
            closed = [
                f
                for f, v in {
                    "kite_connected": gates.kite_connected,
                    "caps_set": gates.caps_set,
                    "kill_switch_disarmed": gates.kill_switch_disarmed,
                    "walkforward_recent": gates.walkforward_recent,
                    "drift_within_limit": gates.drift_within_limit,
                }.items()
                if not v
            ]
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Live mode gates not met: {closed}. "
                    f"Resolve these before enabling live trading."
                ),
            )

        repo = CapsRepo()
        await repo.enable_live_orders(
            uid,
            strategy_id,
            approved_by=uid,
        )
        row = await repo.get_or_default(uid, strategy_id)
        return CapsResponse(
            **{k: row[k] for k in CapsResponse.model_fields if k in row}
        )

    # ----------------------------------------------------------
    @router.post(
        "/disable/{strategy_id}",
        response_model=CapsResponse,
    )
    async def disable_live(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> CapsResponse:
        from backend.algo.live.caps_repo import CapsRepo

        uid = UUID(user.user_id)
        repo = CapsRepo()
        await repo.disable_live_orders(uid, strategy_id)
        row = await repo.get_or_default(uid, strategy_id)
        return CapsResponse(
            **{k: row[k] for k in CapsResponse.model_fields if k in row}
        )

    # ----------------------------------------------------------
    # Dry-run mode toggle (per-user, Redis-backed).
    # ----------------------------------------------------------
    @router.post("/dry-run/arm")
    async def dry_run_arm(
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict[str, Any]:
        from backend.algo.live.dry_run_flag import arm

        uid = UUID(user.user_id)
        new_state = await arm(uid, _redis())
        return {"dry_run": new_state}

    @router.post("/dry-run/disarm")
    async def dry_run_disarm(
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict[str, Any]:
        from backend.algo.live.dry_run_flag import disarm

        uid = UUID(user.user_id)
        new_state = await disarm(uid, _redis())
        return {"dry_run": new_state}

    @router.get("/dry-run")
    async def dry_run_state(
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict[str, Any]:
        from backend.algo.live.dry_run_flag import is_armed

        uid = UUID(user.user_id)
        state = await is_armed(uid, _redis())
        return {"dry_run": state}

    # ----------------------------------------------------------
    # OBS-1 — Kite WS health snapshot for the dashboard dot.
    # Always 200; returns disconnected zeros when no multiplexer
    # is registered for the user. MUST NOT spin up a multiplexer
    # as a side-effect of polling.
    # ----------------------------------------------------------
    @router.get("/ws-health", response_model=WsHealth)
    async def get_ws_health(
        user: UserContext = Depends(pro_or_superuser),
    ) -> WsHealth:
        from backend.algo.broker.ws_registry import (
            get_multiplexer_if_exists,
        )
        from backend.routes import _iso_utc

        uid = UUID(user.user_id)
        mux = get_multiplexer_if_exists(uid)
        if mux is None:
            return WsHealth()

        snap = mux.health_snapshot()
        last = snap.get("last_tick_at")
        age: int | None = None
        if last is not None:
            now = datetime.now(UTC).replace(tzinfo=None)
            try:
                age = int((now - last).total_seconds())
            except (TypeError, ValueError):
                age = None
        return WsHealth(
            connected=bool(snap.get("connected")),
            subscriber_count=int(snap.get("subscriber_count", 0)),
            subscribed_tokens=int(snap.get("subscribed_tokens", 0)),
            last_tick_at=_iso_utc(last),
            tick_age_seconds=age,
            tick_count_today=int(snap.get("tick_count_today", 0)),
        )

    # ----------------------------------------------------------
    @router.get("/orders/{strategy_id}")
    async def get_in_flight_orders(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict]:
        """Return in-flight orders for the most recent live run."""
        from sqlalchemy import text

        from backend.algo.live.caps_repo import CapsRepo  # noqa: F401

        uid = UUID(user.user_id)
        # Find latest live run for this strategy
        factory = _sf()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id, live_orders_in_flight "
                            "FROM algo.runs "
                            "WHERE user_id = :uid "
                            "  AND strategy_id = :sid "
                            "ORDER BY started_at DESC LIMIT 1"
                        ),
                        {"uid": uid, "sid": strategy_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return []
        in_flight = row.get("live_orders_in_flight") or []
        if isinstance(in_flight, str):
            import json

            in_flight = json.loads(in_flight)
        return in_flight

    # ---------------------------------------------------------------
    # Postback read endpoint (OBS-2 companion)
    # ---------------------------------------------------------------

    @router.get(
        "/postbacks",
        response_model=list[PostbackEvent],
    )
    async def get_live_postbacks(
        limit: int = 50,
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[PostbackEvent]:
        """Return last N Kite postback events for the user.

        Args:
            limit: Max rows (capped at 200, default 50).

        Returns:
            Bare list, newest first. Frontend KitePostback type
            consumes ``event_ts`` as ISO 8601 UTC.
        """
        import asyncio as _asyncio
        from datetime import datetime, timezone

        cap = min(limit, 200)
        raw_rows = await _asyncio.to_thread(
            _query_postback_events, user.user_id, cap
        )

        events: list[PostbackEvent] = []
        for r in raw_rows:
            try:
                p = json.loads(r["payload_json"])
            except Exception:
                continue
            ts_ns = int(r["ts_ns"])
            event_ts = datetime.fromtimestamp(
                ts_ns / 1_000_000_000, tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
            events.append(
                PostbackEvent(
                    event_id=r["event_id"],
                    event_ts=event_ts,
                    ts_ns=ts_ns,
                    ts_date=str(r["ts_date"]),
                    guid=p.get("guid", ""),
                    order_id=p.get("order_id", ""),
                    status=p.get("status", ""),
                    tradingsymbol=p.get("tradingsymbol", ""),
                    filled_quantity=int(p.get("filled_quantity", 0)),
                    average_price=float(p.get("average_price", 0.0)),
                    our_user_id=p.get("our_user_id"),
                    raw=p.get("raw", {}),
                )
            )

        return events

    return router
