"""Restart-replay rebuilder.

On backend restart, scratch state in PaperRuntime instances is
lost. Per spec § 5.3, intra-day risk_state must be rebuilt from
the canonical algo.events log instead of trusting any in-memory
counters.

This module reads today's order_filled events for a user from
algo.events (via DuckDB), replays them through a PositionTracker
to recompute realised P&L, and writes the result back into
algo.risk_state for that user.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill
from backend.algo.paper.risk_state_repo import RiskStateRepo

_logger = logging.getLogger(__name__)


def _ist_today() -> date:
    now_ist = datetime.now(timezone.utc) + timedelta(
        hours=5, minutes=30,
    )
    return now_ist.date()


def _load_paper_fills_today(user_id: UUID) -> list[Fill]:
    """Read today's mode='paper' order_filled events for the user
    via DuckDB over algo.events. Returns hydrated Fill instances.
    """
    from backend.db.duckdb_engine import query_iceberg_table

    sql = (
        "SELECT payload_json FROM events "
        "WHERE user_id = ? "
        "  AND mode = 'paper' "
        "  AND type = 'order_filled' "
        "  AND ts_date = ? "
        "ORDER BY ts_ns"
    )
    rows = query_iceberg_table(
        "algo.events", sql,
        [str(user_id), _ist_today().isoformat()],
    )
    fills: list[Fill] = []
    for r in rows:
        payload = json.loads(r["payload_json"])
        fills.append(Fill(
            intent_id=uuid4(),
            ticker=payload["ticker"],
            side=payload["side"],
            qty=int(payload["qty"]),
            fill_price=Decimal(str(payload["fill_price"])),
            fill_date=date.fromisoformat(payload["fill_date"]),
            fees_inr=Decimal(str(payload["fees_inr"])),
            fee_rates_version=payload["fee_rates_version"],
        ))
    return fills


async def rebuild_risk_state_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> dict[str, Any]:
    """Replay today's paper fills through a PositionTracker, then
    persist realised + (zero) unrealised into algo.risk_state."""
    fills = _load_paper_fills_today(user_id)
    pt = PositionTracker()
    for fill in fills:
        pt.apply_fill(fill)
    realised = pt.total_realised_pnl_inr()

    repo = RiskStateRepo()
    today = _ist_today()
    await repo.reset_for_day(
        session, user_id=user_id, day_date=today,
    )
    await repo.update_pnl(
        session,
        user_id=user_id,
        day_date=today,
        realised_delta=realised,
        unrealised_inr=Decimal("0"),
    )
    _logger.info(
        "rebuilt risk_state for %s: %d fills, realised=%s",
        user_id, len(fills), realised,
    )
    return {
        "user_id": str(user_id),
        "fills_replayed": len(fills),
        "realised_pnl_inr": str(realised),
        "day_date": today.isoformat(),
    }
