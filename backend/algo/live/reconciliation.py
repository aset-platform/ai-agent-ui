"""Reconciliation loop — V2-3.

Periodic diff between Kite-reported broker positions and our
``algo.positions`` rows.  Alert-only: never writes broker values back.

Public API
----------
``compute_drift(our, broker, threshold)``
    Pure function.  Given two ``{symbol: qty}`` snapshots, returns
    the list of ``DriftItem`` namedtuples where the absolute diff
    exceeds *threshold* shares.

``reconcile_user(user_id)``
    Async orchestrator.  Fetches both sides, computes diff, persists
    state in ``algo.live_drift_state``, emits Iceberg events.

Market-hours guard
------------------
The scheduler job calls ``is_market_open_ist()`` before delegating
to this module, so the functions here do NOT re-check market hours.
They can be called independently in tests without time-mocking.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import text

from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.live.drift_repo import DriftRepo
from backend.db.engine import get_session_factory

_logger = logging.getLogger(__name__)

UTC = timezone.utc


# ---------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------

class DriftItem(NamedTuple):
    """A single position discrepancy."""
    symbol: str
    our_qty: int
    broker_qty: int
    diff: int          # broker_qty - our_qty


# ---------------------------------------------------------------
# Pure diff function
# ---------------------------------------------------------------

def compute_drift(
    our: dict[str, int],
    broker: dict[str, int],
    threshold: int = 0,
) -> list[DriftItem]:
    """Compute position diffs that exceed *threshold* shares.

    Args:
        our: ``{symbol: qty}`` from ``algo.positions``.
        broker: ``{symbol: qty}`` from Kite ``positions["net"]``.
        threshold: Absolute share difference below which the diff
            is ignored (0 = any non-zero diff).

    Returns:
        List of ``DriftItem`` for every symbol where
        ``abs(broker_qty - our_qty) > threshold``.
        Empty list when perfectly reconciled.
    """
    all_symbols = set(our) | set(broker)
    result: list[DriftItem] = []
    for sym in all_symbols:
        our_qty = our.get(sym, 0)
        broker_qty = broker.get(sym, 0)
        diff = broker_qty - our_qty
        if abs(diff) > threshold:
            result.append(
                DriftItem(
                    symbol=sym,
                    our_qty=our_qty,
                    broker_qty=broker_qty,
                    diff=diff,
                ),
            )
    result.sort(key=lambda d: d.symbol)
    return result


# ---------------------------------------------------------------
# Position fetchers
# ---------------------------------------------------------------

async def _fetch_our_positions(user_id: UUID) -> dict[str, int]:
    """Return ``{symbol: qty}`` from open ``algo.positions`` rows.

    Note: as of V2-3 the positions table has no ``source`` column yet
    (that lands in V2-5).  We reconcile ALL open positions for the
    user regardless of source.  In V2-5 the caller will filter to
    ``source='live'`` only.
    """
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT p.symbol, p.qty "
                    "FROM algo.positions p "
                    "JOIN algo.runs r ON r.id = p.run_id "
                    "WHERE r.user_id = :uid "
                    "  AND p.closed_at IS NULL"
                ),
                {"uid": user_id},
            )
        ).all()
    # Accumulate across multiple open runs.
    result: dict[str, int] = {}
    for sym, qty in rows:
        result[sym] = result.get(sym, 0) + int(qty)
    return result


async def _fetch_broker_positions(user_id: UUID) -> dict[str, int]:
    """Return ``{symbol: qty}`` from the Kite broker.

    Loads the user's KiteClient credentials from
    ``algo.broker_credentials``, then calls ``get_positions()``.
    Falls back to empty dict if credentials are missing / expired
    (logs a WARNING — the job handles absent credentials gracefully).
    """
    from backend.algo.broker.credentials_repo import (
        BrokerCredentialsRepo,
    )
    from backend.algo.broker.kite_client import KiteClient
    from auth.encryption import decrypt_fernet

    repo = BrokerCredentialsRepo()
    creds = await repo.get_credentials(user_id)
    if not creds:
        _logger.warning(
            "reconcile: no broker credentials for user %s",
            user_id,
        )
        return {}

    access_token_fernet = creds.get("access_token_fernet")
    if not access_token_fernet:
        _logger.warning(
            "reconcile: no access_token for user %s",
            user_id,
        )
        return {}

    try:
        access_token = decrypt_fernet(access_token_fernet)
    except Exception as exc:
        _logger.warning(
            "reconcile: could not decrypt token for user %s: %s",
            user_id, exc,
        )
        return {}

    kite = KiteClient(
        api_key=creds["api_key"],
        access_token=access_token,
    )
    raw = kite.get_positions()
    result: dict[str, int] = {}
    for pos in raw:
        sym = pos.get("tradingsymbol", "")
        qty = int(pos.get("quantity", 0))
        if sym and qty != 0:
            result[sym] = result.get(sym, 0) + qty
    return result


# ---------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------

async def reconcile_user(user_id: UUID) -> dict:
    """Reconcile broker ↔ our positions for one user.

    Steps:
    1. Fetch our open positions from PG.
    2. Fetch broker net positions from Kite.
    3. Compute drift (respecting per-user threshold).
    4. For each NEW drift symbol → emit ``position_drift_detected``.
       For previously seen, same diff → only bump counter.
       For symbols that were drifting but now agree → emit
       ``drift_resolved``.
    5. Persist all state changes to ``algo.live_drift_state``.
    6. Bulk-flush Iceberg events.

    Returns a summary dict (useful for tests and the job runner).
    """
    drift_repo = DriftRepo()
    threshold = await drift_repo.get_drift_threshold(user_id)

    our = await _fetch_our_positions(user_id)
    broker = await _fetch_broker_positions(user_id)

    current_drifts = compute_drift(our, broker, threshold)
    current_symbols = {d.symbol for d in current_drifts}

    open_rows = await drift_repo.get_open_drifts(user_id)
    open_symbols = {r["symbol"] for r in open_rows}

    events: list[dict] = []
    now = datetime.now(UTC)

    # --- Handle active drifts ---------------------------------
    for item in current_drifts:
        diff_payload = {
            "symbol": item.symbol,
            "our_qty": item.our_qty,
            "broker_qty": item.broker_qty,
            "diff": item.diff,
        }
        new_count = await drift_repo.upsert_drift(
            user_id, item.symbol, diff_payload,
        )
        is_new = item.symbol not in open_symbols
        _logger.debug(
            "reconcile: drift symbol=%s new=%s runs=%d",
            item.symbol, is_new, new_count,
        )
        if is_new:
            # First time we see this drift → emit event.
            events.append(event_row(
                session_id=UUID(int=0),  # system event
                user_id=user_id,
                strategy_id=None,
                mode="live",
                type_="position_drift_detected",
                payload={
                    **diff_payload,
                    "consecutive_runs": new_count,
                    "threshold": threshold,
                },
            ))

    # --- Handle resolved drifts -------------------------------
    resolved_symbols = open_symbols - current_symbols
    for sym in resolved_symbols:
        was_resolved = await drift_repo.resolve_drift(user_id, sym)
        if was_resolved:
            events.append(event_row(
                session_id=UUID(int=0),
                user_id=user_id,
                strategy_id=None,
                mode="live",
                type_="drift_resolved",
                payload={
                    "symbol": sym,
                    "resolution": "auto_position_match",
                },
            ))
            _logger.info(
                "reconcile: drift resolved symbol=%s user=%s",
                sym, user_id,
            )

    # Bulk flush events to Iceberg.
    if events:
        flush_events(events)

    _logger.info(
        "reconcile: user=%s our=%d broker=%d "
        "drifts=%d resolved=%d events=%d",
        user_id,
        len(our),
        len(broker),
        len(current_drifts),
        len(resolved_symbols),
        len(events),
    )
    return {
        "user_id": str(user_id),
        "our_positions": our,
        "broker_positions": broker,
        "drifts": [d._asdict() for d in current_drifts],
        "resolved_symbols": list(resolved_symbols),
        "events_emitted": len(events),
    }


# ---------------------------------------------------------------
# Market-hours guard helper (used by the scheduler job)
# ---------------------------------------------------------------

def is_market_open_ist() -> bool:
    """Return True when IST wall clock is in NSE session hours.

    09:15 – 15:30 IST, Monday – Friday.
    """
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    t = now.time()
    from datetime import time as _time
    open_t = _time(9, 15)
    close_t = _time(15, 30)
    return open_t <= t <= close_t
