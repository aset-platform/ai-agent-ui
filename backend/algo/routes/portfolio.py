"""Algo Portfolio dashboard tab — GET
/v1/algo/portfolio/positions.

Returns currently-open algo-attributed positions (intraday
MIS from kc.positions().net + overnight CNC from
kc.holdings()), joined with strategy attribution and
augmented with days_held + t1_pending flags.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

_logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# Lookback floor for joining algo.events attribution.
# 2024-01-01 covers any CNC overnight position opened
# since the v1 algo trading launch (~17 months); plenty
# of margin to attribute the oldest still-held position
# without scanning the entire algo.events Iceberg table.
_ATTRIBUTION_SINCE = "2024-01-01"

# Redis cache TTL (matches the existing
# /v1/algo/live/positions endpoint behavior — TTL-only, no
# write-through invalidation in v1).
_CACHE_TTL_S = 60


class AlgoPositionRow(BaseModel):
    """One algo-attributed open position."""

    model_config = ConfigDict(extra="forbid")

    tradingsymbol: str
    internal_ticker: str
    product: Literal["MIS", "CNC"]
    quantity: int = Field(ge=0)
    avg_price: Decimal = Field(ge=Decimal("0"))
    last_price: Decimal = Field(ge=Decimal("0"))
    pnl_inr: Decimal
    pnl_pct: Decimal
    strategy_id: UUID
    strategy_name: str
    entry_ts: datetime | None = None
    days_held: int = Field(ge=0)
    t1_pending: bool = False


class AlgoPositionsResponse(BaseModel):
    """Wire shape for the dashboard Algo tab."""

    model_config = ConfigDict(extra="forbid")

    positions: list[AlgoPositionRow]
    as_of: datetime
    market_open: bool


def _days_held(entry_ts: datetime | None) -> int:
    """Floor(today_ist - entry_date_ist).days, clamped ≥ 0."""
    if entry_ts is None:
        return 0
    today_ist = datetime.now(_IST).date()
    entry_ist = entry_ts.astimezone(_IST).date()
    return max(0, (today_ist - entry_ist).days)
