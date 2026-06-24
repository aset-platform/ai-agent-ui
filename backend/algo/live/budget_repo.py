"""Async PG repository for algo.user_budget +
algo.budget_reservations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

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

    async def reserve_if_headroom(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        strategy_id: UUID,
        ticker: str,
        side: str,
        qty: int,
        reserved_inr: Decimal,
        allocated_inr: Decimal,
        metadata: dict[str, Any] | None = None,
    ) -> UUID | None:
        """Atomic, uncached, headroom-aware PENDING reservation.

        Closes the TOCTOU over-deploy gap (Critical C4): the
        budget CHECK and the RESERVE happen in ONE transaction so
        two concurrent BUYs can no longer both pass and over-
        deploy the user's allocated pool.

        Steps (single txn on ``session`` — caller commits):
          1. ``SELECT ... FOR UPDATE`` the user's
             ``algo.user_budget`` row to serialize concurrent
             reservers per-user. The lock — not the row's value —
             is what matters; ``allocated_inr`` is supplied by the
             caller so the gate works even before the row exists.
          2. Recompute ``headroom = allocated_inr − Σopen_cost −
             Σactive_reservations`` UNCACHED inside this txn,
             reusing ``sum_open_position_cost`` /
             ``sum_active_reservations`` executed on THIS session
             (NOT the 5s cache in ``budget.py``).
          3. Insert the PENDING row only if
             ``headroom >= reserved_inr``; else return ``None``.

        Returns the new ``reservation_id`` on success, else
        ``None``. Does NOT commit — the caller owns the txn
        boundary so the lock is held until commit/rollback.
        """
        # 1. Acquire the per-user serialization lock.
        #
        # The ``SELECT ... FOR UPDATE`` below is a NO-OP when the
        # user has no ``algo.user_budget`` row yet (it locks zero
        # rows), which leaves the no-row concurrency hole open:
        # two concurrent reservers both pass and over-deploy
        # (Critical C4). The transaction-scoped advisory lock keyed
        # on the user serializes reservers regardless of row
        # existence and auto-releases at commit/rollback.
        # ``hashtext`` returns int4 which auto-casts to the bigint
        # single-arg ``pg_advisory_xact_lock``.
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext(:k))"
            ),
            {"k": str(user_id)},
        )
        # Belt-and-suspenders: also take the row lock when the row
        # exists so a concurrent ``upsert_user_budget`` serializes
        # against in-flight reservations too.
        await session.execute(
            text(
                "SELECT user_id FROM algo.user_budget "
                "WHERE user_id = :uid FOR UPDATE"
            ),
            {"uid": user_id},
        )

        # 2. Recompute headroom UNCACHED, in this same txn — so we
        # observe any committed reservation from a serialized peer.
        open_cost = await self.sum_open_position_cost(
            session,
            user_id=user_id,
        )
        active = await self.sum_active_reservations(
            session,
            user_id=user_id,
        )
        headroom = allocated_inr - open_cost - active

        # 3. Conditional insert.
        if headroom < reserved_inr:
            _logger.info(
                "reserve_if_headroom REJECT user=%s ticker=%s "
                "reserved=%s headroom=%s (alloc=%s open=%s "
                "active=%s)",
                user_id,
                ticker,
                reserved_inr,
                headroom,
                allocated_inr,
                open_cost,
                active,
            )
            return None

        reservation_id = uuid4()
        row = BudgetReservation(
            reservation_id=reservation_id,
            user_id=user_id,
            strategy_id=strategy_id,
            state=ReservationState.PENDING,
            ticker=ticker,
            side=side,
            qty=qty,
            reserved_inr=reserved_inr,
            transitioned_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )
        await self.insert_reservation_event(session, row)
        return reservation_id

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
                "ORDER BY transitioned_at DESC, id DESC "
                "LIMIT 1"
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
        """Sum of remaining unfilled budget across active BUY
        reservations (current state ∈ ACTIVE_STATES).

        Each row contributes ``GREATEST(reserved_inr −
        filled_inr, 0)`` so that a partial fill where slippage
        caused ``filled_inr`` to slightly exceed ``reserved_inr``
        does not produce a negative contribution that shrinks the
        total below the other rows' rightful share.

        SELL reservations are written for audit history only and
        must NOT deduct from BUY headroom in safety.py — they free
        capital rather than consume it.

        Only real-money LIVE reservations count
        (``COALESCE(metadata->>'mode', 'live') = 'live'``).
        Paper- AND dry-run-mode reservations are excluded: they
        exist for UX visibility on the BudgetPanel (badged
        "PAPER" / "DRYRUN") but must NOT deduct from real-money
        Cap 0 headroom — a rehearsal must not consume the user's
        allocated budget.
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
                "           transitioned_at DESC, id DESC "
                ") "
                "SELECT COALESCE(SUM("
                "  GREATEST(reserved_inr - filled_inr, 0)"
                "), 0) AS total "
                f"FROM latest WHERE state IN ({active}) "
                "AND side = 'BUY' "
                "AND COALESCE(metadata->>'mode', 'live') "
                "    = 'live'"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None or row["total"] is None:
            return Decimal("0")
        return Decimal(row["total"])

    async def sum_active_reservations_for_strategy(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        strategy_id: UUID,
    ) -> Decimal:
        """Sum reserved_inr - filled_inr across active BUY
        reservations for ONE strategy (latest state per
        reservation_id ∈ ACTIVE_STATES, side='BUY', live mode).

        Drives the strategy max_inr cap: two BUYs in one tick must
        size against deployed = filled positions + in-flight
        reservations, not just filled positions (Critical C4).
        Same live-only / BUY-only filters as
        ``sum_active_reservations``; additionally scoped to the
        strategy so one strategy's in-flight orders don't shrink
        another's headroom.
        """
        active = ",".join(f"'{s.value}'" for s in ACTIVE_STATES)
        result = await session.execute(
            text(
                "WITH latest AS ( "
                "  SELECT DISTINCT ON (reservation_id) "
                "    reservation_id, state, side, strategy_id, "
                "    reserved_inr, filled_inr, metadata "
                "  FROM algo.budget_reservations "
                "  WHERE user_id = :uid "
                "    AND strategy_id = :sid "
                "  ORDER BY reservation_id, "
                "           transitioned_at DESC, id DESC "
                ") "
                "SELECT COALESCE(SUM("
                "  reserved_inr - filled_inr), 0) AS total "
                f"FROM latest WHERE state IN ({active}) "
                "AND side = 'BUY' "
                "AND COALESCE(metadata->>'mode', 'live') "
                "    = 'live'"
            ),
            {"uid": user_id, "sid": strategy_id},
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
        """Per-ticker cost basis of open positions from the FILLED
        reservation ledger, summed across tickers.

        Algorithm (per ticker):
          avg_buy_price = Σ filled_inr (FILLED BUY)
                        / Σ filled_qty (FILLED BUY)
          open_qty      = Σ filled_qty (FILLED BUY)
                        − Σ filled_qty (FILLED SELL)
          ticker_cost   = GREATEST(open_qty, 0) × avg_buy_price

        Summing ``ticker_cost`` across all tickers prevents a
        profitable SELL on ticker B from reducing the apparent cost
        basis of an unrelated open BUY position on ticker A (the
        "global SELL netting" bug). Each ticker is floored
        independently at 0 via ``GREATEST``; there is no outer
        floor that could mask a net-negative.

        Divide-by-zero guard: a ticker with Σbuy_qty = 0 (only
        SELL rows in ledger — edge case) is excluded by the
        ``HAVING SUM(buy_qty) > 0`` clause so avg_buy_price is
        never computed over a zero denominator.

        Falls back to ``reserved_inr`` when ``filled_inr`` was not
        populated (COALESCE/NULLIF pattern preserved per ticker).
        Counts only LIVE rows (paper + dry-run excluded).
        """
        result = await session.execute(
            text(
                "WITH latest AS ( "
                "  SELECT DISTINCT ON (reservation_id) "
                "    reservation_id, state, side, ticker, "
                "    reserved_inr, filled_qty, filled_inr, "
                "    metadata "
                "  FROM algo.budget_reservations "
                "  WHERE user_id = :uid "
                "  ORDER BY reservation_id, "
                "           transitioned_at DESC, id DESC "
                "), "
                "live_filled AS ( "
                "  SELECT ticker, side, filled_qty, "
                "    COALESCE(NULLIF(filled_inr, 0), "
                "             reserved_inr) AS eff_inr "
                "  FROM latest "
                "  WHERE state = 'FILLED' "
                "  AND COALESCE(metadata->>'mode', 'live') "
                "      = 'live' "
                "), "
                "per_ticker AS ( "
                "  SELECT ticker, "
                "    SUM(CASE WHEN side = 'BUY' "
                "        THEN filled_qty ELSE 0 END) AS buy_qty, "
                "    SUM(CASE WHEN side = 'SELL' "
                "        THEN filled_qty ELSE 0 END) AS sell_qty, "
                "    SUM(CASE WHEN side = 'BUY' "
                "        THEN eff_inr ELSE 0 END) AS buy_inr "
                "  FROM live_filled "
                "  GROUP BY ticker "
                "  HAVING SUM(CASE WHEN side = 'BUY' "
                "             THEN filled_qty ELSE 0 END) > 0 "
                ") "
                "SELECT COALESCE(SUM( "
                "  GREATEST(buy_qty - sell_qty, 0) "
                "  * (buy_inr / buy_qty) "
                "), 0) AS total "
                "FROM per_ticker"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None or row["total"] is None:
            return Decimal("0")
        return Decimal(row["total"])

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
                "         transitioned_at DESC, id DESC"
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
