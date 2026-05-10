"""Per-regime breakdown tests."""
from __future__ import annotations

from backend.algo.backtest.metrics import (
    PerRegimeMetrics,
    per_regime_breakdown,
)


def _curve(*pts: tuple[str, float]) -> list[dict]:
    return [{"bar_date": d, "equity_inr": e} for d, e in pts]


def test_per_regime_empty_curve_empty_list() -> None:
    assert per_regime_breakdown([], {}) == []


def test_per_regime_three_regimes_each_present() -> None:
    """Three regimes, each with a few sequential days."""
    curve = _curve(
        ("2024-01-01", 100.0),
        ("2024-01-02", 102.0),
        ("2024-01-03", 104.0),  # BULL: +4%
        ("2024-01-04", 103.0),
        ("2024-01-05", 103.0),  # SIDEWAYS: -0.96%
        ("2024-01-06", 100.0),
        ("2024-01-07", 95.0),   # BEAR: -7.77%
    )
    labels = {
        "2024-01-01": "BULL", "2024-01-02": "BULL",
        "2024-01-03": "BULL",
        "2024-01-04": "SIDEWAYS", "2024-01-05": "SIDEWAYS",
        "2024-01-06": "BEAR", "2024-01-07": "BEAR",
    }
    out = per_regime_breakdown(curve, labels)
    by_regime = {r.regime: r for r in out}
    assert set(by_regime.keys()) == {"BULL", "SIDEWAYS", "BEAR"}
    assert by_regime["BULL"].n_days == 3
    assert by_regime["BULL"].cum_return_pct > 0
    assert by_regime["BEAR"].cum_return_pct < 0


def test_per_regime_missing_label_drops_silently() -> None:
    """Days without a regime label drop out (silent)."""
    curve = _curve(
        ("2024-01-01", 100.0),
        ("2024-01-02", 102.0),
        ("2024-01-03", 104.0),
    )
    labels = {"2024-01-01": "BULL", "2024-01-02": "BULL"}
    out = per_regime_breakdown(curve, labels)
    by_regime = {r.regime: r for r in out}
    assert "BULL" in by_regime
    assert by_regime["BULL"].n_days == 2  # dropped Jan 3


def test_per_regime_single_day_returns_zero_metrics() -> None:
    """A regime with only one day has no daily returns - sharpe
    and sortino are 0; cum_return and max_dd are 0."""
    curve = _curve(("2024-01-01", 100.0))
    labels = {"2024-01-01": "BULL"}
    out = per_regime_breakdown(curve, labels)
    assert len(out) == 1
    bull = out[0]
    assert bull.regime == "BULL"
    assert bull.n_days == 1
    assert bull.sharpe == 0.0
    assert bull.sortino == 0.0


def test_per_regime_metrics_dataclass_fields() -> None:
    """The dataclass exposes the seven documented fields."""
    fields = {
        "regime", "n_days", "cum_return_pct", "sharpe",
        "sortino", "max_dd_pct", "hit_rate",
    }
    assert (
        set(PerRegimeMetrics.__dataclass_fields__.keys()) == fields
    )


def test_per_regime_hit_rate_in_unit_interval() -> None:
    """hit_rate ∈ [0, 1] for every regime present."""
    curve = _curve(
        ("2024-01-01", 100.0),
        ("2024-01-02", 110.0),
        ("2024-01-03", 95.0),
        ("2024-01-04", 105.0),
    )
    labels = {
        "2024-01-01": "BULL", "2024-01-02": "BULL",
        "2024-01-03": "BULL", "2024-01-04": "BULL",
    }
    out = per_regime_breakdown(curve, labels)
    for r in out:
        assert 0.0 <= r.hit_rate <= 1.0
