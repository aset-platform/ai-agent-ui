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

from pydantic import BaseModel, ConfigDict, Field


class BacktestRequest(BaseModel):
    """Request body for POST /v1/algo/backtest/run."""
    model_config = ConfigDict(extra="forbid")

    strategy_id: UUID
    period_start: date
    period_end: date
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"), ge=Decimal("1000.00"),
    )


class BarData(BaseModel):
    """One day of OHLCV for one ticker."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


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


class BacktestSummary(BaseModel):
    """Run-level metrics persisted to algo.runs and returned by
    GET /v1/algo/backtest/runs/{id}."""
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    strategy_id: UUID
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
