"""Pydantic models + enums shared across the paper-trading runtime."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class RejectReason(str, Enum):
    """Why the risk engine blocked a signal. Surfaced via Replay
    tab in Slice 10."""

    DAILY_LOSS_CAP = "daily_loss_cap"
    EXPOSURE_CAP = "exposure_cap"
    POSITION_CAP = "position_cap"
    MAX_OPEN_POSITIONS = "max_open_positions"
    MAX_QTY = "max_qty"
    KILL_SWITCH = "kill_switch"
    INSTRUMENT_BLACKLIST = "instrument_blacklist"


class Signal(BaseModel):
    """A strategy-emitted intent, before the risk gate."""
    model_config = ConfigDict(extra="forbid")

    signal_id: UUID = Field(default_factory=uuid4)
    strategy_id: UUID
    user_id: UUID
    ticker: str
    side: Literal["BUY", "SELL"]
    qty: int = Field(ge=1)
    emitted_at_ns: int = Field(ge=0)


class AccountState(BaseModel):
    """Snapshot the risk engine reads to gate a signal.

    All values are in INR. ``open_positions`` is keyed by ticker.
    """
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    day_date: date
    initial_capital_inr: Decimal
    current_equity_inr: Decimal
    daily_realised_pnl_inr: Decimal
    daily_unrealised_pnl_inr: Decimal
    open_positions: dict[str, int] = Field(default_factory=dict)
    open_position_count: int = 0
    kill_switch_active: bool = False


class RiskDecision(BaseModel):
    """The risk engine's verdict.

    - ``outcome="accept"`` → forward the signal verbatim.
    - ``outcome="scale"``  → scale qty down to ``adjusted_qty``.
    - ``outcome="reject"`` → drop the signal; ``reason`` populated.
    """
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["accept", "scale", "reject"]
    adjusted_qty: int | None = None
    reason: RejectReason | None = None
    threshold: Decimal | None = None
    observed_value: Decimal | None = None


class KillSwitchState(BaseModel):
    """Row shape for GET /v1/algo/kill-switch."""
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    active: bool
    set_by: UUID | None = None
    set_at: datetime | None = None
    reason: str | None = None
