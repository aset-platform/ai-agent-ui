"""Walkforward two-clock inheritance + metadata-propagation tests.

Step 1 — window math
    walk_windows arithmetic is the backbone of walkforward folds;
    lock it so any future refactor breaks loudly.

Step 2 — metadata propagation via model_copy
    Walkforward stamps each fold summary via::

        summary = summary.model_copy(update={"run_id": child_run_id})

    This test exercises that exact operation with a VALID BacktestSummary
    (built using the same minimal field-set used by _fake_summary in
    test_sweep_pbo.py) and asserts that execution_interval_sec survives
    the copy unchanged while run_id is updated.  That is the real
    propagation path, not just construction.
"""
from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, timedelta
from decimal import Decimal

from backend.algo.backtest.types import BacktestSummary, EquityPoint
from backend.algo.backtest.walkforward import walk_windows


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _minimal_summary(
    *,
    execution_interval_sec: int,
) -> BacktestSummary:
    """Build the smallest valid BacktestSummary.

    Field set mirrors _fake_summary() in test_sweep_pbo.py — the
    canonical minimal fixture used across the backtest test suite.
    """
    start = date(2026, 1, 1)
    pts = [
        EquityPoint(
            bar_date=start + timedelta(days=i),
            equity_inr=Decimal(str(v)),
        )
        for i, v in enumerate([100_000.0, 101_000.0, 102_000.0])
    ]
    return BacktestSummary(
        run_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        status="completed",
        period_start=start,
        period_end=start + timedelta(days=2),
        initial_capital_inr=Decimal("100000"),
        final_equity_inr=Decimal("102000"),
        total_pnl_inr=Decimal("2000"),
        total_pnl_pct=Decimal("2"),
        total_fees_inr=Decimal("0"),
        total_trades=2,
        winning_trades=2,
        losing_trades=0,
        win_rate_pct=Decimal("100"),
        max_drawdown_pct=Decimal("0"),
        started_at=dt.datetime.now(),
        completed_at=dt.datetime.now(),
        fee_rates_version="test",
        equity_curve=pts,
        execution_interval_sec=execution_interval_sec,
    )


# ---------------------------------------------------------------------------
# Step 1: window-math lock
# ---------------------------------------------------------------------------

def test_walk_windows_cover_period():
    """Window math: test slices are 15 calendar days, contiguous."""
    wins = walk_windows(
        date(2026, 1, 1), date(2026, 3, 31),
        train_days=30, test_days=15, step_days=15,
    )
    assert wins, "expected at least one window"
    # walk_windows: test_end = test_start + test_days - 1
    # so (test_end - test_start).days == test_days - 1 == 14
    for w in wins:
        assert (w.test_end - w.test_start).days == 14
        # test window begins the calendar day AFTER train ends
        assert w.test_start == w.train_end + timedelta(days=1)


def test_walk_windows_no_partial_windows():
    """Trailing partial windows are dropped, not truncated."""
    # 30-day train + 15-day test = 45 days minimum per window.
    # A 43-day period can't fit even one complete window.
    wins = walk_windows(
        date(2026, 1, 1), date(2026, 2, 13),  # 43 days span
        train_days=30, test_days=15, step_days=15,
    )
    assert wins == [], (
        "period shorter than one window must yield no windows"
    )


# ---------------------------------------------------------------------------
# Step 2: execution_interval_sec propagation via model_copy
# ---------------------------------------------------------------------------

def test_fold_summary_carries_execution_interval():
    """execution_interval_sec survives the per-fold model_copy.

    Walkforward does exactly::

        summary = summary.model_copy(update={"run_id": child_run_id})

    We exercise that same operation with a real BacktestSummary
    (not just assert a value we just set) and verify:
      1. execution_interval_sec is PRESERVED in the copy.
      2. run_id is UPDATED to the new child_run_id.

    This is the propagation path that matters — if the field were
    ever accidentally excluded from the model or marked as non-copyable
    this test would catch it.
    """
    original_run_id = uuid.uuid4()
    child_run_id = uuid.uuid4()

    original = _minimal_summary(execution_interval_sec=900)

    # Exercise the exact operation walkforward.py line ~685 performs.
    copied = original.model_copy(update={"run_id": child_run_id})

    # The new field must survive the copy.
    assert copied.execution_interval_sec == 900, (
        "execution_interval_sec was lost by model_copy — "
        "walkforward fold summaries would silently revert to the "
        "default 86400 (daily) regardless of the actual clock used"
    )
    # The mutation must actually take effect.
    assert copied.run_id == child_run_id, (
        "run_id was not updated by model_copy"
    )
    # Original must be untouched (Pydantic model_copy is non-mutating).
    assert original.run_id != child_run_id, (
        "model_copy must not mutate the original"
    )


def test_fold_summary_non_default_interval_roundtrip():
    """Non-default execution intervals other than 900 also survive."""
    for interval in (60, 300, 900):
        summary = _minimal_summary(execution_interval_sec=interval)
        child_id = uuid.uuid4()
        copy = summary.model_copy(update={"run_id": child_id})
        assert copy.execution_interval_sec == interval, (
            f"execution_interval_sec={interval} lost after model_copy"
        )
