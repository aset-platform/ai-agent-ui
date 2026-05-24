"""Tests for sweep PBO-aggregation helpers."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest

from backend.algo.backtest.sweep_pbo import (
    build_returns_matrix,
    compute_sweep_pbo,
    variant_equity_curve,
)
from backend.algo.backtest.types import (
    BacktestSummary,
    EquityPoint,
)


def _fake_summary(
    start: date, equity_seq: list[float],
) -> BacktestSummary:
    """Minimal BacktestSummary fixture for testing."""
    pts = [
        EquityPoint(
            bar_date=start + timedelta(days=i),
            equity_inr=Decimal(str(v)),
        )
        for i, v in enumerate(equity_seq)
    ]
    return BacktestSummary(
        run_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        status="completed",
        period_start=start,
        period_end=start + timedelta(
            days=len(equity_seq) - 1,
        ),
        initial_capital_inr=Decimal(str(equity_seq[0])),
        final_equity_inr=Decimal(str(equity_seq[-1])),
        total_pnl_inr=(
            Decimal(str(equity_seq[-1]))
            - Decimal(str(equity_seq[0]))
        ),
        total_pnl_pct=Decimal("0"),
        total_fees_inr=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate_pct=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        started_at=dt.datetime.now(),
        completed_at=dt.datetime.now(),
        fee_rates_version="test",
        equity_curve=pts,
    )


def test_variant_equity_curve_single_window():
    w = _fake_summary(date(2025, 1, 1), [100, 102, 105])
    out = variant_equity_curve(
        [w], initial_capital=Decimal("1000"),
    )
    assert len(out) == 3
    assert out[0][1] == Decimal("1000")
    # 102/100 * 1000 = 1020
    assert out[1][1] == Decimal("1020")
    # 105/100 * 1000 = 1050
    assert out[2][1] == Decimal("1050")


def test_variant_equity_curve_chains_windows():
    w1 = _fake_summary(date(2025, 1, 1), [100, 110])
    w2 = _fake_summary(date(2025, 2, 1), [100, 90])
    out = variant_equity_curve(
        [w1, w2], initial_capital=Decimal("1000"),
    )
    # Window 1: ends at 1100 (10% gain)
    # Window 2: 90/100 * 1100 = 990 (10% loss applied)
    assert out[-1][1] == Decimal("990")


def test_variant_equity_curve_empty():
    out = variant_equity_curve(
        [], initial_capital=Decimal("1000"),
    )
    assert out == []


def test_build_returns_matrix_two_aligned_variants():
    a = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 1, 2), Decimal("101")),
        (date(2025, 1, 3), Decimal("103")),
    ]
    b = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 1, 2), Decimal("102")),
        (date(2025, 1, 3), Decimal("99")),
    ]
    R, dates = build_returns_matrix([a, b])
    assert R.shape == (2, 2)
    np.testing.assert_allclose(R[0], [0.01, 0.02])
    np.testing.assert_allclose(
        R[1], [(103 - 101) / 101, (99 - 102) / 102],
    )


def test_build_returns_matrix_drops_non_common_dates():
    a = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 1, 2), Decimal("101")),
        (date(2025, 1, 3), Decimal("103")),
    ]
    b = [
        (date(2025, 1, 2), Decimal("100")),
        (date(2025, 1, 3), Decimal("102")),
    ]
    R, dates = build_returns_matrix([a, b])
    # Common dates: 2025-01-02, 2025-01-03
    # T = len(common) - 1 = 1
    assert R.shape == (1, 2)
    assert len(dates) == 1


def test_build_returns_matrix_insufficient_common_dates():
    a = [(date(2025, 1, 1), Decimal("100"))]
    b = [(date(2025, 1, 2), Decimal("100"))]
    R, dates = build_returns_matrix([a, b])
    assert R.shape == (0, 0)
    assert dates == []


def test_compute_sweep_pbo_too_few_variants():
    R = np.random.RandomState(42).normal(
        0, 0.01, size=(50, 1),
    )
    assert compute_sweep_pbo(R) is None


def test_compute_sweep_pbo_too_few_days():
    R = np.random.RandomState(42).normal(
        0, 0.01, size=(5, 3),
    )
    assert compute_sweep_pbo(R) is None


def test_compute_sweep_pbo_returns_decimal():
    rng = np.random.RandomState(42)
    R = rng.normal(0, 0.01, size=(100, 5))
    pbo = compute_sweep_pbo(R)
    assert pbo is not None
    assert isinstance(pbo, Decimal)
    assert Decimal("0") <= pbo <= Decimal("1")
