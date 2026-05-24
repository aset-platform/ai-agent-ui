"""POST/GET routes for /v1/algo/budget/*.

Lift-to-module-level pattern: handlers delegate to pure
``_impl`` functions so unit tests can exercise the handler
without an HTTP harness.

Endpoints (all gated by pro_or_superuser):
  GET  /v1/algo/budget                  -- current headroom
  PUT  /v1/algo/budget/allocation       -- set allocated_inr
  GET  /v1/algo/budget/reservations     -- active or history
  POST /v1/algo/budget/reservations/
       {reservation_id}/force-release   -- owner force-cancel
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.live.budget import (
    _invalidate_cache,
    _session_factory,
    fetch_kite_available_cash,
    load_user_budget,
    sum_active_reservations,
    sum_open_position_cost,
    transition,
)
from backend.algo.live.budget_repo import BudgetRepo
from backend.algo.live.budget_types import ReservationState

_logger = logging.getLogger(__name__)


async def _get_budget_impl(*, user_id: UUID) -> dict:
    user_budget = await load_user_budget(user_id)
    open_cost = await sum_open_position_cost(user_id)
    active_res = await sum_active_reservations(user_id)
    kite = await fetch_kite_available_cash(user_id)

    internal_headroom = user_budget.allocated_inr - open_cost - active_res
    available = min(internal_headroom, kite)
    return {
        "user_id": str(user_id),
        "allocated_inr": str(user_budget.allocated_inr),
        "enabled": user_budget.enabled,
        "open_pos_cost": str(open_cost),
        "active_reserved": str(active_res),
        "internal_headroom": str(internal_headroom),
        "kite_available": (str(kite) if kite != Decimal("inf") else None),
        "available": str(available),
    }


async def _put_allocation_impl(
    *,
    user_id: UUID,
    new_allocation: Decimal,
) -> dict:
    if new_allocation < Decimal("0"):
        raise HTTPException(
            status_code=400,
            detail="allocated_inr must be >= 0",
        )

    open_cost = await sum_open_position_cost(user_id)
    active_res = await sum_active_reservations(user_id)
    committed = open_cost + active_res
    warning = None
    if new_allocation < committed:
        warning = (
            f"New allocation INR {new_allocation} is below "
            f"committed INR {committed} (open positions + "
            "active reservations). No new orders will fire "
            "until open positions close."
        )

    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        await repo.upsert_user_budget(
            session,
            user_id=user_id,
            allocated_inr=new_allocation,
            enabled=(new_allocation > 0),
            updated_by=user_id,
        )
        await session.commit()
    _invalidate_cache(user_id)

    out: dict = {
        "user_id": str(user_id),
        "allocated_inr": str(new_allocation),
        "enabled": new_allocation > 0,
    }
    if warning is not None:
        out["warning"] = warning
    return out


async def _list_reservations_impl(
    *,
    user_id: UUID,
    include_history: bool = False,
) -> dict:
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        if include_history:
            from sqlalchemy import text

            result = await session.execute(
                text(
                    "SELECT reservation_id, user_id, "
                    "       strategy_id, state, ticker, "
                    "       side, qty, reserved_inr, "
                    "       filled_qty, filled_inr, "
                    "       kite_order_id, "
                    "       transitioned_at, metadata, "
                    "       error_text "
                    "FROM algo.budget_reservations "
                    "WHERE user_id = :uid "
                    "ORDER BY transitioned_at DESC "
                    "LIMIT 500"
                ),
                {"uid": user_id},
            )
            rows = result.mappings().all()
            return {
                "reservations": [
                    {k: str(v) for k, v in dict(r).items()} for r in rows
                ],
            }
        else:
            active = await repo.list_active_reservations(
                session,
                user_id=user_id,
            )
    return {
        "reservations": [
            {
                "reservation_id": str(r.reservation_id),
                "strategy_id": str(r.strategy_id),
                "state": r.state.value,
                "ticker": r.ticker,
                "side": r.side,
                "qty": r.qty,
                "reserved_inr": str(r.reserved_inr),
                "filled_qty": r.filled_qty,
                "filled_inr": str(r.filled_inr),
                "kite_order_id": r.kite_order_id,
                "transitioned_at": (r.transitioned_at.isoformat()),
            }
            for r in active
        ],
    }


async def _force_release_impl(
    *,
    user_id: UUID,
    reservation_id: UUID,
) -> dict:
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        current = await repo.get_current_state(
            session,
            reservation_id=reservation_id,
        )
    if current is None:
        raise HTTPException(
            status_code=404,
            detail="Reservation not found",
        )
    if current.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Not owner of reservation",
        )
    await transition(
        reservation_id=reservation_id,
        new_state=ReservationState.CANCELLED,
        error_text="force-released by user",
    )
    return {"status": "released"}


def create_budget_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/budget",
        tags=["algo-trading"],
    )

    @router.get("")
    async def get_budget(
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _get_budget_impl(
            user_id=UUID(user.user_id),
        )

    @router.put("/allocation")
    async def put_allocation(
        body: dict = Body(...),
        user: UserContext = Depends(pro_or_superuser),
    ):
        try:
            new_alloc = Decimal(
                str(body.get("allocated_inr", "0")),
            )
        except (InvalidOperation, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid allocated_inr: {exc}",
            ) from exc
        return await _put_allocation_impl(
            user_id=UUID(user.user_id),
            new_allocation=new_alloc,
        )

    @router.get("/reservations")
    async def list_reservations(
        include_history: bool = False,
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _list_reservations_impl(
            user_id=UUID(user.user_id),
            include_history=include_history,
        )

    @router.post(
        "/reservations/{reservation_id}/force-release",
    )
    async def force_release(
        reservation_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _force_release_impl(
            user_id=UUID(user.user_id),
            reservation_id=reservation_id,
        )

    return router
