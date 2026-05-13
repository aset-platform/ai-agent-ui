"""Pydantic models shared across the backtest engine.

Single source of truth for the wire shape between the runner,
evaluator, sim-broker, position tracker, and the run-summary
endpoint. Keep these stable — every downstream module imports
the types declared here.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

# REGIME-7 — backtest start floor. 2007-01-01 is mandatory so
# every backtest spans at least one bear-market regime (the 2008
# global financial crisis), which the regime-stratified gates
# in REGIME-5 require to detect overfitting.
BACKTEST_START_FLOOR = date(2007, 1, 1)


def _enforce_backtest_start_floor(v: date) -> date:
    if v < BACKTEST_START_FLOOR:
        raise ValueError(
            "Backtest start floor is 2007-01-01 (mandatory to "
            "include the 2008 bear market for survivorship + "
            "regime validation)."
        )
    return v


class BacktestRequest(BaseModel):
    """Request body for POST /v1/algo/backtest/run."""
    model_config = ConfigDict(extra="forbid")

    strategy_id: UUID
    period_start: date
    period_end: date
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"), ge=Decimal("1000.00"),
    )

    @field_validator("period_start")
    @classmethod
    def _start_floor(cls, v: date) -> date:
        return _enforce_backtest_start_floor(v)


class BarData(BaseModel):
    """One OHLCV bar for one ticker.

    For daily strategies (the original use case), one bar = one
    trading day and ``date`` uniquely identifies it. ``bar_open_ts_ns``
    is None for backwards-compat.

    For intraday strategies (ASETPLTFRM-392), multiple bars share
    the same ``date``. ``bar_open_ts_ns`` carries the bar window's
    start as ns-since-epoch so the runtime's append-vs-update logic
    can distinguish bars within the same trading day.
    """
    model_config = ConfigDict(extra="forbid")

    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    # ASETPLTFRM-392 — intraday bar window start (ns since epoch).
    # Optional: daily bars leave this None.
    bar_open_ts_ns: int | None = None


class OrderIntent(BaseModel):
    """Strategy → SimBroker handoff. Emitted by the evaluator."""
    model_config = ConfigDict(extra="forbid")

    intent_id: UUID = Field(default_factory=uuid4)
    ticker: str
    side: Literal["BUY", "SELL"]
    qty: int = Field(ge=1)
    intent_emitted_at: date  # bar T; fills at T+1 open


class Fill(BaseModel):
    """One executed fill emitted by SimBroker."""
    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    ticker: str
    side: Literal["BUY", "SELL"]
    qty: int
    fill_price: Decimal
    fill_date: date         # T+1
    fees_inr: Decimal       # IndianFeeModel.compute total
    fee_rates_version: str  # stamps the dated YAML row used


class Position(BaseModel):
    """Open or closed position from PositionTracker."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    qty: int
    avg_price: Decimal
    opened_at: date
    closed_at: date | None = None
    realised_pnl_inr: Decimal = Field(default=Decimal("0.00"))


class EquityPoint(BaseModel):
    """One end-of-day equity snapshot."""
    model_config = ConfigDict(extra="forbid")

    bar_date: date
    equity_inr: Decimal


class TradeRow(BaseModel):
    """One closed-position row for the trade table."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    qty: int
    avg_price: Decimal
    fill_price: Decimal
    opened_at: date
    closed_at: date
    holding_days: int
    realised_pnl_inr: Decimal
    return_pct: Decimal


class BacktestRun(BaseModel):
    """Row shape for GET /runs (list endpoint)."""
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    strategy_id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    period_start: date
    period_end: date
    started_at: datetime
    completed_at: datetime | None = None
    total_pnl_inr: Decimal | None = None
    total_pnl_pct: Decimal | None = None
    error_text: str | None = None


class BacktestSummary(BaseModel):
    """Run-level metrics persisted to algo.runs and returned by
    GET /v1/algo/backtest/runs/{id}."""
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    strategy_id: UUID
    status: Literal[
        "pending", "running", "completed", "failed",
    ] = "completed"
    period_start: date
    period_end: date
    initial_capital_inr: Decimal
    final_equity_inr: Decimal
    total_pnl_inr: Decimal
    total_pnl_pct: Decimal
    total_fees_inr: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    max_drawdown_pct: Decimal
    started_at: datetime
    completed_at: datetime
    fee_rates_version: str
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    trade_list: list[TradeRow] = Field(default_factory=list)
    error_text: str | None = None
