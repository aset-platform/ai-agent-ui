"""Regime-stratified walk_windows() tests."""
from __future__ import annotations

from datetime import date, timedelta

from backend.algo.backtest.walkforward import (
    WalkForwardConfig,
    walk_windows,
)


def _build_labels(
    start: date, end: date, pattern: list[str],
) -> dict[date, str]:
    """Cycle ``pattern`` over [start, end] inclusive."""
    out: dict[date, str] = {}
    cur = start
    i = 0
    while cur <= end:
        out[cur] = pattern[i % len(pattern)]
        cur += timedelta(days=1)
        i += 1
    return out


def test_walkforward_config_extension_defaults() -> None:
    """REGIME-5 fields default to safe values."""
    from uuid import uuid4
    cfg = WalkForwardConfig(
        strategy_id=uuid4(),
        period_start=date(2024, 1, 1),
        period_end=date(2024, 6, 30),
        train_days=30, test_days=30, step_days=30,
    )
    assert cfg.regime_stratified is True
    assert cfg.require_per_regime_non_negative is True
    assert float(cfg.require_dsr_min) == 0.95
    assert float(cfg.require_pbo_max) == 0.30
    assert float(cfg.require_max_dd_pct) == 25.0
    assert cfg.require_recovery_months_max == 18


def test_walk_windows_no_labels_keeps_v2_behavior() -> None:
    """Passing ``regime_labels=None`` reproduces V2-2 windows."""
    wins = walk_windows(
        date(2024, 1, 1), date(2024, 5, 8),
        train_days=30, test_days=30, step_days=30,
    )
    assert len(wins) == 3


def test_walk_windows_stratified_drops_window_missing_regime() -> None:
    """When a window's TRAIN slice misses a regime that exists
    elsewhere in the period, it is dropped."""
    start = date(2024, 1, 1)
    end = date(2024, 5, 8)
    # 0..29 BULL, 30..59 SIDEWAYS, 60..end BEAR
    labels: dict[date, str] = {}
    cur = start
    i = 0
    while cur <= end:
        if i < 30:
            labels[cur] = "BULL"
        elif i < 60:
            labels[cur] = "SIDEWAYS"
        else:
            labels[cur] = "BEAR"
        cur += timedelta(days=1)
        i += 1
    wins = walk_windows(
        start, end,
        train_days=30, test_days=30, step_days=30,
        regime_labels=labels,
    )
    # No 30-day window covers all 3 regimes (each spans 30 days),
    # so every window is dropped.
    assert wins == []


def test_walk_windows_stratified_keeps_window_with_all_regimes() -> None:
    """A window whose train slice contains all three regimes is
    kept."""
    start = date(2024, 1, 1)
    end = date(2024, 6, 30)
    # Cycle BULL/SIDEWAYS/BEAR each day - every window's train
    # slice will see all three.
    labels = _build_labels(start, end, ["BULL", "SIDEWAYS", "BEAR"])
    wins = walk_windows(
        start, end,
        train_days=30, test_days=30, step_days=30,
        regime_labels=labels,
    )
    assert len(wins) > 0


def test_walk_windows_stratified_period_with_only_two_regimes() -> None:
    """If the FULL period only has 2 regimes, windows need only
    cover those 2 - missing regime not required."""
    start = date(2024, 1, 1)
    end = date(2024, 4, 30)
    labels = _build_labels(start, end, ["BULL", "SIDEWAYS"])
    wins = walk_windows(
        start, end,
        train_days=30, test_days=30, step_days=30,
        regime_labels=labels,
    )
    # Every train window cycles between BULL+SIDEWAYS so all kept.
    assert len(wins) >= 1


def test_walk_windows_stratified_empty_labels_falls_through() -> None:
    """Empty ``regime_labels`` dict ignores stratification."""
    wins = walk_windows(
        date(2024, 1, 1), date(2024, 5, 8),
        train_days=30, test_days=30, step_days=30,
        regime_labels={},
    )
    assert len(wins) == 3
