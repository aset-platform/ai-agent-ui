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
    _session_factory,
    transition,
)
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
)

_logger = logging.getLogger(__name__)

PENDING_TIMEOUT_S = 120
SUBMITTED_HARD_TIMEOUT_S = 300


async def _list_pending() -> list[BudgetReservation]:
    """Pull all reservations whose latest state is PENDING."""
    factory = _session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT DISTINCT ON (reservation_id) "
                "  reservation_id, user_id, strategy_id, "
                "  state, ticker, side, qty, reserved_inr, "
                "  filled_qty, filled_inr, kite_order_id, "
                "  transitioned_at, metadata, error_text "
                "FROM algo.budget_reservations "
                "ORDER BY reservation_id, "
                "         transitioned_at DESC"
            ),
        )
        rows = result.mappings().all()
    out: list[BudgetReservation] = []
    for row in rows:
        d = dict(row)
        d["state"] = ReservationState(d["state"])
        if d["state"] == ReservationState.PENDING:
            out.append(BudgetReservation(**d))
    return out


async def _list_submitted_and_partial(
) -> list[BudgetReservation]:
    factory = _session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT DISTINCT ON (reservation_id) "
                "  reservation_id, user_id, strategy_id, "
                "  state, ticker, side, qty, reserved_inr, "
                "  filled_qty, filled_inr, kite_order_id, "
                "  transitioned_at, metadata, error_text "
                "FROM algo.budget_reservations "
                "ORDER BY reservation_id, "
                "         transitioned_at DESC"
            ),
        )
        rows = result.mappings().all()
    out: list[BudgetReservation] = []
    for row in rows:
        d = dict(row)
        d["state"] = ReservationState(d["state"])
        if d["state"] in (
            ReservationState.SUBMITTED,
            ReservationState.PARTIAL,
        ):
            out.append(BudgetReservation(**d))
    return out


async def _fetch_kite_order_status(
    user_id: UUID, kite_order_id: str,
) -> dict[str, Any] | None:
    """Pull the latest leg of a Kite order's history. None on
    error or when creds are missing/expired."""
    try:
        kc = await _build_kite_for_user(user_id)
        history = await asyncio.to_thread(
            kc._kc.order_history, kite_order_id,
        )
        if not history:
            return None
        return history[-1]
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "kite order_history failed user=%s order=%s: %s",
            user_id, kite_order_id, exc, exc_info=True,
        )
        return None


async def reconcile_pending_timeouts() -> None:
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=PENDING_TIMEOUT_S)

    pending = await _list_pending()
    for res in pending:
        if res.transitioned_at < threshold:
            await transition(
                reservation_id=res.reservation_id,
                new_state=ReservationState.TIMEOUT,
                error_text=(
                    f"PENDING timeout > "
                    f"{PENDING_TIMEOUT_S}s"
                ),
            )


async def reconcile_one(res: BudgetReservation) -> None:
    """Reconcile a single SUBMITTED/PARTIAL reservation."""
    if res.kite_order_id is None:
        return

    now = datetime.now(timezone.utc)
    threshold = now - timedelta(
        seconds=SUBMITTED_HARD_TIMEOUT_S,
    )

    status_row = await _fetch_kite_order_status(
        res.user_id, res.kite_order_id,
    )
    if status_row is None:
        if res.transitioned_at < threshold:
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

    kite_status = (
        str(status_row.get("status", "")).upper()
    )
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
                new_state=(
                    ReservationState.PARTIAL_CANCELLED
                ),
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
                status_row.get("status_message", "")
                or "rejected",
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


async def reconcile_submitted() -> None:
    for res in await _list_submitted_and_partial():
        try:
            await reconcile_one(res)
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "budget reconcile_one failed res=%s: %s",
                res.reservation_id, exc, exc_info=True,
            )


async def reconcile() -> None:
    """Entrypoint called by the scheduler tick."""
    await reconcile_pending_timeouts()
    await reconcile_submitted()
