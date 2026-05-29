"""Async PG repository for algo.user_budget +
algo.budget_reservations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.live.budget_types import (
    ACTIVE_STATES,
    BudgetReservation,
    ReservationState,
    UserBudget,
)

_logger = logging.getLogger(__name__)


class BudgetRepo:
    """CRUD + event-log queries for budget tables."""

    async def get_user_budget(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> UserBudget:
        """Return the user's budget row; default-zero
        when no row exists yet."""
        result = await session.execute(
            text(
                "SELECT user_id, allocated_inr, enabled, "
                "       updated_at, updated_by "
                "FROM algo.user_budget WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None:
            return UserBudget(user_id=user_id)
        return UserBudget(**dict(row))

    async def upsert_user_budget(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        allocated_inr: Decimal,
        enabled: bool,
        updated_by: UUID | None = None,
    ) -> None:
        """Insert or update the user_budget row."""
        await session.execute(
            text(
                "INSERT INTO algo.user_budget ("
                "  user_id, allocated_inr, enabled, "
                "  updated_at, updated_by"
                ") VALUES ("
                "  :uid, :alloc, :en, :now, :by"
                ") ON CONFLICT (user_id) DO UPDATE SET "
                "  allocated_inr = EXCLUDED.allocated_inr, "
                "  enabled = EXCLUDED.enabled, "
                "  updated_at = EXCLUDED.updated_at, "
                "  updated_by = EXCLUDED.updated_by"
            ),
            {
                "uid": user_id,
                "alloc": allocated_inr,
                "en": enabled,
                "now": datetime.now(timezone.utc),
                "by": updated_by,
            },
        )

    async def insert_reservation_event(
        self,
        session: AsyncSession,
        res: BudgetReservation,
    ) -> None:
        """Append one row to algo.budget_reservations."""
        await session.execute(
            text(
                "INSERT INTO algo.budget_reservations ("
                "  reservation_id, user_id, strategy_id, "
                "  state, ticker, side, qty, "
                "  reserved_inr, filled_qty, filled_inr, "
                "  kite_order_id, transitioned_at, "
                "  metadata, error_text"
                ") VALUES ("
                "  :rid, :uid, :sid, :st, :tk, :sd, :q, "
                "  :ri, :fq, :fi, :koi, :ta, "
                "  CAST(:md AS jsonb), :et"
                ")"
            ),
            {
                "rid": res.reservation_id,
                "uid": res.user_id,
                "sid": res.strategy_id,
                "st": res.state.value,
                "tk": res.ticker,
                "sd": res.side,
                "q": res.qty,
                "ri": res.reserved_inr,
                "fq": res.filled_qty,
                "fi": res.filled_inr,
                "koi": res.kite_order_id,
                "ta": res.transitioned_at,
                "md": json.dumps(res.metadata),
                "et": res.error_text,
            },
        )

    async def get_current_state(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
    ) -> BudgetReservation | None:
        """Latest event row for this reservation_id."""
        result = await session.execute(
            text(
                "SELECT reservation_id, user_id, "
                "       strategy_id, state, ticker, side, "
                "       qty, reserved_inr, filled_qty, "
                "       filled_inr, kite_order_id, "
                "       transitioned_at, metadata, "
                "       error_text "
                "FROM algo.budget_reservations "
                "WHERE reservation_id = :rid "
                "ORDER BY transitioned_at DESC LIMIT 1"
            ),
            {"rid": reservation_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        d = dict(row)
        d["state"] = ReservationState(d["state"])
        return BudgetReservation(**d)

    async def sum_active_reservations(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> Decimal:
        """Sum reserved_inr - filled_inr across reservations
        whose CURRENT state ∈ ACTIVE_STATES and side = 'BUY'.

        SELL reservations are written for audit history only and
        must NOT deduct from BUY headroom in safety.py — they free
        capital rather than consume it.

        Paper-mode reservations (``metadata->>'mode' = 'paper'``)
        are likewise excluded: they exist for UX visibility on
        the BudgetPanel (badged "PAPER") but must NOT deduct
        from real-money Cap 0 headroom.
        """
        active = ",".join(f"'{s.value}'" for s in ACTIVE_STATES)
        result = await session.execute(
            text(
                "WITH latest AS ( "
                "  SELECT DISTINCT ON (reservation_id) "
                "    reservation_id, state, side, "
                "    reserved_inr, filled_inr, metadata "
                "  FROM algo.budget_reservations "
                "  WHERE user_id = :uid "
                "  ORDER BY reservation_id, "
                "           transitioned_at DESC "
                ") "
                "SELECT COALESCE(SUM("
                "  reserved_inr - filled_inr), 0) AS total "
                f"FROM latest WHERE state IN ({active}) "
                "AND side = 'BUY' "
                "AND COALESCE(metadata->>'mode', 'live') "
                "    <> 'paper'"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None or row["total"] is None:
            return Decimal("0")
        return Decimal(row["total"])

    async def sum_open_position_cost(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> Decimal:
        """Net cost basis of open positions from the FILLED
        reservation ledger:

            Σ(FILLED BUY filled_inr) − Σ(FILLED SELL filled_inr)

        floored at 0. Excludes paper-mode rows. This is an
        approximation used for Cap 0 headroom (allocated −
        open_pos_cost − active_reserved); it nets sell proceeds
        against cost basis, which is good enough to keep a filled
        position consuming budget until it is closed. Falls back to
        ``reserved_inr`` when ``filled_inr`` was not populated.
        """
        result = await session.execute(
            text(
                "WITH latest AS ( "
                "  SELECT DISTINCT ON (reservation_id) "
                "    reservation_id, state, side, "
                "    reserved_inr, filled_inr, metadata "
                "  FROM algo.budget_reservations "
                "  WHERE user_id = :uid "
                "  ORDER BY reservation_id, "
                "           transitioned_at DESC "
                ") "
                "SELECT COALESCE(SUM(CASE WHEN side = 'BUY' "
                "  THEN COALESCE(NULLIF(filled_inr, 0), "
                "               reserved_inr) "
                "  ELSE -COALESCE(NULLIF(filled_inr, 0), "
                "                reserved_inr) END), 0) AS total "
                "FROM latest WHERE state = 'FILLED' "
                "AND COALESCE(metadata->>'mode', 'live') "
                "    <> 'paper'"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None or row["total"] is None:
            return Decimal("0")
        total = Decimal(row["total"])
        return total if total > Decimal("0") else Decimal("0")

    async def list_active_reservations(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> list[BudgetReservation]:
        """All active reservation rows (latest state per
        reservation_id, filtered to ACTIVE_STATES)."""
        # NOTE: Active-state filter applied Python-side at the
        # bottom of this method (see ACTIVE_STATES check below);
        # SQL returns the latest event per reservation_id and we
        # discard non-active states after model construction.
        result = await session.execute(
            text(
                "SELECT DISTINCT ON (reservation_id) "
                "  reservation_id, user_id, strategy_id, "
                "  state, ticker, side, qty, "
                "  reserved_inr, filled_qty, filled_inr, "
                "  kite_order_id, transitioned_at, "
                "  metadata, error_text "
                "FROM algo.budget_reservations "
                "WHERE user_id = :uid "
                "ORDER BY reservation_id, "
                "         transitioned_at DESC"
            ),
            {"uid": user_id},
        )
        out: list[BudgetReservation] = []
        for row in result.mappings().all():
            d = dict(row)
            d["state"] = ReservationState(d["state"])
            if d["state"] not in ACTIVE_STATES:
                continue
            out.append(BudgetReservation(**d))
        return out
