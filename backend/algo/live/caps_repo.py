"""Repository for ``algo.live_caps`` — live safety-belt config.

One row per (user_id, strategy_id).  Default state is:
  live_orders_enabled=false, max_inr=0, max_orders_per_day=0,
  allowed_tickers=[]

NEVER enable live trading from this repo directly —
that requires an explicit API call through the live routes
with 4-gate validation on the frontend.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.db.engine import get_session_factory

_logger = logging.getLogger(__name__)

UTC = timezone.utc


class CapsRepo:
    """Async PG repository for ``algo.live_caps``."""

    # ----------------------------------------------------------
    # Read
    # ----------------------------------------------------------

    async def get(
        self, user_id: UUID, strategy_id: UUID,
    ) -> dict[str, Any] | None:
        """Fetch the caps row for a (user, strategy) pair.

        Returns ``None`` if no row exists (= never opted in).
        """
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT user_id, strategy_id, "
                        "  max_inr, max_orders_per_day, "
                        "  allowed_tickers, live_orders_enabled, "
                        "  approved_by, approved_at, "
                        "  last_walkforward_run_id, "
                        "  cumulative_inr_today, "
                        "  orders_count_today, "
                        "  created_at, updated_at "
                        "FROM algo.live_caps "
                        "WHERE user_id = :uid "
                        "  AND strategy_id = :sid"
                    ),
                    {"uid": user_id, "sid": strategy_id},
                )
            ).mappings().one_or_none()
        if row is None:
            return None
        result = dict(row)
        # Parse JSONB list back to Python list
        tickers = result.get("allowed_tickers")
        if isinstance(tickers, str):
            result["allowed_tickers"] = json.loads(tickers)
        return result

    async def get_or_default(
        self, user_id: UUID, strategy_id: UUID,
    ) -> dict[str, Any]:
        """Return caps or a safe default (all zeros, disabled)."""
        row = await self.get(user_id, strategy_id)
        if row is not None:
            return row
        return {
            "user_id": user_id,
            "strategy_id": strategy_id,
            "max_inr": Decimal("0"),
            "max_orders_per_day": 0,
            "allowed_tickers": [],
            "live_orders_enabled": False,
            "approved_by": None,
            "approved_at": None,
            "last_walkforward_run_id": None,
            "cumulative_inr_today": Decimal("0"),
            "orders_count_today": 0,
        }

    async def list_enabled(
        self, user_id: UUID,
    ) -> list[dict[str, Any]]:
        """All strategies with live_orders_enabled=True for a user."""
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT user_id, strategy_id, "
                        "  max_inr, max_orders_per_day, "
                        "  allowed_tickers, live_orders_enabled, "
                        "  cumulative_inr_today, "
                        "  orders_count_today "
                        "FROM algo.live_caps "
                        "WHERE user_id = :uid "
                        "  AND live_orders_enabled = true"
                    ),
                    {"uid": user_id},
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------
    # Write — upsert / update helpers
    # ----------------------------------------------------------

    async def upsert(
        self,
        user_id: UUID,
        strategy_id: UUID,
        *,
        max_inr: Decimal,
        max_orders_per_day: int,
        allowed_tickers: list[str],
        last_walkforward_run_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create or update the caps row (does NOT change
        live_orders_enabled — that requires a separate call).

        Returns the updated row.
        """
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO algo.live_caps ("
                    "  user_id, strategy_id, max_inr, "
                    "  max_orders_per_day, allowed_tickers, "
                    "  live_orders_enabled, "
                    "  last_walkforward_run_id, "
                    "  cumulative_inr_today, orders_count_today, "
                    "  created_at, updated_at) "
                    "VALUES ("
                    "  :uid, :sid, :max_inr, :max_ord, "
                    "  :tickers::jsonb, false, "
                    "  :wf_run_id, 0, 0, :now, :now) "
                    "ON CONFLICT (user_id, strategy_id) "
                    "DO UPDATE SET "
                    "  max_inr = :max_inr, "
                    "  max_orders_per_day = :max_ord, "
                    "  allowed_tickers = :tickers::jsonb, "
                    "  last_walkforward_run_id = :wf_run_id, "
                    "  updated_at = :now"
                ),
                {
                    "uid": user_id,
                    "sid": strategy_id,
                    "max_inr": max_inr,
                    "max_ord": max_orders_per_day,
                    "tickers": json.dumps(allowed_tickers),
                    "wf_run_id": last_walkforward_run_id,
                    "now": now,
                },
            )
            await session.commit()
        row = await self.get(user_id, strategy_id)
        assert row is not None
        return row

    async def enable_live_orders(
        self,
        user_id: UUID,
        strategy_id: UUID,
        *,
        approved_by: UUID,
    ) -> None:
        """Flip live_orders_enabled=True.

        MUST only be called after all 4 frontend gates pass.
        """
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE algo.live_caps "
                    "SET live_orders_enabled = true, "
                    "    approved_by = :approver, "
                    "    approved_at = :now, "
                    "    updated_at  = :now "
                    "WHERE user_id = :uid "
                    "  AND strategy_id = :sid"
                ),
                {
                    "uid": user_id,
                    "sid": strategy_id,
                    "approver": approved_by,
                    "now": now,
                },
            )
            await session.commit()
        _logger.warning(
            "caps_repo: live_orders ENABLED for "
            "user=%s strategy=%s approved_by=%s",
            user_id, strategy_id, approved_by,
        )

    async def disable_live_orders(
        self,
        user_id: UUID,
        strategy_id: UUID,
    ) -> None:
        """Flip live_orders_enabled=False."""
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE algo.live_caps "
                    "SET live_orders_enabled = false, "
                    "    updated_at = :now "
                    "WHERE user_id = :uid "
                    "  AND strategy_id = :sid"
                ),
                {
                    "uid": user_id,
                    "sid": strategy_id,
                    "now": now,
                },
            )
            await session.commit()
        _logger.warning(
            "caps_repo: live_orders DISABLED for "
            "user=%s strategy=%s",
            user_id, strategy_id,
        )

    async def increment_daily_counters(
        self,
        user_id: UUID,
        strategy_id: UUID,
        *,
        inr_amount: Decimal,
    ) -> None:
        """Atomically bump cumulative_inr_today + orders_count_today."""
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE algo.live_caps "
                    "SET cumulative_inr_today = "
                    "      cumulative_inr_today + :inr, "
                    "    orders_count_today = "
                    "      orders_count_today + 1, "
                    "    updated_at = :now "
                    "WHERE user_id = :uid "
                    "  AND strategy_id = :sid"
                ),
                {
                    "uid": user_id,
                    "sid": strategy_id,
                    "inr": inr_amount,
                    "now": now,
                },
            )
            await session.commit()

    async def reset_daily_counters(
        self,
        user_id: UUID | None = None,
    ) -> int:
        """Reset cumulative_inr_today + orders_count_today to 0.

        If ``user_id`` is None, resets ALL rows (called at market
        open by the scheduler job).  Returns rows updated.
        """
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            if user_id is None:
                result = await session.execute(
                    text(
                        "UPDATE algo.live_caps "
                        "SET cumulative_inr_today = 0, "
                        "    orders_count_today = 0, "
                        "    updated_at = :now"
                    ),
                    {"now": now},
                )
            else:
                result = await session.execute(
                    text(
                        "UPDATE algo.live_caps "
                        "SET cumulative_inr_today = 0, "
                        "    orders_count_today = 0, "
                        "    updated_at = :now "
                        "WHERE user_id = :uid"
                    ),
                    {"uid": user_id, "now": now},
                )
            await session.commit()
        return result.rowcount

    async def update_in_flight(
        self,
        user_id: UUID,
        run_id: UUID,
        in_flight: list[dict],
    ) -> None:
        """Overwrite ``algo.runs.live_orders_in_flight`` for a run."""
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE algo.runs "
                    "SET live_orders_in_flight = :payload::jsonb "
                    "WHERE id = :rid "
                    "  AND user_id = :uid"
                ),
                {
                    "payload": json.dumps(in_flight, default=str),
                    "rid": run_id,
                    "uid": user_id,
                },
            )
            await session.commit()

    async def get_in_flight(
        self, user_id: UUID, run_id: UUID,
    ) -> list[dict]:
        """Return the in-flight orders list for a run."""
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT live_orders_in_flight "
                        "FROM algo.runs "
                        "WHERE id = :rid "
                        "  AND user_id = :uid"
                    ),
                    {"rid": run_id, "uid": user_id},
                )
            ).one_or_none()
        if row is None:
            return []
        raw = row[0]
        if isinstance(raw, str):
            return json.loads(raw)
        if isinstance(raw, list):
            return raw
        return []
