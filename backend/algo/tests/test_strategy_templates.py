"""Template loader + sector_rotation_monthly parsing (REGIME-7)."""
from __future__ import annotations

import pytest

from backend.algo.strategy.ast import Strategy
from backend.algo.strategy.templates.loader import (
    list_templates,
    load_template,
)


def test_list_includes_sector_rotation() -> None:
    assert "sector_rotation_monthly" in list_templates()


def test_sector_rotation_parses() -> None:
    s = load_template("sector_rotation_monthly")
    assert isinstance(s, Strategy)
    assert s.name.startswith("Regime-aware")


def test_sector_rotation_root_is_and() -> None:
    s = load_template("sector_rotation_monthly")
    assert s.root.type == "and"
    # 3 operands: regime_label, mom_12_1, f_score
    assert len(s.root.operands) == 3


def test_sector_rotation_universe_is_india_stock() -> None:
    s = load_template("sector_rotation_monthly")
    assert s.universe.scope == "discovery"
    assert s.universe.filter.market == "india"
    assert "stock" in s.universe.filter.ticker_type


def test_unknown_template_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_template("does_not_exist")


# Regime-tailored templates (post-v3 polish) -------------------

def test_lists_all_four_regime_templates() -> None:
    names = set(list_templates())
    assert {
        "sector_rotation_monthly",
        "regime_bull_momentum",
        "regime_sideways_meanrev_quality",
        "regime_bear_defensive_lowvol",
    } <= names


def test_bull_template_parses_and_gates_on_bull() -> None:
    s = load_template("regime_bull_momentum")
    assert isinstance(s, Strategy)
    assert s.name.startswith("BULL")
    # First condition is regime_label == "BULL"
    assert s.root.type == "and"
    first = s.root.operands[0]
    assert first.type == "compare"
    assert first.left.feature == "regime_label"
    assert first.op == "=="
    assert first.right.literal == "BULL"


def test_sideways_template_parses_and_gates_on_sideways() -> None:
    s = load_template("regime_sideways_meanrev_quality")
    assert isinstance(s, Strategy)
    assert s.name.startswith("SIDEWAYS")
    first = s.root.operands[0]
    assert first.right.literal == "SIDEWAYS"


def test_bear_template_parses_and_gates_on_bear() -> None:
    s = load_template("regime_bear_defensive_lowvol")
    assert isinstance(s, Strategy)
    assert s.name.startswith("BEAR")
    first = s.root.operands[0]
    assert first.right.literal == "BEAR"


def test_bear_template_includes_stress_prob_gate() -> None:
    """BEAR template should require HMM-confirmed thaw via
    stress_prob < 0.5 — the only template that consults the
    HMM advisory directly."""
    s = load_template("regime_bear_defensive_lowvol")
    operand_features = []
    for op in s.root.operands:
        if op.type == "compare":
            operand_features.append(op.left.feature)
    assert "stress_prob" in operand_features


def test_all_templates_use_india_stock_universe() -> None:
    for name in (
        "regime_bull_momentum",
        "regime_sideways_meanrev_quality",
        "regime_bear_defensive_lowvol",
    ):
        s = load_template(name)
        assert s.universe.filter.market == "india"
        assert "stock" in s.universe.filter.ticker_type


def test_risk_budget_tightens_with_regime_severity() -> None:
    """Per the design doc — BULL is most aggressive, BEAR most
    conservative. Verify caps reflect the staircase."""
    bull = load_template("regime_bull_momentum")
    side = load_template("regime_sideways_meanrev_quality")
    bear = load_template("regime_bear_defensive_lowvol")
    assert (
        bull.risk.portfolio.max_exposure_pct
        > side.risk.portfolio.max_exposure_pct
        > bear.risk.portfolio.max_exposure_pct
    )
    assert (
        bull.risk.daily.max_open_positions
        >= side.risk.daily.max_open_positions
        > bear.risk.daily.max_open_positions
    )
