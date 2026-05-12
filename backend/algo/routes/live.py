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

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.broker.credentials_repo import (
    BrokerCredentialsRepo,
)
from backend.algo.broker.kite_client import KiteClient
from backend.cache import get_cache
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
_30_DAYS = timedelta(days=30)
_DASHBOARD_CACHE_TTL = 15  # seconds — per CLAUDE.md § 5.13
_POSITIONS_CACHE_TTL = 10  # seconds — SWR polls every 10s
_HOLDINGS_CACHE_TTL = 60  # seconds — SWR polls every 30s


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
                summary = row.get("summary_json") or {}
                if isinstance(summary, str):
                    summary = json.loads(summary)
                # V2-2 stores fields nested under ``aggregate``,
                # not at the top level. ``avg_pnl_pct > 0`` is
                # the meaningful gate (raw win-rate alone can
                # mislead under bad R:R).
                aggregate = summary.get("aggregate") or {}
                avg_pnl = aggregate.get("avg_pnl_pct", 0)
                walkforward_recent = float(avg_pnl) > 0

                # REGIME-5: when env flag is set, additionally
                # require all 5 quality gates to pass. Default OFF
                # — staged rollout per spec §7.1: existing live
                # runs grandfathered; flip on at day 21 to enforce
                # on new toggles.
                if (
                    walkforward_recent
                    and os.environ.get(
                        "ALGO_REGIME_5_GATES_REQUIRED",
                    ) == "1"
                ):
                    gates = aggregate.get("gates_passed") or {}
                    if not gates or not all(gates.values()):
                        walkforward_recent = False

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
# Live dashboard / positions / holdings models
# ---------------------------------------------------------------


class DashboardSummary(BaseModel):
    """Aggregated KPIs for the Live Trading header strip."""

    today_pnl_inr: Decimal = Field(default=Decimal("0"))
    open_pnl_inr: Decimal = Field(default=Decimal("0"))
    realised_pnl_inr: Decimal = Field(default=Decimal("0"))
    cash_inr: Decimal = Field(default=Decimal("0"))
    open_position_count: int = 0
    mode: str  # "live" | "dry_run"
    ws_age_seconds: int | None = None
    kill_switch_active: bool = False


class PositionRow(BaseModel):
    tradingsymbol: str
    exchange: str
    quantity: int
    average_price: Decimal
    last_price: Decimal
    pnl_inr: Decimal
    pnl_pct: Decimal
    product: str
    strategy_id: str | None = None
    strategy_name: str | None = None
    entry_ts_utc: datetime | None = None
    entry_reason: str | None = None


class PositionsResponse(BaseModel):
    rows: list[PositionRow]
    ledger_drift: bool = False


class HoldingRow(BaseModel):
    tradingsymbol: str
    exchange: str
    quantity: int
    average_price: Decimal
    last_price: Decimal
    pnl_inr: Decimal
    pnl_pct: Decimal
    days_held: int | None = None
    strategy_id: str | None = None
    strategy_name: str | None = None


class HoldingsResponse(BaseModel):
    rows: list[HoldingRow]
    ledger_drift: bool = False


# ---------------------------------------------------------------
# Live-dashboard helpers (shared by /dashboard-summary,
# /positions, /holdings).
# ---------------------------------------------------------------


def _get_session_factory():
    from backend.db.engine import get_session_factory

    return get_session_factory()


async def _build_kite_client_for_user(user_id: UUID) -> KiteClient:
    """Load broker creds + construct a real-mode KiteClient.

    Raises HTTPException(503) when credentials are missing or the
    Kite access token has expired. Dashboard reads are always live
    truth — never synthetic — so we pin ``dry_run=False`` regardless
    of the user's dry-run flag.
    """
    creds_repo = BrokerCredentialsRepo()
    factory = _get_session_factory()
    async with factory() as session:
        creds = await creds_repo.load(session, user_id)
    if not creds:
        raise HTTPException(
            status_code=503,
            detail="Kite not connected",
        )
    if creds.get("access_token_expired"):
        raise HTTPException(
            status_code=503,
            detail="Kite token expired",
        )
    api_key = creds.get("api_key")
    access_token = creds.get("access_token")
    if not api_key or not access_token:
        raise HTTPException(
            status_code=503,
            detail="Kite credentials incomplete",
        )
    return KiteClient(
        api_key=api_key,
        access_token=access_token,
        dry_run=False,
    )


def _ist_midnight_str() -> str:
    """Return today's date in IST as YYYY-MM-DD."""
    return datetime.now(IST).strftime("%Y-%m-%d")


async def _realised_pnl_today(user_id: UUID) -> Decimal:
    """Sum realised P&L from algo.events for today (IST, live mode).

    NOTE: the current event vocabulary does NOT emit a dedicated
    ``pnl_realised`` event (verified via grep across backend/algo).
    Realised P&L is therefore returned as Decimal("0") until that
    event is introduced upstream. Frontend already renders 0 as
    "—" via the empty-state helper, so no UX regression.

    The Iceberg read pattern below is wired but disabled — leaving
    it in code as the documented integration point for when the
    runtime starts emitting position_closed / pnl_realised rows.
    """
    return Decimal("0")


async def _dry_run_armed(user_id: UUID, redis_client: Any) -> bool:
    """Resolve the per-user dry-run flag from Redis (or env fallback)."""
    from backend.algo.live.dry_run_flag import is_armed

    return await is_armed(user_id, redis_client)


async def _ws_age_seconds(user_id: UUID) -> int | None:
    """Seconds since the latest Kite WS tick (None if disconnected)."""
    from backend.algo.broker.ws_registry import (
        get_multiplexer_if_exists,
    )

    mux = get_multiplexer_if_exists(user_id)
    if mux is None:
        return None
    snap = mux.health_snapshot()
    last = snap.get("last_tick_at")
    if last is None:
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        return max(0, int((now - last).total_seconds()))
    except (TypeError, ValueError):
        return None


async def _kill_switch_active(
    user_id: UUID,
    redis_client: Any,
) -> bool:
    """Read the kill-switch active flag (PG-backed via repo)."""
    from backend.algo.paper.kill_switch_repo import KillSwitchRepo

    repo = KillSwitchRepo(redis_client=redis_client)
    try:
        return await repo.is_active(user_id)
    except Exception:  # noqa: BLE001
        _logger.warning(
            "kill-switch read failed",
            exc_info=True,
        )
        return False


async def _strategy_name_lookup(
    strategy_ids: set[str],
) -> dict[str, str]:
    """Return ``{strategy_id_str: name}`` for the given UUIDs.

    Empty dict on read failure — caller treats missing names as
    "unknown" via the frontend's null-handling helper.
    """
    if not strategy_ids:
        return {}
    factory = _get_session_factory()
    try:
        async with factory() as session:
            from sqlalchemy import bindparam, text

            stmt = text(
                "SELECT id::text AS id, name FROM algo.strategies "
                "WHERE id::text IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            rows = (
                await session.execute(
                    stmt,
                    {"ids": list(strategy_ids)},
                )
            ).mappings().all()
        return {str(r["id"]): r["name"] for r in rows}
    except Exception:  # noqa: BLE001
        _logger.warning(
            "strategy-name lookup failed",
            exc_info=True,
        )
        return {}


async def _fetch_strategy_attribution(
    user_id: UUID,
    symbols: list[str],
) -> dict[str, dict[str, Any]]:
    """For each symbol, find today's first live BUY fill in
    ``algo.events`` and return the originating strategy + the
    entry reason carried in the event payload.

    ``algo.events`` has no top-level ``tradingsymbol`` / ``side`` /
    ``product`` columns — those live inside ``payload_json``. We
    therefore fetch all of today's live fills for the user, then
    filter + extract in Python. Returns an empty dict on Iceberg
    read failure (drift logic still works via the cheap ledger
    check downstream).

    Join is symbol-only (not ``(symbol, product)``): the runtime
    currently emits ``payload.product = None`` on every fill, so
    a (sym, product) key would never match a Kite CNC position.
    """
    if not symbols:
        return {}
    wanted_symbols = set(symbols)
    today_ist = _ist_midnight_str()
    try:
        rows = await asyncio.to_thread(
            query_iceberg_table,
            "algo.events",
            "SELECT event_id, ts_ns, ts_date, strategy_id, "
            "       payload_json "
            "FROM events "
            "WHERE user_id = ? "
            "  AND mode = 'live' "
            "  AND type = 'order_filled_live' "
            "  AND ts_date >= ? "
            "ORDER BY ts_ns ASC",
            [str(user_id), today_ist],
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "attribution read failed",
            exc_info=True,
        )
        return {}

    # First BUY per symbol wins. We intentionally drop the
    # ``product`` dimension from the join key: the live-runtime
    # currently emits ``payload.product = None`` (see
    # backend/algo/live/runtime.py order_filled_live payload), so a
    # ``(symbol, product)`` key defaulted to ``MIS`` here would
    # never match a Kite ``CNC`` position. Until the runtime
    # starts stamping ``product`` on every fill, the symbol-only
    # join is the only attribution that actually works. Caveat:
    # a user with simultaneous MIS + CNC positions on the same
    # symbol shares one strategy/entry attribution row.
    by_key: dict[str, dict[str, Any]] = {}
    strategy_ids: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if payload.get("dry_run"):
            continue
        side = payload.get("side") or ""
        if side.upper() != "BUY":
            continue
        sym = payload.get("symbol") or ""
        if not sym or sym not in wanted_symbols or sym in by_key:
            continue
        sid = row.get("strategy_id")
        if sid:
            strategy_ids.add(str(sid))
        ts_ns = int(row.get("ts_ns") or 0)
        entry_dt = (
            datetime.fromtimestamp(
                ts_ns / 1_000_000_000, tz=UTC,
            )
            if ts_ns
            else None
        )
        by_key[sym] = {
            "strategy_id": str(sid) if sid else None,
            "strategy_name": None,
            "entry_ts_utc": (
                entry_dt.isoformat() if entry_dt else None
            ),
            "entry_reason": payload.get("reason"),
        }

    # Resolve strategy names from PG in one round-trip.
    names = await _strategy_name_lookup(strategy_ids)
    for ctx in by_key.values():
        sid = ctx.get("strategy_id")
        if sid:
            ctx["strategy_name"] = names.get(sid)
    return by_key


async def _fetch_holding_attribution(
    user_id: UUID,
    symbols: list[str],
) -> dict[str, dict[str, Any]]:
    """For each holding symbol, find the earliest live BUY fill
    within the past 365 days and return strategy + days_held.
    Positions opened earlier surface with days_held=None.
    Empty dict on read failure.
    """
    if not symbols:
        return {}
    wanted = set(symbols)
    one_year_ago = (
        datetime.now(IST).date() - timedelta(days=365)
    ).isoformat()
    try:
        rows = await asyncio.to_thread(
            query_iceberg_table,
            "algo.events",
            "SELECT event_id, ts_ns, strategy_id, payload_json "
            "FROM events "
            "WHERE user_id = ? "
            "  AND mode = 'live' "
            "  AND type = 'order_filled_live' "
            "  AND ts_date >= ? "
            "ORDER BY ts_ns ASC",
            [str(user_id), one_year_ago],
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "holding attribution read failed",
            exc_info=True,
        )
        return {}

    out: dict[str, dict[str, Any]] = {}
    strategy_ids: set[str] = set()
    today_ist_date = datetime.now(IST).date()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if payload.get("dry_run"):
            continue
        side = payload.get("side") or ""
        if side.upper() != "BUY":
            continue
        sym = payload.get("symbol") or ""
        if sym not in wanted or sym in out:
            continue
        ts_ns = int(row.get("ts_ns") or 0)
        entry_dt = (
            datetime.fromtimestamp(
                ts_ns / 1_000_000_000, tz=UTC,
            )
            if ts_ns
            else None
        )
        days_held: int | None = None
        if entry_dt is not None:
            entry_ist = entry_dt.astimezone(IST).date()
            days_held = max(0, (today_ist_date - entry_ist).days)
        sid = row.get("strategy_id")
        if sid:
            strategy_ids.add(str(sid))
        out[sym] = {
            "strategy_id": str(sid) if sid else None,
            "strategy_name": None,
            "days_held": days_held,
        }
    names = await _strategy_name_lookup(strategy_ids)
    for ctx in out.values():
        sid = ctx.get("strategy_id")
        if sid:
            ctx["strategy_name"] = names.get(sid)
    return out


async def _ledger_kite_drift(
    user_id: UUID,
    kite_symbols: set[str],
) -> bool:
    """Cheap signal: do today's ledger BUYs match Kite's open set?

    True only if the ledger has open BUYs Kite doesn't, or vice
    versa (after best-effort net-down). Quiet (False) on any read
    error — the dedicated reconciliation panel surfaces the real
    drift signal.
    """
    try:
        rows = await asyncio.to_thread(
            query_iceberg_table,
            "algo.events",
            "SELECT payload_json FROM events "
            "WHERE user_id = ? "
            "  AND mode = 'live' "
            "  AND type = 'order_filled_live' "
            "  AND ts_date = ?",
            [str(user_id), _ist_midnight_str()],
        )
    except Exception:  # noqa: BLE001
        return False
    ledger_net: dict[str, int] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if payload.get("dry_run"):
            continue
        sym = payload.get("symbol") or ""
        qty = int(payload.get("qty") or 0)
        side = (payload.get("side") or "").upper()
        if not sym or not qty:
            continue
        sign = 1 if side == "BUY" else -1
        ledger_net[sym] = ledger_net.get(sym, 0) + sign * qty
    ledger_open = {s for s, q in ledger_net.items() if q > 0}
    # Symmetric difference: drift if either side has extras.
    return bool(ledger_open.symmetric_difference(kite_symbols))


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


class OrderSubmissionEvent(BaseModel):
    """Single ``order_submitted_live`` row for the Submissions tab.

    Mirrors PostbackEvent shape but exposes the spec §3.6 payload
    structure (request / context / response). Top-level legacy
    fields (kite_order_id, symbol, side, qty, dry_run) are flattened
    out of payload for the table view; full payload is preserved
    under ``raw`` for the expand-row toggle.
    """

    event_id: str
    event_ts: str
    ts_ns: int
    ts_date: str
    session_id: str
    strategy_id: str | None = None
    internal_order_id: str
    kite_order_id: str
    symbol: str
    side: str
    qty: int
    dry_run: bool
    raw: dict = Field(default_factory=dict)


class OrderSubmissionsResponse(BaseModel):
    """Wrapper response for ``GET /order-submissions``.

    Keyed under ``submissions`` (vs. postbacks' bare-list shape) so
    we can grow the response with pagination + filter metadata
    later without a breaking change.
    """

    submissions: list[OrderSubmissionEvent]


def _query_order_submission_events(
    user_id: str,
    limit: int,
    session_id: str | None = None,
) -> list[dict]:
    """Query algo.events for order_submitted_live rows.

    Mirrors ``_query_postback_events``. ``session_id`` filter is
    optional — when provided, restricts to a single LiveRuntime
    session (one frontend Submissions panel typically wants the
    current session only; without a filter we show all live mode
    submissions for the user).
    """
    try:
        if session_id:
            rows = query_iceberg_table(
                "algo.events",
                "SELECT event_id, ts_ns, ts_date, session_id, "
                "       strategy_id, payload_json "
                "FROM events "
                "WHERE user_id = ? "
                "  AND mode = 'live' "
                "  AND session_id = ? "
                "  AND type = 'order_submitted_live' "
                "ORDER BY ts_ns DESC "
                "LIMIT ?",
                [user_id, session_id, limit],
            )
        else:
            rows = query_iceberg_table(
                "algo.events",
                "SELECT event_id, ts_ns, ts_date, session_id, "
                "       strategy_id, payload_json "
                "FROM events "
                "WHERE user_id = ? "
                "  AND mode = 'live' "
                "  AND type = 'order_submitted_live' "
                "ORDER BY ts_ns DESC "
                "LIMIT ?",
                [user_id, limit],
            )
        return rows
    except Exception:  # noqa: BLE001
        _logger.warning(
            "order_submitted_live query failed for user=%s",
            user_id,
            exc_info=True,
        )
        return []


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
    except Exception:  # noqa: BLE001
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
    # Live dashboard aggregate KPIs — 15s Redis cache.
    # ----------------------------------------------------------
    @router.get(
        "/dashboard-summary",
        response_model=DashboardSummary,
    )
    async def dashboard_summary(
        user: UserContext = Depends(pro_or_superuser),
    ) -> DashboardSummary:
        """Header-strip KPIs: today/open/realised P&L, cash, mode."""
        cache = get_cache()
        cache_key = f"cache:algo:dash:{user.user_id}"
        cached = cache.get(cache_key)
        if cached:
            try:
                return DashboardSummary.model_validate_json(cached)
            except (ValueError, TypeError):
                # Corrupted cache entry — fall through and recompute.
                pass

        uid = UUID(user.user_id)
        kite = await _build_kite_client_for_user(uid)
        kc = kite._kc
        try:
            positions = await asyncio.to_thread(kc.positions)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "kite positions read failed",
                exc_info=True,
            )
            positions = {}
        try:
            margins = await asyncio.to_thread(kc.margins, "equity")
        except Exception:  # noqa: BLE001
            _logger.warning(
                "kite margins read failed",
                exc_info=True,
            )
            margins = {}

        net_rows = (
            positions.get("net", [])
            if isinstance(positions, dict) else []
        )
        day_rows = (
            positions.get("day", [])
            if isinstance(positions, dict) else []
        )
        open_pnl = sum(
            (
                Decimal(str(r.get("pnl", 0)))
                for r in net_rows
                if int(r.get("quantity", 0)) != 0
            ),
            Decimal("0"),
        )
        today_pnl = sum(
            (Decimal(str(r.get("pnl", 0))) for r in day_rows),
            Decimal("0"),
        )
        cash = Decimal(
            str(
                (margins or {})
                .get("available", {})
                .get("live_balance", 0)
            )
        )
        open_count = sum(
            1
            for r in net_rows
            if int(r.get("quantity", 0)) != 0
        )

        realised = await _realised_pnl_today(uid)
        dry_run = await _dry_run_armed(uid, _redis())
        ws_age = await _ws_age_seconds(uid)
        ks_active = await _kill_switch_active(uid, _redis())

        out = DashboardSummary(
            today_pnl_inr=today_pnl,
            open_pnl_inr=open_pnl,
            realised_pnl_inr=realised,
            cash_inr=cash,
            open_position_count=open_count,
            mode="dry_run" if dry_run else "live",
            ws_age_seconds=ws_age,
            kill_switch_active=ks_active,
        )
        try:
            cache.set(
                cache_key,
                out.model_dump_json(),
                ttl=_DASHBOARD_CACHE_TTL,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "dashboard cache set failed",
                exc_info=True,
            )
        return out

    # ----------------------------------------------------------
    # Open intraday positions (joined with strategy attribution).
    # ----------------------------------------------------------
    @router.get("/positions", response_model=PositionsResponse)
    async def get_positions(
        user: UserContext = Depends(pro_or_superuser),
    ) -> PositionsResponse:
        cache = get_cache()
        cache_key = f"cache:algo:live:positions:{user.user_id}"
        cached = cache.get(cache_key)
        if cached:
            try:
                return PositionsResponse.model_validate_json(cached)
            except (ValueError, TypeError):
                # Corrupted cache entry — fall through and recompute.
                pass

        uid = UUID(user.user_id)
        kite = await _build_kite_client_for_user(uid)
        kc = kite._kc
        try:
            raw = await asyncio.to_thread(kc.positions)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "kite positions read failed",
                exc_info=True,
            )
            raw = {}
        net = raw.get("net", []) if isinstance(raw, dict) else []
        open_rows = [
            r for r in net if int(r.get("quantity", 0)) != 0
        ]

        attr = await _fetch_strategy_attribution(
            uid,
            [r["tradingsymbol"] for r in open_rows],
        )

        out_rows: list[PositionRow] = []
        for r in open_rows:
            ctx = attr.get(r["tradingsymbol"], {})
            qty = int(r.get("quantity", 0))
            avg = Decimal(str(r.get("average_price", 0) or 0))
            ltp = Decimal(str(r.get("last_price", 0) or 0))
            pnl_inr = Decimal(str(r.get("pnl", 0) or 0))
            pnl_pct = (
                ((ltp - avg) / avg) * Decimal("100")
                if avg > 0
                else Decimal("0")
            )
            entry_ts_iso = ctx.get("entry_ts_utc")
            entry_ts = None
            if entry_ts_iso:
                try:
                    entry_ts = datetime.fromisoformat(entry_ts_iso)
                except (ValueError, TypeError):
                    entry_ts = None
            out_rows.append(
                PositionRow(
                    tradingsymbol=r["tradingsymbol"],
                    exchange=r.get("exchange", "NSE"),
                    quantity=qty,
                    average_price=avg,
                    last_price=ltp,
                    pnl_inr=pnl_inr,
                    pnl_pct=pnl_pct,
                    product=r.get("product") or "MIS",
                    strategy_id=ctx.get("strategy_id"),
                    strategy_name=ctx.get("strategy_name"),
                    entry_ts_utc=entry_ts,
                    entry_reason=ctx.get("entry_reason"),
                )
            )

        drift = await _ledger_kite_drift(
            uid,
            {r["tradingsymbol"] for r in open_rows},
        )
        out = PositionsResponse(rows=out_rows, ledger_drift=drift)
        try:
            cache.set(
                cache_key,
                out.model_dump_json(),
                ttl=_POSITIONS_CACHE_TTL,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "positions cache set failed",
                exc_info=True,
            )
        return out

    # ----------------------------------------------------------
    # Settled CNC holdings (joined with strategy + days_held).
    # ----------------------------------------------------------
    @router.get("/holdings", response_model=HoldingsResponse)
    async def get_holdings(
        user: UserContext = Depends(pro_or_superuser),
    ) -> HoldingsResponse:
        cache = get_cache()
        cache_key = f"cache:algo:live:holdings:{user.user_id}"
        cached = cache.get(cache_key)
        if cached:
            try:
                return HoldingsResponse.model_validate_json(cached)
            except (ValueError, TypeError):
                # Corrupted cache entry — fall through and recompute.
                pass

        uid = UUID(user.user_id)
        kite = await _build_kite_client_for_user(uid)
        kc = kite._kc
        try:
            raw = await asyncio.to_thread(kc.holdings)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "kite holdings read failed",
                exc_info=True,
            )
            raw = []
        rows = raw if isinstance(raw, list) else []
        open_rows = [
            r for r in rows if int(r.get("quantity", 0)) > 0
        ]

        attr = await _fetch_holding_attribution(
            uid,
            [r["tradingsymbol"] for r in open_rows],
        )

        out_rows: list[HoldingRow] = []
        for r in open_rows:
            ctx = attr.get(r["tradingsymbol"], {})
            qty = int(r.get("quantity", 0))
            avg = Decimal(str(r.get("average_price", 0) or 0))
            ltp = Decimal(str(r.get("last_price", 0) or 0))
            pnl_inr = Decimal(str(r.get("pnl", 0) or 0))
            pnl_pct = (
                ((ltp - avg) / avg) * Decimal("100")
                if avg > 0
                else Decimal("0")
            )
            out_rows.append(
                HoldingRow(
                    tradingsymbol=r["tradingsymbol"],
                    exchange=r.get("exchange", "NSE"),
                    quantity=qty,
                    average_price=avg,
                    last_price=ltp,
                    pnl_inr=pnl_inr,
                    pnl_pct=pnl_pct,
                    days_held=ctx.get("days_held"),
                    strategy_id=ctx.get("strategy_id"),
                    strategy_name=ctx.get("strategy_name"),
                )
            )
        out = HoldingsResponse(rows=out_rows, ledger_drift=False)
        try:
            cache.set(
                cache_key,
                out.model_dump_json(),
                ttl=_HOLDINGS_CACHE_TTL,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "holdings cache set failed",
                exc_info=True,
            )
        return out

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
        cap = min(limit, 200)
        raw_rows = await asyncio.to_thread(
            _query_postback_events, user.user_id, cap
        )

        events: list[PostbackEvent] = []
        for r in raw_rows:
            try:
                p = json.loads(r["payload_json"])
            except Exception:  # noqa: BLE001
                continue
            ts_ns = int(r["ts_ns"])
            event_ts = datetime.fromtimestamp(
                ts_ns / 1_000_000_000, tz=UTC,
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

    # ---------------------------------------------------------------
    # Order-submission read endpoint (PR #1 — order-safety hardening)
    # ---------------------------------------------------------------

    @router.get(
        "/order-submissions",
        response_model=OrderSubmissionsResponse,
    )
    async def get_order_submissions(
        limit: int = 50,
        session_id: str | None = None,
        user: UserContext = Depends(pro_or_superuser),
    ) -> OrderSubmissionsResponse:
        """Return last N ``order_submitted_live`` events for user.

        Mirrors ``GET /postbacks`` filter shape. ``session_id``
        restricts to a single LiveRuntime session when supplied;
        without it the most-recent N across all sessions are
        returned (newest first). Full request/context/response
        payload exposed under each row's ``raw`` field for the
        Submissions panel expand-toggle.
        """
        cap = min(limit, 200)
        raw_rows = await asyncio.to_thread(
            _query_order_submission_events,
            user.user_id, cap, session_id,
        )

        out: list[OrderSubmissionEvent] = []
        for r in raw_rows:
            try:
                p = json.loads(r["payload_json"] or "{}")
            except Exception:  # noqa: BLE001
                continue
            ts_ns = int(r["ts_ns"])
            event_ts = datetime.fromtimestamp(
                ts_ns / 1_000_000_000, tz=UTC,
            ).isoformat().replace("+00:00", "Z")
            sid = r.get("strategy_id")
            out.append(
                OrderSubmissionEvent(
                    event_id=r["event_id"],
                    event_ts=event_ts,
                    ts_ns=ts_ns,
                    ts_date=str(r["ts_date"]),
                    session_id=str(r.get("session_id") or ""),
                    strategy_id=str(sid) if sid else None,
                    internal_order_id=str(
                        p.get("internal_order_id") or "",
                    ),
                    kite_order_id=str(
                        p.get("kite_order_id") or "",
                    ),
                    symbol=str(p.get("symbol") or ""),
                    side=str(p.get("side") or ""),
                    qty=int(p.get("qty") or 0),
                    dry_run=bool(p.get("dry_run")),
                    raw=p,
                )
            )

        return OrderSubmissionsResponse(submissions=out)

    return router
