"""Async CRUD for algo.risk_state — intra-day rolling P&L per
(user_id, day_date). Uses stub-friendly session pattern (mirrors
backtest/runs_repo.py)."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)


class RiskStateRepo:
    async def get_or_create(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        day_date: date,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "SELECT user_id, day_date, "
                "daily_realised_pnl_inr, daily_unrealised_pnl_inr, "
                "breaches "
                "FROM algo.risk_state "
                "WHERE user_id = :uid AND day_date = :dd"
            ),
            {"uid": user_id, "dd": day_date},
        )
        row = result.mappings().first()
        if row is not None:
            return dict(row)

        await session.execute(
            text(
                "INSERT INTO algo.risk_state ("
                "  user_id, day_date, daily_realised_pnl_inr, "
                "  daily_unrealised_pnl_inr, breaches) "
                "VALUES (:uid, :dd, 0, 0, '[]'::jsonb)"
            ),
            {"uid": user_id, "dd": day_date},
        )
        return {
            "user_id": user_id, "day_date": day_date,
            "daily_realised_pnl_inr": Decimal("0"),
            "daily_unrealised_pnl_inr": Decimal("0"),
            "breaches": [],
        }

    async def update_pnl(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        day_date: date,
        realised_delta: Decimal,
        unrealised_inr: Decimal,
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.risk_state SET "
                "  daily_realised_pnl_inr = "
                "    daily_realised_pnl_inr + :rd, "
                "  daily_unrealised_pnl_inr = :ud, "
                "  updated_at = :ua "
                "WHERE user_id = :uid AND day_date = :dd"
            ),
            {
                "uid": user_id, "dd": day_date,
                "rd": realised_delta,
                "ud": unrealised_inr,
                "ua": datetime.now(timezone.utc),
            },
        )

    async def append_breach(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        day_date: date,
        breach: dict[str, Any],
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.risk_state SET "
                "  breaches = breaches || CAST(:b AS jsonb), "
                "  updated_at = :ua "
                "WHERE user_id = :uid AND day_date = :dd"
            ),
            {
                "uid": user_id, "dd": day_date,
                "b": json.dumps([breach]),
                "ua": datetime.now(timezone.utc),
            },
        )

    async def reset_for_day(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        day_date: date,
    ) -> None:
        """Used by the IST-midnight scheduler + restart-replay.

        Idempotent: an INSERT-on-conflict-update so the row exists
        even if the user has never traded today.
        """
        await session.execute(
            text(
                "INSERT INTO algo.risk_state ("
                "  user_id, day_date, daily_realised_pnl_inr, "
                "  daily_unrealised_pnl_inr, breaches) "
                "VALUES (:uid, :dd, 0, 0, '[]'::jsonb) "
                "ON CONFLICT (user_id, day_date) DO UPDATE SET "
                "  daily_realised_pnl_inr = 0, "
                "  daily_unrealised_pnl_inr = 0, "
                "  breaches = '[]'::jsonb, "
                "  updated_at = :ua"
            ),
            {
                "uid": user_id, "dd": day_date,
                "ua": datetime.now(timezone.utc),
            },
        )
