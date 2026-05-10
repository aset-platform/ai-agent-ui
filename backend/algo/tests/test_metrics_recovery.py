"""Recovery time tests."""
from __future__ import annotations

from backend.algo.backtest.metrics import recovery_months_from_dd


def _curve(*pts: tuple[str, float]) -> list[dict]:
    return [{"bar_date": d, "equity_inr": e} for d, e in pts]


def test_recovery_no_drawdown_returns_zero() -> None:
    """Monotonically rising equity → no DD → 0 months recovery."""
    curve = _curve(
        ("2024-01-01", 100.0),
        ("2024-02-01", 110.0),
        ("2024-03-01", 120.0),
    )
    assert recovery_months_from_dd(curve) == 0


def test_recovery_quick_recovery_one_month() -> None:
    """Trough Jan 31, recovers Feb 28 → ~1 month recovery."""
    curve = _curve(
        ("2024-01-01", 100.0),
        ("2024-01-31", 80.0),    # trough at -20%
        ("2024-02-28", 100.0),   # recovered to HWM
    )
    months = recovery_months_from_dd(curve)
    assert 0 <= months <= 2


def test_recovery_never_recovers_returns_window_months() -> None:
    """Equity stays below HWM until end of window → returns
    months from trough to end."""
    curve = _curve(
        ("2024-01-01", 100.0),
        ("2024-02-01", 80.0),
        ("2024-12-01", 85.0),    # still below 100
    )
    months = recovery_months_from_dd(curve)
    # Trough is Feb 1 → Dec 1 = 304 days ≈ 11 months
    assert months >= 9


def test_recovery_empty_curve_returns_zero() -> None:
    assert recovery_months_from_dd([]) == 0


def test_recovery_single_point_returns_zero() -> None:
    curve = _curve(("2024-01-01", 100.0))
    assert recovery_months_from_dd(curve) == 0
