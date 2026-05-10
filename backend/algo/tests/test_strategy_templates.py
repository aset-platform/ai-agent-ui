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
