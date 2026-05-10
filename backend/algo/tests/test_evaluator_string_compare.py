"""Evaluator handles string equal/not-equal compares (REGIME-3).

The evaluator's ``compare`` branch must dispatch to string equality
when both operands resolve to strings (e.g. ``regime_label == "bull"``)
while preserving the existing numeric path for everything else.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.algo.backtest.evaluator import EvalContext, Evaluator


def _ctx(features: dict) -> EvalContext:
    return EvalContext(
        ticker="TEST.NS",
        bar_date=date(2026, 5, 10),
        features=features,
        open_qty=0,
    )


def _compare(left: dict, op: str, right: dict) -> dict:
    return {"type": "compare", "left": left, "op": op, "right": right}


def test_string_equal_true() -> None:
    node = _compare(
        {"feature": "regime_label"}, "==", {"literal": "bull"},
    )
    assert Evaluator().eval_node(node, _ctx({"regime_label": "bull"})) is True


def test_string_equal_false() -> None:
    node = _compare(
        {"feature": "regime_label"}, "==", {"literal": "bull"},
    )
    assert (
        Evaluator().eval_node(node, _ctx({"regime_label": "sideways"}))
        is False
    )


def test_string_not_equal_true() -> None:
    node = _compare(
        {"feature": "regime_label"}, "!=", {"literal": "bear"},
    )
    assert Evaluator().eval_node(node, _ctx({"regime_label": "bull"})) is True


def test_string_not_equal_false() -> None:
    node = _compare(
        {"feature": "regime_label"}, "!=", {"literal": "bear"},
    )
    assert (
        Evaluator().eval_node(node, _ctx({"regime_label": "bear"}))
        is False
    )


def test_numeric_compare_unchanged() -> None:
    """Existing Decimal path must not regress."""
    node = _compare({"feature": "rsi"}, ">", {"literal": 70})
    assert Evaluator().eval_node(node, _ctx({"rsi": Decimal("75")})) is True
    assert (
        Evaluator().eval_node(node, _ctx({"rsi": Decimal("65")}))
        is False
    )


def test_string_op_other_than_eq_raises() -> None:
    """``<`` / ``>`` etc. on strings is nonsense — raise loudly."""
    node = _compare(
        {"feature": "regime_label"}, ">", {"literal": "bull"},
    )
    with pytest.raises(ValueError):
        Evaluator().eval_node(node, _ctx({"regime_label": "bear"}))


def test_mixed_string_numeric_raises() -> None:
    """Catching upstream typos early."""
    node = _compare(
        {"feature": "regime_label"}, "==", {"literal": 3.14},
    )
    with pytest.raises(ValueError):
        Evaluator().eval_node(node, _ctx({"regime_label": "bull"}))
