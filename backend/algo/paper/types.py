"""Pydantic models + enums shared across the paper-trading runtime."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class RejectReason(str, Enum):
    """Why the risk engine blocked a signal. Surfaced via Replay
    tab in Slice 10.

    v2 additions (live-only caps 2-4):
    - LIVE_TICKER_NOT_ALLOWED — ticker not in the allow-list.
    - LIVE_INR_CAP — daily ₹ cap would be exceeded.
    - LIVE_ORDERS_PER_DAY_CAP — max_orders_per_day reached.
    - LIVE_NOT_ENABLED — live_orders_enabled=False.
    """

    DAILY_LOSS_CAP = "daily_loss_cap"
    EXPOSURE_CAP = "exposure_cap"
    POSITION_CAP = "position_cap"
    MAX_OPEN_POSITIONS = "max_open_positions"
    MAX_QTY = "max_qty"
    KILL_SWITCH = "kill_switch"
    INSTRUMENT_BLACKLIST = "instrument_blacklist"
    # v2 live-only reject reasons
    LIVE_TICKER_NOT_ALLOWED = "live_ticker_not_allowed"
    LIVE_INR_CAP = "live_inr_cap"
    LIVE_ORDERS_PER_DAY_CAP = "live_orders_per_day_cap"
    LIVE_NOT_ENABLED = "live_not_enabled"
    LIVE_BUDGET_CAP = "live_budget_cap"


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
    # Human-readable trigger label (the action type emitted by the
    # strategy AST: "buy" / "sell" / "exit" / "set_target_weight").
    # Threaded through to the order_filled_live event payload so
    # the Positions tab Reason column has something to display.
    # Optional + default None for backwards compat with callers
    # that don't populate it yet.
    reason: str | None = None


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
    metadata: dict[str, Any] = Field(default_factory=dict)


class KillSwitchState(BaseModel):
    """Row shape for GET /v1/algo/kill-switch."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    active: bool
    set_by: UUID | None = None
    set_at: datetime | None = None
    reason: str | None = None
