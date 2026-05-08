"""AST evaluator dispatch tests. Inputs are minimal — the
evaluator only cares about the per-bar context; it's the
runner's job to assemble that context from data_source +
PositionTracker.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.algo.backtest.evaluator import (
    EvalContext,
    Evaluator,
)


@pytest.fixture
def ctx() -> EvalContext:
    return EvalContext(
        ticker="RELIANCE.NS",
        bar_date=date(2024, 1, 5),
        features={
            "today_ltp": Decimal("2945.20"),
            "sma_50": Decimal("2900.00"),
            "sma_200": Decimal("2800.00"),
            "rsi": Decimal("65"),
            "pscore": Decimal("8"),
        },
        open_qty=0,
    )


def test_compare_feature_to_literal_true(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "compare",
            "left": {"feature": "rsi"},
            "op": "<",
            "right": {"literal": 70},
        },
        ctx,
    ) is True


def test_compare_feature_to_literal_false(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "compare",
            "left": {"feature": "rsi"},
            "op": ">",
            "right": {"literal": 70},
        },
        ctx,
    ) is False


def test_and_short_circuits(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "and",
            "operands": [
                {
                    "type": "compare",
                    "left": {"feature": "today_ltp"},
                    "op": ">",
                    "right": {"feature": "sma_50"},
                },
                {
                    "type": "compare",
                    "left": {"feature": "pscore"},
                    "op": ">=",
                    "right": {"literal": 7},
                },
            ],
        },
        ctx,
    ) is True


def test_or_short_circuits(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "or",
            "operands": [
                {
                    "type": "compare",
                    "left": {"feature": "rsi"},
                    "op": "<",
                    "right": {"literal": 30},
                },
                {
                    "type": "compare",
                    "left": {"feature": "rsi"},
                    "op": "<",
                    "right": {"literal": 70},
                },
            ],
        },
        ctx,
    ) is True


def test_not_inverts(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "not",
            "operand": {
                "type": "compare",
                "left": {"feature": "rsi"},
                "op": ">",
                "right": {"literal": 70},
            },
        },
        ctx,
    ) is True


def test_if_then_returns_action_when_cond_true(ctx):
    e = Evaluator()
    out = e.eval_node(
        {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "today_ltp"},
                "op": ">",
                "right": {"feature": "sma_50"},
            },
            "then": {"type": "set_target_weight", "weight": 0.20},
            "else": {"type": "hold"},
        },
        ctx,
    )
    assert out == {"type": "set_target_weight", "weight": 0.20}


def test_if_else_path(ctx):
    e = Evaluator()
    out = e.eval_node(
        {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "rsi"},
                "op": ">",
                "right": {"literal": 90},
            },
            "then": {"type": "set_target_weight", "weight": 0.20},
            "else": {"type": "hold"},
        },
        ctx,
    )
    assert out == {"type": "hold"}


def test_unknown_feature_raises(ctx):
    e = Evaluator()
    with pytest.raises(KeyError, match="not_a_feature"):
        e.eval_node(
            {
                "type": "compare",
                "left": {"feature": "not_a_feature"},
                "op": "<",
                "right": {"literal": 100},
            },
            ctx,
        )


def test_hold_returns_self(ctx):
    e = Evaluator()
    out = e.eval_node({"type": "hold"}, ctx)
    assert out == {"type": "hold"}


def test_buy_returns_self(ctx):
    e = Evaluator()
    out = e.eval_node({"type": "buy", "qty": {"shares": 5}}, ctx)
    assert out == {"type": "buy", "qty": {"shares": 5}}
