"""Brinson decomposition tests — algebraic identity (REGIME-6)."""
from __future__ import annotations

import pytest

from backend.algo.attribution.brinson import (
    BrinsonComponents,
    compute_brinson,
)


def test_decomposition_sums_to_active_return() -> None:
    """alloc + sel + inter == active return (sum across sectors).

    This is the canonical Brinson identity. If it fails the
    formula is wrong.
    """
    portfolio_weights = {"IT": 0.5, "Banks": 0.3, "Auto": 0.2}
    benchmark_weights = {"IT": 0.3, "Banks": 0.4, "Auto": 0.3}
    portfolio_returns = {"IT": 0.05, "Banks": 0.02, "Auto": -0.01}
    benchmark_returns = {"IT": 0.04, "Banks": 0.03, "Auto": 0.00}

    out = compute_brinson(
        portfolio_weights, benchmark_weights,
        portfolio_returns, benchmark_returns,
    )

    total_alloc = sum(c.allocation for c in out.values())
    total_sel = sum(c.selection for c in out.values())
    total_inter = sum(c.interaction for c in out.values())

    p_total = sum(
        portfolio_weights[s] * portfolio_returns[s]
        for s in portfolio_weights
    )
    b_total = sum(
        benchmark_weights[s] * benchmark_returns[s]
        for s in benchmark_weights
    )
    active = p_total - b_total

    assert abs(
        (total_alloc + total_sel + total_inter) - active,
    ) < 1e-9


def test_zero_active_when_identical_portfolios() -> None:
    """Identical portfolio + benchmark → zero in every cell."""
    weights = {"IT": 0.5, "Banks": 0.5}
    returns = {"IT": 0.03, "Banks": -0.01}
    out = compute_brinson(weights, weights, returns, returns)
    for c in out.values():
        assert abs(c.allocation) < 1e-9
        assert abs(c.selection) < 1e-9
        assert abs(c.interaction) < 1e-9


def test_handles_sector_in_portfolio_only() -> None:
    """A sector in portfolio but not benchmark must still appear
    in the output (zero benchmark side)."""
    pw = {"IT": 0.6, "EmergingTech": 0.4}
    bw = {"IT": 0.5, "Banks": 0.5}
    pr = {"IT": 0.05, "EmergingTech": 0.10}
    br = {"IT": 0.04, "Banks": 0.02}
    out = compute_brinson(pw, bw, pr, br)
    assert "EmergingTech" in out
    assert "Banks" in out


def test_empty_inputs_return_empty() -> None:
    assert compute_brinson({}, {}, {}, {}) == {}


def test_components_total_property() -> None:
    """BrinsonComponents.total = alloc + sel + inter."""
    c = BrinsonComponents(
        sector="X", allocation=0.1, selection=-0.05,
        interaction=0.02,
    )
    assert c.total == pytest.approx(0.07)
