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


_SUPPORTED_INTERVAL_SEC = frozenset({60, 300, 900, 86400})


class BacktestRequest(BaseModel):
    """Request body for POST /v1/algo/backtest/run."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: UUID
    period_start: date
    period_end: date
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"),
        ge=Decimal("1000.00"),
    )
    # ASETPLTFRM-400 slice 3 — backtest cadence. 86400 = daily
    # (the original use case, unchanged); 60/300/900 select the
    # intraday loader + (ticker, bar_open_ts_ns) runner loop.
    interval_sec: int = Field(default=86400)

    @field_validator("period_start")
    @classmethod
    def _start_floor(cls, v: date) -> date:
        return _enforce_backtest_start_floor(v)

    @field_validator("interval_sec")
    @classmethod
    def _supported_interval(cls, v: int) -> int:
        if v not in _SUPPORTED_INTERVAL_SEC:
            raise ValueError(
                f"interval_sec={v} not supported; use one of "
                f"{sorted(_SUPPORTED_INTERVAL_SEC)} (86400=daily)."
            )
        return v


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
    # ASETPLTFRM-400 slice 3 — intraday cadence. When set, the
    # SimBroker fills at the NEXT intraday bar's open (not next
    # calendar day's open). Daily strategies leave this None.
    intent_emitted_ts_ns: int | None = None
    # exit_reason tag — propagates through Fill to Position.
    # "signal" (AST), "stop_loss", "mis_square_off",
    # "period_end_mtm". Default "signal" keeps existing AST-emit
    # code backwards-compat.
    exit_reason: str = "signal"


class Fill(BaseModel):
    """One executed fill emitted by SimBroker."""

    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    ticker: str
    side: Literal["BUY", "SELL"]
    qty: int
    fill_price: Decimal
    fill_date: date  # T+1
    fees_inr: Decimal  # IndianFeeModel.compute total
    fee_rates_version: str  # stamps the dated YAML row used
    # ASETPLTFRM-400 slice 3 — intraday cadence. The ns-since-
    # epoch timestamp of the fill bar's open. ``None`` for daily
    # fills.
    fill_ts_ns: int | None = None
    # Propagated from the originating OrderIntent. Defaults to
    # "signal" so AST-emitted intents that don't set the field
    # serialise as ordinary strategy exits.
    exit_reason: str = "signal"


class Position(BaseModel):
    """Open or closed position from PositionTracker."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    qty: int
    avg_price: Decimal
    opened_at: date
    closed_at: date | None = None
    realised_pnl_inr: Decimal = Field(default=Decimal("0.00"))
    # Why this position closed:
    #   "signal"          — strategy exit rule fired (default)
    #   "stop_loss"       — per-trade stop-loss tripped
    #   "mis_square_off"  — MIS auto-square-off at day end
    #   "period_end_mtm"  — backtest force-closed at last bar
    exit_reason: str = "signal"
    # Intraday timestamps (ns since epoch UTC). Stays None for
    # daily-cadence trades; intraday cadences (15m / 5m / 1m)
    # stamp the bar_open_ts_ns of the fill bar so the UI can
    # render "YYYY-MM-DD HH:mm IST" instead of bare dates.
    opened_at_ts_ns: int | None = None
    closed_at_ts_ns: int | None = None


class EquityPoint(BaseModel):
    """One equity snapshot.

    Daily backtests emit one per ``bar_date`` (``bar_open_ts_ns``
    stays None). Intraday backtests (slice 5) emit one per
    closed bar within the trading day — ``bar_open_ts_ns``
    disambiguates the ~25 snapshots that share a single
    ``bar_date`` so the equity curve can be plotted with
    intra-day granularity on the x-axis.
    """

    model_config = ConfigDict(extra="forbid")

    bar_date: date
    equity_inr: Decimal
    # ASETPLTFRM-400 slice 5 — intraday equity granularity.
    bar_open_ts_ns: int | None = None


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
    # Mirror of ``Position.exit_reason`` so the UI trade-table can
    # show a badge per row (signal / stop_loss / mis_square_off /
    # period_end_mtm). Defaults to "signal" on legacy persisted
    # summaries.
    exit_reason: str = "signal"
    # Intraday timestamps (ns since epoch UTC). None on daily
    # cadence; intraday cadences carry the fill bar's open ts so
    # the UI can render "YYYY-MM-DD HH:mm IST" in Opened/Closed.
    opened_at_ts_ns: int | None = None
    closed_at_ts_ns: int | None = None


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
        "pending",
        "running",
        "completed",
        "failed",
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
    # ASETPLTFRM-400 slice 7 — surfaced to the UI so the
    # results panel can show a cadence chip
    # ("15m" / "5m" / "1m" / "Daily"). Defaults to 86400 so
    # historical runs serialised before slice 7 deserialise
    # cleanly as daily.
    interval_sec: int = 86400
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    trade_list: list[TradeRow] = Field(default_factory=list)
    error_text: str | None = None
