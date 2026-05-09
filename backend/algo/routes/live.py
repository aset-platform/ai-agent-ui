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

    # Gate 4: last walkforward run < 30 days old + positive
    walkforward_recent = False
    if caps and caps.get("last_walkforward_run_id"):
        wf_run_id = caps["last_walkforward_run_id"]
        async with session_factory() as session:
            from sqlalchemy import text
            row = (
                await session.execute(
                    text(
                        "SELECT started_at, summary_json "
                        "FROM algo.runs "
                        "WHERE id = :rid "
                        "  AND user_id = :uid"
                    ),
                    {"rid": wf_run_id, "uid": user_id},
                )
            ).mappings().one_or_none()
        if row:
            started_at = row["started_at"]
            if started_at:
                age = datetime.now(UTC) - started_at.replace(
                    tzinfo=UTC,
                )
                if age < _30_DAYS:
                    # Check positive win-rate in summary_json
                    import json as _json
                    summary = row.get("summary_json") or {}
                    if isinstance(summary, str):
                        summary = _json.loads(summary)
                    win_rate = summary.get("aggregate_win_rate", 0)
                    walkforward_recent = float(win_rate) > 0

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

    # Dry-run flag — reads ALGO_LIVE_DRY_RUN from env.
    # Import here to avoid circular import with broker module.
    from backend.algo.broker.kite_client import _read_dry_run_env
    dry_run = _read_dry_run_env()

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
            UUID(user.user_id), strategy_id,
        )
        return CapsResponse(**{
            k: row[k] for k in CapsResponse.model_fields
            if k in row
        })

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
        return CapsResponse(**{
            k: row[k] for k in CapsResponse.model_fields
            if k in row
        })

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
            uid, strategy_id, _sf(), _redis(),
        )
        if not gates.all_pass:
            closed = [
                f for f, v in {
                    "kite_connected": gates.kite_connected,
                    "caps_set": gates.caps_set,
                    "kill_switch_disarmed": gates.kill_switch_disarmed,
                    "walkforward_recent": gates.walkforward_recent,
                    "drift_within_limit": gates.drift_within_limit,
                }.items() if not v
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
            uid, strategy_id, approved_by=uid,
        )
        row = await repo.get_or_default(uid, strategy_id)
        return CapsResponse(**{
            k: row[k] for k in CapsResponse.model_fields
            if k in row
        })

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
        return CapsResponse(**{
            k: row[k] for k in CapsResponse.model_fields
            if k in row
        })

    # ----------------------------------------------------------
    @router.get("/orders/{strategy_id}")
    async def get_in_flight_orders(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict]:
        """Return in-flight orders for the most recent live run."""
        from backend.algo.live.caps_repo import CapsRepo
        from sqlalchemy import text

        uid = UUID(user.user_id)
        # Find latest live run for this strategy
        factory = _sf()
        async with factory() as session:
            row = (
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
            ).mappings().one_or_none()
        if row is None:
            return []
        in_flight = row.get("live_orders_in_flight") or []
        if isinstance(in_flight, str):
            import json
            in_flight = json.loads(in_flight)
        return in_flight

    return router
