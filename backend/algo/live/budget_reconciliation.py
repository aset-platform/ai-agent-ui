"""Periodic reconciliation of budget reservations.

Two passes per tick:

1. ``reconcile_pending_timeouts`` — PENDING > 120s → TIMEOUT.
2. ``reconcile_submitted`` — for each SUBMITTED/PARTIAL, query
   Kite for order status; transition accordingly. SUBMITTED
   with no Kite update for 5 minutes → force TIMEOUT.

Driven by ``backend/algo/jobs/algo_reconciliation.py`` once per
scheduler tick.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.algo.live.budget import (
    _build_kite_for_user,
    transition,
)
from backend.db.engine import disposable_pg_session
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
)

_logger = logging.getLogger(__name__)

PENDING_TIMEOUT_S = 120
SUBMITTED_HARD_TIMEOUT_S = 300


async def _list_pending() -> list[BudgetReservation]:
    """Pull reservations whose latest state is PENDING.

    Computes the most-recent row per reservation_id first
    (DISTINCT ON ordered by transitioned_at DESC, id DESC),
    then filters — so a reservation that has since transitioned
    to a terminal state (TIMEOUT, FILLED, …) is excluded even
    though it has an older PENDING row in the ledger.
    """
    async with disposable_pg_session() as session:
        result = await session.execute(
            text(
                "SELECT * FROM ( "
                "  SELECT DISTINCT ON (reservation_id) "
                "    reservation_id, user_id, strategy_id, "
                "    state, ticker, side, qty, reserved_inr, "
                "    filled_qty, filled_inr, kite_order_id, "
                "    transitioned_at, metadata, error_text "
                "  FROM algo.budget_reservations "
                "  ORDER BY reservation_id, "
                "           transitioned_at DESC, id DESC "
                ") latest "
                "WHERE latest.state = 'PENDING'"
            ),
        )
        rows = result.mappings().all()
    out: list[BudgetReservation] = []
    for row in rows:
        d = dict(row)
        d["state"] = ReservationState(d["state"])
        out.append(BudgetReservation(**d))
    return out


async def _list_submitted_and_partial() -> list[BudgetReservation]:
    async with disposable_pg_session() as session:
        result = await session.execute(
            text(
                "SELECT * FROM ( "
                "  SELECT DISTINCT ON (reservation_id) "
                "    reservation_id, user_id, strategy_id, "
                "    state, ticker, side, qty, reserved_inr, "
                "    filled_qty, filled_inr, kite_order_id, "
                "    transitioned_at, metadata, error_text "
                "  FROM algo.budget_reservations "
                "  ORDER BY reservation_id, "
                "           transitioned_at DESC, id DESC "
                ") latest "
                "WHERE latest.state IN ('SUBMITTED', 'PARTIAL')"
            ),
        )
        rows = result.mappings().all()
    out: list[BudgetReservation] = []
    for row in rows:
        d = dict(row)
        d["state"] = ReservationState(d["state"])
        out.append(BudgetReservation(**d))
    return out


async def _fetch_order_status_for_user(
    kite_client,
    kite_order_id: str,
    user_id: UUID,
) -> dict[str, Any] | None:
    """Pull the latest leg of a Kite order's history using a pre-built
    KiteClient. None on error or empty history."""
    try:
        history = await asyncio.to_thread(
            kite_client._kc.order_history,
            kite_order_id,
        )
        if not history:
            return None
        return history[-1]
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "kite order_history failed user=%s order=%s: %s",
            user_id,
            kite_order_id,
            exc,
            exc_info=True,
        )
        return None


async def reconcile_pending_timeouts() -> dict:
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=PENDING_TIMEOUT_S)

    pending = await _list_pending()
    total = len(pending)
    stale = [r for r in pending if r.transitioned_at < threshold]
    _logger.info(
        "budget_reconcile pending: found=%d stale=%d (>%ds)",
        total, len(stale), PENDING_TIMEOUT_S,
    )
    timed_out = 0
    errors = 0
    for res in stale:
        try:
            await transition(
                reservation_id=res.reservation_id,
                new_state=ReservationState.TIMEOUT,
                error_text=(f"PENDING timeout > {PENDING_TIMEOUT_S}s"),
            )
            timed_out += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            _logger.error(
                "PENDING-timeout transition failed res=%s: %s",
                res.reservation_id,
                exc,
                exc_info=True,
            )
    if stale:
        _logger.info(
            "budget_reconcile pending: timed_out=%d errors=%d",
            timed_out, errors,
        )
    return {"found": total, "stale": len(stale), "timed_out": timed_out, "errors": errors}


_SYNTHETIC_ORDER_PREFIXES = ("DRY_", "paper-", "dryrun-")


def _is_synthetic_order(order_id: str | None) -> bool:
    """True for paper/dryrun order IDs that have no Kite counterpart."""
    return bool(
        order_id
        and any(order_id.startswith(p) for p in _SYNTHETIC_ORDER_PREFIXES)
    )


async def _timeout_synthetic_if_stale(res: BudgetReservation) -> None:
    """Timeout a synthetic (paper/dryrun) reservation if past hard timeout.

    Called directly without any Kite API call — synthetic order IDs are
    invalid on Kite and would only produce 'Invalid order_id' warnings.
    """
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=SUBMITTED_HARD_TIMEOUT_S)
    if res.transitioned_at < threshold:
        if res.filled_inr > Decimal("0"):
            await transition(
                reservation_id=res.reservation_id,
                new_state=ReservationState.FILLED,
                filled_qty=res.filled_qty,
                filled_inr=res.filled_inr,
            )
        else:
            await transition(
                reservation_id=res.reservation_id,
                new_state=ReservationState.TIMEOUT,
                error_text=(
                    f"synthetic order ({res.kite_order_id!r}) — "
                    f"SUBMITTED hard timeout > {SUBMITTED_HARD_TIMEOUT_S}s"
                ),
            )


async def reconcile_one(
    res: BudgetReservation,
    kite_client,
    *,
    order_book: dict[str, dict] | None = None,
) -> None:
    """Reconcile a single SUBMITTED/PARTIAL reservation against Kite.

    Only called for real (non-synthetic) Kite orders. Synthetic orders
    are handled by ``_timeout_synthetic_if_stale`` before this loop.

    ``order_book`` is an optional pre-fetched ``{order_id: row}`` map
    from ``kc._kc.orders()`` (one call per user in
    ``reconcile_submitted``). When provided the map is consulted first;
    per-order ``order_history`` is only called as a fallback (order not
    found in today's book).  Passing ``order_book=None`` (default)
    restores the original per-order-history behaviour.
    """
    if res.kite_order_id is None:
        return

    now = datetime.now(timezone.utc)
    threshold = now - timedelta(
        seconds=SUBMITTED_HARD_TIMEOUT_S,
    )

    status_row: dict[str, Any] | None = None
    if order_book is not None and res.kite_order_id is not None:
        status_row = order_book.get(str(res.kite_order_id))
    if status_row is None:
        status_row = await _fetch_order_status_for_user(
            kite_client,
            res.kite_order_id,
            res.user_id,
        )
    if status_row is None:
        if res.transitioned_at < threshold:
            # Kite drops order history after 1 trading day. If filled_inr > 0
            # the fill was already recorded (via webhook or sync) — treat as
            # FILLED rather than TIMEOUT so open_pos_cost stays accurate.
            if res.filled_inr > Decimal("0"):
                _logger.info(
                    "reconcile_one: Kite history gone but filled_inr=%.2f"
                    " — marking FILLED (not TIMEOUT) res=%s ticker=%s",
                    res.filled_inr,
                    res.reservation_id,
                    res.ticker,
                )
                await transition(
                    reservation_id=res.reservation_id,
                    new_state=ReservationState.FILLED,
                    filled_qty=res.filled_qty,
                    filled_inr=res.filled_inr,
                )
            else:
                await transition(
                    reservation_id=res.reservation_id,
                    new_state=ReservationState.TIMEOUT,
                    error_text=(
                        f"SUBMITTED hard timeout > "
                        f"{SUBMITTED_HARD_TIMEOUT_S}s, "
                        "Kite unreachable"
                    ),
                )
        return

    kite_status = str(status_row.get("status", "")).upper()
    filled_qty = int(
        status_row.get("filled_quantity", 0) or 0,
    )
    avg_price = Decimal(
        str(status_row.get("average_price", 0) or 0),
    )
    filled_inr = Decimal(filled_qty) * avg_price

    if kite_status == "COMPLETE":
        await transition(
            reservation_id=res.reservation_id,
            new_state=ReservationState.FILLED,
            filled_qty=filled_qty,
            filled_inr=filled_inr,
        )
    elif kite_status == "CANCELLED":
        if filled_qty > 0:
            await transition(
                reservation_id=res.reservation_id,
                new_state=(ReservationState.PARTIAL_CANCELLED),
                filled_qty=filled_qty,
                filled_inr=filled_inr,
            )
        else:
            await transition(
                reservation_id=res.reservation_id,
                new_state=ReservationState.CANCELLED,
            )
    elif kite_status == "REJECTED":
        await transition(
            reservation_id=res.reservation_id,
            new_state=ReservationState.REJECTED,
            error_text=str(
                status_row.get("status_message", "") or "rejected",
            )[:500],
        )
    elif kite_status == "OPEN" and filled_qty > 0:
        await transition(
            reservation_id=res.reservation_id,
            new_state=ReservationState.PARTIAL,
            filled_qty=filled_qty,
            filled_inr=filled_inr,
        )
    elif res.transitioned_at < threshold:
        await transition(
            reservation_id=res.reservation_id,
            new_state=ReservationState.TIMEOUT,
            error_text=(
                f"SUBMITTED hard timeout > "
                f"{SUBMITTED_HARD_TIMEOUT_S}s, "
                f"Kite status={kite_status}"
            ),
        )


async def reconcile_submitted() -> dict:
    """Reconcile SUBMITTED/PARTIAL reservations.

    Splits reservations into two buckets:
    - Synthetic (paper-/dryrun-/DRY_): timed out internally; no Kite API call.
    - Real Kite orders: grouped by user_id with one KiteClient per user.

    Returns a summary dict with counts per phase.
    """
    import time

    reservations = await _list_submitted_and_partial()
    if not reservations:
        _logger.info("budget_reconcile submitted: nothing to process")
        return {"synthetic": 0, "real": 0, "kite_checked": 0}

    synthetic = [r for r in reservations if _is_synthetic_order(r.kite_order_id)]
    real = [r for r in reservations if not _is_synthetic_order(r.kite_order_id)]

    _logger.info(
        "budget_reconcile submitted: total=%d synthetic=%d real=%d",
        len(reservations), len(synthetic), len(real),
    )

    # --- Synthetic phase (no Kite API) ---
    syn_timed_out = 0
    syn_errors = 0
    if synthetic:
        t0 = time.monotonic()
        for res in synthetic:
            try:
                before = res.state  # noqa: F841 — for future debug
                await _timeout_synthetic_if_stale(res)
                syn_timed_out += 1
            except Exception as exc:  # noqa: BLE001
                syn_errors += 1
                _logger.error(
                    "budget reconcile_one (synthetic) failed res=%s: %s",
                    res.reservation_id, exc, exc_info=True,
                )
        _logger.info(
            "budget_reconcile synthetic: timed_out=%d errors=%d elapsed=%.1fs",
            syn_timed_out, syn_errors, time.monotonic() - t0,
        )

    if not real:
        return {"synthetic": len(synthetic), "syn_timed_out": syn_timed_out, "real": 0, "kite_checked": 0}

    # --- Real Kite orders phase ---
    t0 = time.monotonic()
    user_ids = {res.user_id for res in real}
    kite_clients: dict[UUID, Any] = {}
    for uid in user_ids:
        try:
            kite_clients[uid] = await _build_kite_for_user(uid)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "budget reconcile: no kite for user=%s: %s", uid, exc,
            )

    # Batch-fetch each user's order book once (one orders() call/user).
    # Falls back to per-order history for any user whose orders() fails.
    order_books: dict[UUID, dict[str, dict]] = {}
    for uid, kc in kite_clients.items():
        try:
            book = await asyncio.to_thread(kc._kc.orders)
            order_books[uid] = {
                str(o.get("order_id")): o for o in (book or [])
            }
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "budget reconcile: orders() failed user=%s: %s — "
                "falling back to per-order history",
                uid,
                exc,
                exc_info=True,
            )
            # uid absent from order_books → reconcile_one falls back

    kite_checked = 0
    kite_errors = 0
    for i, res in enumerate(real, 1):
        kc = kite_clients.get(res.user_id)
        if kc is None:
            continue
        if i % 50 == 0 or i == len(real):
            _logger.info(
                "budget_reconcile kite: %d/%d checked elapsed=%.1fs",
                i, len(real), time.monotonic() - t0,
            )
        try:
            await reconcile_one(
                res, kc, order_book=order_books.get(res.user_id)
            )
            kite_checked += 1
        except Exception as exc:  # noqa: BLE001
            kite_errors += 1
            _logger.error(
                "budget reconcile_one failed res=%s: %s",
                res.reservation_id, exc, exc_info=True,
            )

    _logger.info(
        "budget_reconcile kite: done checked=%d errors=%d elapsed=%.1fs",
        kite_checked, kite_errors, time.monotonic() - t0,
    )
    return {
        "synthetic": len(synthetic),
        "syn_timed_out": syn_timed_out,
        "real": len(real),
        "kite_checked": kite_checked,
        "kite_errors": kite_errors,
    }


async def reconcile() -> dict:
    """Entrypoint called by the scheduler tick.

    Returns a summary dict: pending + submitted phase counts.
    """
    import time

    t_start = time.monotonic()
    _logger.info("budget_reconcile: starting")

    pending_summary = await reconcile_pending_timeouts()
    submitted_summary = await reconcile_submitted()

    elapsed = time.monotonic() - t_start
    _logger.info(
        "budget_reconcile: done in %.1fs — pending(found=%d timed_out=%d) "
        "submitted(synthetic=%d syn_timed_out=%d real=%d kite_checked=%d)",
        elapsed,
        pending_summary.get("found", 0),
        pending_summary.get("timed_out", 0),
        submitted_summary.get("synthetic", 0),
        submitted_summary.get("syn_timed_out", 0),
        submitted_summary.get("real", 0),
        submitted_summary.get("kite_checked", 0),
    )
    return {"pending": pending_summary, "submitted": submitted_summary, "elapsed_s": round(elapsed, 1)}
