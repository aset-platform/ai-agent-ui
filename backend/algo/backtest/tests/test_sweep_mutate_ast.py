"""Tests for the AST-mutation helper used by the sweep
orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.algo.backtest.sweep import _mutate_ast
from backend.algo.strategy.ast import parse_strategy

# Load the v3 template once for path-resolution tests.
TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "strategy" / "templates"
    / "rsi2_connors_daily_v3.json"
)


def _load_v3():
    return parse_strategy(
        json.loads(TEMPLATE_PATH.read_text()),
    )


def test_mutate_cooldown_field_path():
    s = _load_v3()
    original = s.risk.per_trade.cooldown_after_failed_exit_days
    s2 = _mutate_ast(
        s,
        "risk.per_trade.cooldown_after_failed_exit_days",
        14,
    )
    # Mutated value visible on copy
    assert s2.risk.per_trade.cooldown_after_failed_exit_days == 14
    # Source untouched (deep copy)
    assert s.risk.per_trade.cooldown_after_failed_exit_days == original


def test_mutate_decimal_field_path():
    from decimal import Decimal
    s = _load_v3()
    s2 = _mutate_ast(
        s,
        "risk.per_trade.stop_loss_pct",
        Decimal("3.0"),
    )
    assert s2.risk.per_trade.stop_loss_pct == Decimal("3.0")


def test_mutate_min_adtv_inr_path():
    from decimal import Decimal
    s = _load_v3()
    s2 = _mutate_ast(
        s,
        "universe.filter.min_adtv_inr",
        Decimal("100000000"),
    )
    assert s2.universe.filter.min_adtv_inr == Decimal(
        "100000000",
    )


def test_mutate_unknown_path_raises():
    s = _load_v3()
    with pytest.raises(ValueError, match="resolve"):
        _mutate_ast(s, "bogus.path.nope", 7)


def test_mutate_partial_path_raises():
    s = _load_v3()
    with pytest.raises(ValueError, match="resolve"):
        _mutate_ast(
            s, "risk.per_trade.does_not_exist", 7,
        )
