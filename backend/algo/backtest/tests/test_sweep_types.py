"""Tests for sweep Pydantic types."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.backtest.sweep_types import (
    SweepConfig,
    SweepResult,
    SweepVariantSummary,
)


def test_sweep_config_minimal_valid():
    cfg = SweepConfig(
        base_strategy_id=uuid4(),
        period_start=date(2025, 11, 23),
        period_end=date(2026, 5, 23),
        swept_field="cooldown_days",
        swept_values=[3, 7, 14],
    )
    assert cfg.train_days == 60
    assert cfg.test_days == 30
    assert cfg.step_days == 30
    assert cfg.initial_capital_inr == Decimal("100000.00")
    assert cfg.regime_stratified is False
    assert cfg.interval_sec == 86400


def test_sweep_config_rejects_extra_fields():
    with pytest.raises(Exception):
        SweepConfig(
            base_strategy_id=uuid4(),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 6, 1),
            swept_field="cooldown_days",
            swept_values=[3, 7],
            bogus="extra",
        )


def test_sweep_variant_summary_completed():
    s = SweepVariantSummary(
        variant_index=0,
        swept_value=7,
        walkforward_run_id=uuid4(),
        avg_pnl_pct=Decimal("3.74"),
        avg_win_rate_pct=Decimal("63.9"),
        avg_max_drawdown_pct=Decimal("7.63"),
        sharpe=Decimal("0.648"),
        dsr=Decimal("0.62"),
        n_trades=83,
        status="completed",
    )
    assert s.status == "completed"
    assert s.error_text is None


def test_sweep_result_pending_state():
    r = SweepResult(
        run_id=uuid4(),
        base_strategy_id=uuid4(),
        swept_field="cooldown_days",
        swept_values=[3, 7, 14],
        variants=[],
        cross_variant_pbo=None,
        returns_matrix_shape=(0, 0),
        winner_variant_index=None,
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        status="pending",
    )
    assert r.status == "pending"
    assert r.cross_variant_pbo is None
