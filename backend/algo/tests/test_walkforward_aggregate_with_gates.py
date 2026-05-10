"""Aggregator REGIME-5 integration tests.

Calls _aggregate_windows() with synthetic BacktestSummary objects
that carry small equity curves + a regime_labels map; verifies
the new fields land on WalkForwardAggregate.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.gates import GateThresholds
from backend.algo.backtest.types import (
    BacktestSummary,
    EquityPoint,
)
from backend.algo.backtest.walkforward import _aggregate_windows


def _summary(
    sid,
    equity_points: list[tuple[str, float]],
    pnl_pct: str = "5",
    win_rate: str = "60",
    max_dd: str = "3",
) -> BacktestSummary:
    eq = [
        EquityPoint(
            bar_date=date.fromisoformat(d),
            equity_inr=Decimal(str(v)),
        )
        for d, v in equity_points
    ]
    return BacktestSummary(
        run_id=uuid4(),
        strategy_id=sid,
        status="completed",
        period_start=date.fromisoformat(equity_points[0][0]),
        period_end=date.fromisoformat(equity_points[-1][0]),
        initial_capital_inr=Decimal("100000"),
        final_equity_inr=Decimal(str(equity_points[-1][1])),
        total_pnl_inr=Decimal("5000"),
        total_pnl_pct=Decimal(pnl_pct),
        total_fees_inr=Decimal("100"),
        total_trades=5,
        winning_trades=3,
        losing_trades=2,
        win_rate_pct=Decimal(win_rate),
        max_drawdown_pct=Decimal(max_dd),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        fee_rates_version="2024-01-01",
        equity_curve=eq,
        trade_list=[],
    )


def test_aggregate_no_regime_labels_v2_shape() -> None:
    """Without regime_labels the aggregate has empty per_regime
    + non-stratified flag; gates dict still populated."""
    sid = uuid4()
    s1 = _summary(sid, [
        ("2024-01-01", 100.0), ("2024-01-02", 102.0),
        ("2024-01-03", 104.0),
    ])
    agg = _aggregate_windows([s1])
    assert agg.completed_count == 1
    assert agg.per_regime == []
    assert agg.regime_stratified is False
    # Gates are computed even without regime info
    assert set(agg.gates_passed.keys()) == {
        "max_dd_ok", "recovery_ok", "per_regime_non_neg",
        "dsr_ok", "pbo_ok",
    }
    # PBO defaults None for n_trials=1
    assert agg.pbo is None


def test_aggregate_with_regime_labels_emits_per_regime() -> None:
    """With regime_labels, per_regime is populated."""
    sid = uuid4()
    s1 = _summary(sid, [
        ("2024-01-01", 100.0), ("2024-01-02", 102.0),
        ("2024-01-03", 104.0), ("2024-01-04", 103.0),
        ("2024-01-05", 103.0),
    ])
    labels = {
        date(2024, 1, 1): "BULL", date(2024, 1, 2): "BULL",
        date(2024, 1, 3): "BULL",
        date(2024, 1, 4): "SIDEWAYS",
        date(2024, 1, 5): "SIDEWAYS",
    }
    agg = _aggregate_windows(
        [s1],
        regime_labels=labels,
        regime_stratified=True,
    )
    assert agg.regime_stratified is True
    by_regime = {r.regime: r for r in agg.per_regime}
    assert "BULL" in by_regime
    assert "SIDEWAYS" in by_regime


def test_aggregate_thresholds_shift_gate_outcomes() -> None:
    """Custom (loose) thresholds flip dsr_ok from fail to pass."""
    sid = uuid4()
    # Tiny curve → DSR will be low for default 0.95 gate
    s1 = _summary(sid, [
        ("2024-01-01", 100.0), ("2024-01-02", 100.5),
        ("2024-01-03", 101.0),
    ])
    loose = GateThresholds(
        dsr_min=0.0, max_dd_pct=100.0,
        recovery_months_max=999,
    )
    agg = _aggregate_windows([s1], thresholds=loose)
    assert agg.gates_passed["dsr_ok"] is True


def test_aggregate_serialises_with_new_fields() -> None:
    """WalkForwardAggregate round-trips through JSON cleanly."""
    sid = uuid4()
    s1 = _summary(sid, [
        ("2024-01-01", 100.0), ("2024-01-02", 102.0),
    ])
    agg = _aggregate_windows([s1])
    payload = agg.model_dump_json()
    from backend.algo.backtest.walkforward import (
        WalkForwardAggregate,
    )
    restored = WalkForwardAggregate.model_validate_json(payload)
    assert restored.gates_passed == agg.gates_passed
    assert restored.regime_stratified == agg.regime_stratified
    assert restored.recovery_months == agg.recovery_months
