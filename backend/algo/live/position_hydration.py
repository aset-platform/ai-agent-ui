"""Hydrate ``PositionTracker`` from existing Kite positions on
LiveRuntime spawn.

ASETPLTFRM-376 — without this, a fresh LiveRuntime starts with an
empty ``PositionTracker``. Yesterday's open positions (e.g. an
overnight CNC holding from a prior session) are invisible to the
runtime's EXIT logic — ``open_positions().get(symbol)`` returns
``None`` and SELL signals silent no-op.

Source priority (per spec):
  * ``kite._kc.positions()['net']`` row with ``quantity != 0`` AND
    ``product == 'MIS'``  → intraday position.
  * ``kite._kc.holdings()`` row with ``quantity > 0`` AND
    ``product == 'CNC'`` → overnight delivery equity.
  * If a symbol shows up in BOTH with different products we keep
    both legs (rare, but legal — different exits per product).

Entry timestamp resolution:
  Holdings carry no entry ts.  We look up the most-recent
  ``order_filled_live`` event for ``(user_id, symbol)`` in
  ``algo.events`` and use that row's ``ts_ns``.  Legacy holdings
  predating the algo era yield ``entry_ts=None``.

The result is applied to a ``PositionTracker`` via
``apply_hydrated_positions`` which fabricates a synthetic BUY
``Fill`` per leg.  Synthetic-fill is the cleanest fit: the
tracker's weighted-avg path already handles the entry-price +
qty bookkeeping we need, and there's no public ``seed_position``
API to abuse.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID, uuid4

from backend.algo.backtest.event_writer import event_row
from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill

_logger = logging.getLogger(__name__)

UTC = timezone.utc
# Payload ISO strings render in IST per
# feedback_ist_dates_user_facing — backend datetimes stay UTC,
# only the emitted string carries the +05:30 offset.
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class HydratedPosition:
    """One position/holding hydrated into the runtime at spawn.

    ``t1_pending`` flags the SEBI T+1 settlement window for CNC
    BUYs: post Jan 2023 a delivery BUY's shares appear on
    ``holdings()`` as ``quantity=0, t1_quantity=N`` for one
    trading session, then settle to ``quantity=N, t1_quantity=0``
    overnight. T+1 holdings are legally owned and sellable today
    via a regular CNC sell order (Zerodha auto-routes from T+1
    pool). We hydrate them as full ``qty`` so EXIT logic can
    target them, and surface ``t1_pending`` so the UI can chip
    the row distinctly.
    """

    symbol: str           # Internal ticker form (``RELIANCE.NS``).
    qty: int
    avg_price: Decimal
    source: str           # ``"positions"`` | ``"holdings"``.
    product: str          # ``"MIS"`` | ``"CNC"``.
    entry_ts: datetime | None  # UTC; None if no matching event.
    t1_pending: bool = False


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------


def _to_internal_ticker(tradingsymbol: str) -> str:
    """Kite tradingsymbol → internal ticker form.

    Kite emits ``"RELIANCE"``; the runtime keys positions on the
    yfinance suffix form ``"RELIANCE.NS"``.  We append ``.NS`` if
    the tradingsymbol doesn't already carry an exchange suffix.
    """
    if "." in tradingsymbol:
        return tradingsymbol
    return f"{tradingsymbol}.NS"


def _safe_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _safe_decimal(v: Any) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal("0")


def _default_events_reader(
    user_id: UUID, symbol: str,
) -> int | None:
    """Return the ``ts_ns`` of the most recent ``order_filled_live``
    event for ``(user_id, symbol)``, or ``None``.

    Uses the same DuckDB-on-Iceberg path as ``_query_postback_events``
    in ``routes/live.py``.  Best-effort: any failure → ``None``.
    """
    try:
        from backend.db.duckdb_engine import query_iceberg_table
        rows = query_iceberg_table(
            "algo.events",
            "SELECT ts_ns, payload_json "
            "FROM events "
            "WHERE user_id = ? "
            "  AND mode = 'live' "
            "  AND type = 'order_filled_live' "
            "ORDER BY ts_ns DESC "
            "LIMIT 50",
            [str(user_id)],
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "hydration: events read failed for user=%s sym=%s",
            user_id, symbol, exc_info=True,
        )
        return None
    import json as _json
    for r in rows:
        try:
            payload = _json.loads(r.get("payload_json") or "{}")
        except (ValueError, TypeError):
            continue
        if payload.get("symbol") == symbol:
            ts = r.get("ts_ns")
            try:
                return int(ts) if ts is not None else None
            except (TypeError, ValueError):
                return None
    return None


def _ns_to_utc(ts_ns: int | None) -> datetime | None:
    if ts_ns is None:
        return None
    try:
        return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


# ----------------------------------------------------------------
# Public API
# ----------------------------------------------------------------

EventsReader = Callable[[UUID, str], int | None]


def hydrate(
    kite: Any,
    strategy: Any,
    user_id: UUID,
    *,
    allowed_tickers: list[str] | None = None,
    events_reader: EventsReader | None = None,
) -> list[HydratedPosition]:
    """Read Kite positions + holdings and return one row per
    open leg.

    Parameters
    ----------
    kite : KiteClient
        The Kite client.  We reach in to ``kite._kc`` to call
        ``positions()`` / ``holdings()`` directly — mirrors the
        dashboard endpoints in ``routes/live.py``.
    strategy : Strategy
        Used only to log the strategy id for traceability; the
        ticker filter comes from ``allowed_tickers`` explicitly.
    user_id : UUID
        Used to query ``algo.events`` for the entry_ts lookup.
    allowed_tickers : list[str] | None
        Restrict hydration to this ticker list.  ``None`` or empty
        list → hydrate everything Kite returns (rare; the runtime
        usually wires its caps ``allowed_tickers`` here).
    events_reader : Callable
        Override the Iceberg lookup — used by tests to inject a
        canned ``order_filled_live`` ts_ns per symbol.
    """
    reader = events_reader or _default_events_reader
    allowed_set: set[str] | None = (
        set(allowed_tickers) if allowed_tickers else None
    )

    out: list[HydratedPosition] = []

    # Source 1: net positions (intraday MIS).
    try:
        kc = getattr(kite, "_kc", None)
        raw_pos = kc.positions() if kc is not None else {}
    except Exception:  # noqa: BLE001
        _logger.warning(
            "hydration: positions() raised — proceeding with "
            "holdings-only", exc_info=True,
        )
        raw_pos = {}
    net = (
        raw_pos.get("net", [])
        if isinstance(raw_pos, dict) else []
    )
    for r in net:
        product = (r.get("product") or "").upper()
        qty = _safe_int(r.get("quantity"))
        if qty == 0 or product != "MIS":
            continue
        internal = _to_internal_ticker(
            r.get("tradingsymbol") or "",
        )
        if not internal:
            continue
        if allowed_set is not None and internal not in allowed_set:
            continue
        avg = _safe_decimal(r.get("average_price"))
        ts_ns = reader(user_id, internal)
        out.append(HydratedPosition(
            symbol=internal,
            qty=qty,
            avg_price=avg,
            source="positions",
            product="MIS",
            entry_ts=_ns_to_utc(ts_ns),
        ))

    # Source 2: holdings (overnight CNC).
    try:
        kc = getattr(kite, "_kc", None)
        raw_hold = kc.holdings() if kc is not None else []
    except Exception:  # noqa: BLE001
        _logger.warning(
            "hydration: holdings() raised — proceeding with "
            "positions-only", exc_info=True,
        )
        raw_hold = []
    rows = raw_hold if isinstance(raw_hold, list) else []
    for r in rows:
        # SEBI T+1 (post Jan 2023): a CNC BUY's shares appear as
        # quantity=0 + t1_quantity=N during the settlement session.
        # Either pool counts as "held"; sum them so the algo sees
        # the position and EXIT signals can fire CNC SELLs.
        settled_qty = _safe_int(r.get("quantity"))
        t1_qty = _safe_int(r.get("t1_quantity"))
        effective_qty = settled_qty + t1_qty
        product = (r.get("product") or "CNC").upper()
        if effective_qty <= 0 or product != "CNC":
            continue
        internal = _to_internal_ticker(
            r.get("tradingsymbol") or "",
        )
        if not internal:
            continue
        if allowed_set is not None and internal not in allowed_set:
            continue
        avg = _safe_decimal(r.get("average_price"))
        ts_ns = reader(user_id, internal)
        out.append(HydratedPosition(
            symbol=internal,
            qty=effective_qty,
            avg_price=avg,
            source="holdings",
            product="CNC",
            entry_ts=_ns_to_utc(ts_ns),
            t1_pending=(settled_qty == 0 and t1_qty > 0),
        ))

    _logger.info(
        "LiveRuntime hydration: user=%s strategy=%s — "
        "%d position(s) hydrated (positions=%d holdings=%d)",
        user_id,
        getattr(strategy, "id", None),
        len(out),
        sum(1 for h in out if h.source == "positions"),
        sum(1 for h in out if h.source == "holdings"),
    )
    return out


def apply_hydrated_positions(
    tracker: PositionTracker,
    hydrated: list[HydratedPosition],
) -> None:
    """Seed ``tracker`` with one synthetic BUY ``Fill`` per
    hydrated leg.

    The synthetic fill carries today's date as ``fill_date``
    (Fill.fill_date is a ``date`` and not nullable); the real
    entry timestamp lives on the emitted ``position_hydrated``
    event payload.
    """
    today = datetime.now(UTC).date()
    for h in hydrated:
        if h.qty <= 0:
            continue
        opened: date = (
            h.entry_ts.date() if h.entry_ts else today
        )
        tracker.apply_fill(Fill(
            intent_id=uuid4(),
            ticker=h.symbol,
            side="BUY",
            qty=h.qty,
            fill_price=h.avg_price,
            fill_date=opened,
            fees_inr=Decimal("0"),
            fee_rates_version="hydrated",
        ))


def hydration_events(
    *,
    session_id: UUID,
    user_id: UUID,
    strategy_id: UUID | None,
    hydrated: list[HydratedPosition],
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Build one ``position_hydrated`` algo.events row per leg.

    Returned rows are appended to ``LiveRuntime._events`` so they
    flush at the next ``_flush_events_now()`` call.
    """
    rows: list[dict[str, Any]] = []
    for h in hydrated:
        rows.append(event_row(
            session_id=session_id,
            user_id=user_id,
            strategy_id=strategy_id,
            mode="live",
            type_="position_hydrated",
            payload={
                # Live: omit dry_run (always False by construction).
                # Dry-run rehearsals: include "dry_run": true so
                # the Events panel can chip them distinctly.
                **({"dry_run": True} if dry_run else {}),
                "symbol": h.symbol,
                "qty": h.qty,
                "avg_price": str(h.avg_price),
                "source": h.source,
                "product": h.product,
                "entry_ts": (
                    h.entry_ts.astimezone(IST).isoformat()
                    if h.entry_ts else None
                ),
                "t1_pending": h.t1_pending,
            },
        ))
    return rows
