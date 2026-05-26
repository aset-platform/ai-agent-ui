"""Algo Portfolio dashboard tab — GET
/v1/algo/portfolio/positions.

Returns currently-open algo-attributed positions (intraday
MIS from kc.positions().net + overnight CNC from
kc.holdings()), joined with strategy attribution and
augmented with days_held + t1_pending flags.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.live.budget import _build_kite_for_user
from backend.algo.live.reconciliation import is_market_open_ist
from backend.algo.routes.live import _fetch_strategy_attribution
from backend.cache import get_cache

_logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# Lookback floor for joining algo.events attribution.
# 2024-01-01 covers any CNC overnight position opened
# since the v1 algo trading launch (~17 months); plenty
# of margin to attribute the oldest still-held position
# without scanning the entire algo.events Iceberg table.
_ATTRIBUTION_SINCE = "2024-01-01"

# Redis cache TTL (matches the existing
# /v1/algo/live/positions endpoint behavior — TTL-only, no
# write-through invalidation in v1).
_CACHE_TTL_S = 60


class AlgoPositionRow(BaseModel):
    """One algo-attributed open position."""

    model_config = ConfigDict(extra="forbid")

    tradingsymbol: str
    internal_ticker: str
    product: Literal["MIS", "CNC"]
    quantity: int = Field(ge=0)
    avg_price: Decimal = Field(ge=Decimal("0"))
    last_price: Decimal = Field(ge=Decimal("0"))
    pnl_inr: Decimal
    pnl_pct: Decimal
    strategy_id: UUID
    strategy_name: str
    entry_ts: datetime | None = None
    days_held: int = Field(ge=0)
    t1_pending: bool = False
    # "live" = real Kite-held position; "paper" = synthesized
    # from algo.events `mode='paper'` fills (rendered on the
    # dashboard Algo tab with a "PAPER" badge). Default
    # preserves backwards compatibility for older clients.
    source: Literal["live", "paper"] = "live"


class AlgoPositionsResponse(BaseModel):
    """Wire shape for the dashboard Algo tab."""

    model_config = ConfigDict(extra="forbid")

    positions: list[AlgoPositionRow]
    as_of: datetime
    market_open: bool


def _days_held(entry_ts: datetime | None) -> int:
    """Floor(today_ist - entry_date_ist).days, clamped ≥ 0."""
    if entry_ts is None:
        return 0
    today_ist = datetime.now(_IST).date()
    entry_ist = entry_ts.astimezone(_IST).date()
    return max(0, (today_ist - entry_ist).days)


def _to_internal_ticker(tradingsymbol: str) -> str:
    """Kite tradingsymbol → internal ticker (e.g. INFY.NS).

    Indian-only mapping for now (matches existing
    backend/algo/live/position_hydration.py behavior).
    """
    if not tradingsymbol:
        return ""
    return f"{tradingsymbol}.NS"


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _safe_decimal(v: Any) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except (TypeError, ValueError):
        return Decimal("0")


def _row_from_position(
    raw: dict[str, Any],
    attr: dict[str, Any],
) -> AlgoPositionRow:
    """Convert a Kite positions().net row to AlgoPositionRow."""
    tsym = raw.get("tradingsymbol") or ""
    qty = _safe_int(raw.get("quantity"))
    avg = _safe_decimal(raw.get("average_price"))
    ltp = _safe_decimal(raw.get("last_price"))
    pnl_inr = Decimal(qty) * (ltp - avg)
    pnl_pct = (
        ((ltp - avg) / avg) * Decimal("100")
        if avg > 0
        else Decimal("0")
    )
    entry_ts_str = attr.get("entry_ts_utc")
    entry_ts = (
        datetime.fromisoformat(entry_ts_str)
        if entry_ts_str
        else None
    )
    return AlgoPositionRow(
        tradingsymbol=tsym,
        internal_ticker=_to_internal_ticker(tsym),
        product="MIS",
        quantity=abs(qty),
        avg_price=avg,
        last_price=ltp,
        pnl_inr=pnl_inr,
        pnl_pct=pnl_pct,
        strategy_id=UUID(attr["strategy_id"]),
        strategy_name=attr.get("strategy_name") or "",
        entry_ts=entry_ts,
        days_held=_days_held(entry_ts),
        t1_pending=False,
    )


def _row_from_holding(
    raw: dict[str, Any],
    attr: dict[str, Any],
) -> AlgoPositionRow:
    """Convert a Kite holdings() row to AlgoPositionRow.

    SEBI T+1: a CNC BUY shows quantity=0 + t1_quantity=N
    during settlement. Both pools are 'held' from the algo
    perspective; we sum them and flag t1_pending when the
    settled pool is empty.
    """
    tsym = raw.get("tradingsymbol") or ""
    settled = _safe_int(raw.get("quantity"))
    t1 = _safe_int(raw.get("t1_quantity"))
    effective = settled + t1
    avg = _safe_decimal(raw.get("average_price"))
    ltp = _safe_decimal(raw.get("last_price"))
    pnl_inr = Decimal(effective) * (ltp - avg)
    pnl_pct = (
        ((ltp - avg) / avg) * Decimal("100")
        if avg > 0
        else Decimal("0")
    )
    entry_ts_str = attr.get("entry_ts_utc")
    entry_ts = (
        datetime.fromisoformat(entry_ts_str)
        if entry_ts_str
        else None
    )
    return AlgoPositionRow(
        tradingsymbol=tsym,
        internal_ticker=_to_internal_ticker(tsym),
        product="CNC",
        quantity=effective,
        avg_price=avg,
        last_price=ltp,
        pnl_inr=pnl_inr,
        pnl_pct=pnl_pct,
        strategy_id=UUID(attr["strategy_id"]),
        strategy_name=attr.get("strategy_name") or "",
        entry_ts=entry_ts,
        days_held=_days_held(entry_ts),
        t1_pending=(settled == 0 and t1 > 0),
    )


async def _paper_positions_from_events(
    user_id: UUID,
) -> list[AlgoPositionRow]:
    """Synthesize open paper positions from
    ``algo.events`` (mode='paper', type='order_filled').

    Net qty per symbol = sum(qty if side='BUY' else -qty);
    rows with qty > 0 become positions. Average BUY price
    is the weighted avg of BUY fills since last flat;
    last_price falls back to the most recent BUY
    fill_price (no Kite LTP available for paper positions
    until a separate LTP enrichment lands). PnL is computed
    against last_price, so paper rows show 0 PnL when no
    further fills have moved the synthetic mark.

    Joined with ``_fetch_strategy_attribution`` (mode-
    unscoped since we already constrained to paper above)
    so rows carry the originating strategy_id +
    strategy_name + entry_ts.

    Returns [] on any read failure (fail-open — the Algo
    tab still renders live positions; paper rows just
    don't appear).
    """
    try:
        rows = await asyncio.to_thread(
            __import__(
                "backend.db.duckdb_engine",
                fromlist=["query_iceberg_table"],
            ).query_iceberg_table,
            "algo.events",
            "SELECT ts_ns, strategy_id, payload_json "
            "FROM events "
            "WHERE user_id = ? "
            "  AND mode = 'paper' "
            "  AND type = 'order_filled' "
            "  AND ts_date >= ? "
            "ORDER BY ts_ns ASC",
            [str(user_id), _ATTRIBUTION_SINCE],
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "paper positions iceberg read failed "
            "for user=%s", user_id, exc_info=True,
        )
        return []

    import json as _json

    # Per-symbol running state: net qty, summed cost basis
    # (only BUY-side cost), last fill price, first BUY ts,
    # strategy id (first seen).
    per: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            payload = _json.loads(
                row.get("payload_json") or "{}",
            )
        except (ValueError, TypeError):
            continue
        sym = payload.get("ticker") or ""
        if not sym:
            continue
        side = (payload.get("side") or "").upper()
        try:
            qty = int(payload.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        try:
            price = Decimal(
                str(payload.get("fill_price") or 0),
            )
        except (TypeError, ValueError):
            price = Decimal("0")
        state = per.setdefault(sym, {
            "net_qty": 0,
            "buy_qty_sum": 0,
            "buy_cost_sum": Decimal("0"),
            "last_price": Decimal("0"),
            "entry_ts_ns": None,
            "strategy_id": row.get("strategy_id"),
        })
        if side == "BUY":
            state["net_qty"] += qty
            state["buy_qty_sum"] += qty
            state["buy_cost_sum"] += Decimal(qty) * price
            if state["entry_ts_ns"] is None:
                state["entry_ts_ns"] = int(
                    row.get("ts_ns") or 0,
                )
            if not state["strategy_id"]:
                state["strategy_id"] = row.get("strategy_id")
        elif side == "SELL":
            state["net_qty"] -= qty
            # If position flips back to zero/negative, reset
            # cost basis so a subsequent BUY starts a new
            # entry rather than blending with the closed leg.
            if state["net_qty"] <= 0:
                state["buy_qty_sum"] = 0
                state["buy_cost_sum"] = Decimal("0")
                state["entry_ts_ns"] = None
        state["last_price"] = price

    out: list[AlgoPositionRow] = []
    for sym, st in per.items():
        if st["net_qty"] <= 0:
            continue
        sid_raw = st.get("strategy_id")
        if not sid_raw:
            continue
        try:
            sid = UUID(str(sid_raw))
        except (ValueError, TypeError):
            continue
        avg = (
            (st["buy_cost_sum"] / Decimal(st["buy_qty_sum"]))
            if st["buy_qty_sum"] > 0
            else Decimal("0")
        )
        ltp = st["last_price"] if st["last_price"] > 0 else avg
        pnl_inr = Decimal(st["net_qty"]) * (ltp - avg)
        pnl_pct = (
            ((ltp - avg) / avg) * Decimal("100")
            if avg > 0
            else Decimal("0")
        )
        entry_ts_ns = st.get("entry_ts_ns")
        entry_ts = (
            datetime.fromtimestamp(
                entry_ts_ns / 1_000_000_000, tz=timezone.utc,
            )
            if entry_ts_ns
            else None
        )
        # Resolve strategy_name via the same helper Epic B
        # uses for live. Pass mode-unscoped lookback.
        out.append(AlgoPositionRow(
            tradingsymbol=sym.replace(".NS", ""),
            internal_ticker=sym,
            product="MIS",  # paper has no MIS/CNC distinction
            quantity=st["net_qty"],
            avg_price=avg,
            last_price=ltp,
            pnl_inr=pnl_inr,
            pnl_pct=pnl_pct,
            strategy_id=sid,
            strategy_name="",  # filled in by caller via attribution lookup
            entry_ts=entry_ts,
            days_held=_days_held(entry_ts),
            t1_pending=False,
            source="paper",
        ))
    return out


async def _get_algo_positions_impl(
    *,
    user_id: UUID,
) -> AlgoPositionsResponse:
    """Pure async impl, testable without HTTP harness."""
    cache = get_cache()
    cache_key = (
        f"cache:algo:portfolio:positions:{user_id}"
    )
    if cache is not None:
        cached_raw = cache.get(cache_key)
        if cached_raw:
            try:
                return (
                    AlgoPositionsResponse
                    .model_validate_json(cached_raw)
                )
            except (ValueError, TypeError):
                pass

    # Paper positions are independent of Kite; pull them
    # first so they're rendered even when Kite is absent.
    paper_rows = await _paper_positions_from_events(user_id)

    raw_pos: dict[str, Any] = {}
    raw_hold: list[Any] = []
    try:
        kite = await _build_kite_for_user(user_id)
    except RuntimeError as exc:
        _logger.info(
            "algo portfolio: no Kite for user=%s: %s",
            user_id, exc,
        )
        kite = None

    if kite is not None:
        kc = kite._kc
        try:
            raw_pos, raw_hold = await asyncio.gather(
                asyncio.to_thread(kc.positions),
                asyncio.to_thread(kc.holdings),
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "algo portfolio: kite read failed",
                exc_info=True,
            )
            raw_pos, raw_hold = {}, []

    net = (
        raw_pos.get("net", [])
        if isinstance(raw_pos, dict)
        else []
    )
    open_pos = [
        r for r in net
        if _safe_int(r.get("quantity")) != 0
    ]
    open_hold = [
        r for r in (raw_hold or [])
        if (
            _safe_int(r.get("quantity"))
            + _safe_int(r.get("t1_quantity")) > 0
        )
    ]

    symbols = sorted({
        _to_internal_ticker(r.get("tradingsymbol", ""))
        for r in open_pos + open_hold
    } | {r.internal_ticker for r in paper_rows} - {""})

    attr = await _fetch_strategy_attribution(
        user_id, symbols, since_date=_ATTRIBUTION_SINCE,
    )

    rows: list[AlgoPositionRow] = []
    for r in open_pos:
        sym = _to_internal_ticker(r.get("tradingsymbol", ""))
        ctx = attr.get(sym)
        if not ctx or not ctx.get("strategy_id"):
            continue
        rows.append(_row_from_position(r, ctx))
    for r in open_hold:
        sym = _to_internal_ticker(r.get("tradingsymbol", ""))
        ctx = attr.get(sym)
        if not ctx or not ctx.get("strategy_id"):
            continue
        rows.append(_row_from_holding(r, ctx))

    # Backfill strategy_name on paper rows via attribution.
    # If attribution is empty for a paper symbol, leave the
    # blank string — UI renders "—" or just the strategy_id.
    for pr in paper_rows:
        ctx = attr.get(pr.internal_ticker) or {}
        if ctx.get("strategy_name"):
            rows.append(pr.model_copy(update={
                "strategy_name": ctx.get("strategy_name") or "",
            }))
        else:
            rows.append(pr)

    rows.sort(
        key=lambda r: (-r.pnl_inr, r.tradingsymbol),
    )

    resp = AlgoPositionsResponse(
        positions=rows,
        as_of=datetime.now(timezone.utc),
        market_open=is_market_open_ist(),
    )

    if cache is not None:
        try:
            cache.set(
                cache_key,
                resp.model_dump_json(),
                ttl=_CACHE_TTL_S,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "algo portfolio: cache set failed",
                exc_info=True,
            )

    return resp


def create_portfolio_router() -> APIRouter:
    """Builder so `backend/routes.py` mounts it under /v1."""
    router = APIRouter(
        prefix="/algo/portfolio",
        tags=["algo-trading"],
    )

    @router.get(
        "/positions",
        response_model=AlgoPositionsResponse,
    )
    async def get_positions(
        user: UserContext = Depends(pro_or_superuser),
    ) -> AlgoPositionsResponse:
        return await _get_algo_positions_impl(
            user_id=UUID(user.user_id),
        )

    return router
