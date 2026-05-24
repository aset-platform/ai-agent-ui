"""Pydantic types shared by the sweep runner, routes,
and aggregator. Mirrors the shapes in
``backend/algo/backtest/types.py`` (single backtest)
and the walk-forward types — sweep types live one level
higher.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SweepConfig(BaseModel):
    """Request body for POST /v1/algo/sweep/run."""

    model_config = ConfigDict(extra="forbid")

    base_strategy_id: UUID
    period_start: date
    period_end: date
    train_days: int = Field(default=60, ge=1)
    test_days: int = Field(default=30, ge=1)
    step_days: int = Field(default=30, ge=1)
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"),
        ge=Decimal("1000.00"),
    )
    regime_stratified: bool = False
    swept_field: str  # short whitelist key
    swept_values: list[Any]  # validated per field meta
    interval_sec: int = 86400


class SweepVariantSummary(BaseModel):
    """One variant's aggregate, embedded in SweepResult."""

    model_config = ConfigDict(extra="forbid")

    variant_index: int
    swept_value: Any
    walkforward_run_id: UUID
    avg_pnl_pct: Decimal
    avg_win_rate_pct: Decimal
    avg_max_drawdown_pct: Decimal
    sharpe: Decimal
    dsr: Decimal
    n_trades: int
    status: Literal["completed", "failed", "skipped"]
    error_text: str | None = None


class SweepResult(BaseModel):
    """Sweep parent row's ``summary_json`` shape.

    ``swept_field`` is the short whitelist key (e.g.
    ``"cooldown_days"``) — NOT the dotted AST path. The
    path is derivable via SWEEPABLE_FIELDS at read time.
    Keeping the key (not the path) means a future rename
    of the underlying AST path doesn't orphan historical
    sweep rows.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    base_strategy_id: UUID
    swept_field: str
    swept_values: list[Any]
    variants: list[SweepVariantSummary] = Field(
        default_factory=list,
    )
    cross_variant_pbo: Decimal | None = None
    returns_matrix_shape: tuple[int, int] = (0, 0)
    winner_variant_index: int | None = None
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal[
        "pending", "running", "completed", "failed",
    ] = "pending"
