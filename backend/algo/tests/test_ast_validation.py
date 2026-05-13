# backend/algo/tests/test_ast_validation.py
"""Pydantic-validation tests for the strategy AST (Slice 4).

The grammar is closed: every node has a ``type`` discriminator
and a fixed payload shape. Unknown ``type`` values, unknown
feature keys, and bad arithmetic operands all raise at the
validator layer — never at runtime in the backtest engine.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.algo.strategy.ast import Strategy, parse_strategy


def _wrap(root: dict) -> dict:
    return {
        "id": str(uuid4()),
        "name": "Test strategy",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d", "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 10},
        "root": root,
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


# ---- Happy-path shapes ---------------------------------------------

def test_minimal_strategy_with_hold_root():
    s = parse_strategy(_wrap({"type": "hold"}))
    assert isinstance(s, Strategy)
    assert s.root.type == "hold"


def test_compare_two_features():
    root = {
        "type": "compare",
        "left": {"feature": "today_ltp"},
        "op": ">",
        "right": {"feature": "sma_50"},
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "compare"
    assert s.root.op == ">"


def test_compare_feature_to_literal():
    root = {
        "type": "compare",
        "left": {"feature": "rsi"},
        "op": "<",
        "right": {"literal": 70},
    }
    s = parse_strategy(_wrap(root))
    assert s.root.right.literal == 70


def test_and_operator_with_two_compares():
    root = {
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
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "and"
    assert len(s.root.operands) == 2


def test_or_with_three_operands():
    root = {
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
                "op": ">",
                "right": {"literal": 70},
            },
            {
                "type": "compare",
                "left": {"feature": "today_x_vol"},
                "op": ">=",
                "right": {"literal": 2},
            },
        ],
    }
    s = parse_strategy(_wrap(root))
    assert len(s.root.operands) == 3


def test_not_with_compare_inner():
    root = {
        "type": "not",
        "operand": {
            "type": "compare",
            "left": {"feature": "rsi"},
            "op": ">",
            "right": {"literal": 70},
        },
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "not"


def test_crossover_node():
    root = {
        "type": "crossover",
        "fast": {"feature": "sma_50"},
        "slow": {"feature": "sma_200"},
        "direction": "above",
    }
    s = parse_strategy(_wrap(root))
    assert s.root.direction == "above"


def test_between_node():
    root = {
        "type": "between",
        "value": {"feature": "rsi"},
        "low": {"literal": 30},
        "high": {"literal": 70},
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "between"


def test_if_then_else_with_select_top_n():
    root = {
        "type": "if",
        "cond": {
            "type": "compare",
            "left": {"feature": "today_ltp"},
            "op": ">",
            "right": {"feature": "sma_50"},
        },
        "then": {
            "type": "select_top_n",
            "n": 5,
            "rank_by": {"feature": "today_x_vol"},
            "rank_dir": "desc",
            "action": {"type": "set_target_weight", "weight": 0.20},
        },
        "else": {"type": "exit", "scope": "all_open"},
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "if"


def test_buy_action():
    root = {"type": "buy", "qty": {"shares": 10}}
    s = parse_strategy(_wrap(root))
    assert s.root.type == "buy"


def test_sell_action():
    root = {"type": "sell", "qty": {"all": True}}
    s = parse_strategy(_wrap(root))
    assert s.root.type == "sell"


def test_set_target_weight_action():
    root = {"type": "set_target_weight", "weight": 0.25}
    s = parse_strategy(_wrap(root))
    assert s.root.type == "set_target_weight"


def test_exit_all_open():
    root = {"type": "exit", "scope": "all_open"}
    s = parse_strategy(_wrap(root))
    assert s.root.scope == "all_open"


# ---- Validation: rejections ----------------------------------------

def test_unknown_node_type_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "magic_pony"}))


def test_unknown_feature_rejected():
    with pytest.raises(ValidationError, match="not_a_feature"):
        parse_strategy(_wrap({
            "type": "compare",
            "left": {"feature": "not_a_feature"},
            "op": ">",
            "right": {"literal": 0},
        }))


def test_compare_unknown_op_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({
            "type": "compare",
            "left": {"feature": "rsi"},
            "op": "bogus",
            "right": {"literal": 50},
        }))


def test_and_with_zero_operands_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "and", "operands": []}))


def test_select_top_n_with_zero_n_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({
            "type": "select_top_n", "n": 0,
            "rank_by": {"feature": "today_x_vol"},
            "rank_dir": "desc",
            "action": {"type": "set_target_weight", "weight": 0.10},
        }))


def test_set_target_weight_negative_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "set_target_weight", "weight": -0.5}))


def test_set_target_weight_above_one_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "set_target_weight", "weight": 1.5}))


def test_buy_negative_shares_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "buy", "qty": {"shares": -10}}))


def test_unknown_universe_scope_rejected():
    bad = _wrap({"type": "hold"})
    bad["universe"]["scope"] = "alien"
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_unknown_schedule_interval_rejected():
    bad = _wrap({"type": "hold"})
    bad["schedule"]["interval"] = "2d"
    with pytest.raises(ValidationError):
        parse_strategy(bad)


# ---- ASETPLTFRM-387 — MIS / intraday widening ---------------------

class TestCadenceAndProduct:
    """Backwards-compat + new cadence/product axes.

    Existing daily strategies in production have no ``product`` or
    ``square_off_time`` keys in their persisted AST JSON. The tests
    pin that they continue to parse cleanly and default to CNC.
    """

    def test_existing_daily_strategy_parses_without_product_key(self):
        # No ``product`` / ``square_off_time`` in payload — mirrors
        # every strategy created before this slice. Must default to
        # CNC + None and parse without error.
        s = parse_strategy(_wrap({"type": "hold"}))
        assert s.product == "CNC"
        assert s.square_off_time is None
        assert s.schedule.interval == "1d"

    def test_intraday_5m_cadence_accepted(self):
        payload = _wrap({"type": "hold"})
        payload["schedule"]["interval"] = "5m"
        s = parse_strategy(payload)
        assert s.schedule.interval == "5m"
        # Product still defaults to CNC for intraday + CNC scalpers.
        assert s.product == "CNC"

    def test_intraday_15m_cadence_accepted(self):
        payload = _wrap({"type": "hold"})
        payload["schedule"]["interval"] = "15m"
        s = parse_strategy(payload)
        assert s.schedule.interval == "15m"

    def test_intraday_1m_cadence_accepted(self):
        payload = _wrap({"type": "hold"})
        payload["schedule"]["interval"] = "1m"
        s = parse_strategy(payload)
        assert s.schedule.interval == "1m"

    def test_mis_with_5m_cadence_accepted(self):
        payload = _wrap({"type": "hold"})
        payload["schedule"]["interval"] = "5m"
        payload["product"] = "MIS"
        payload["square_off_time"] = "15:14 IST"
        s = parse_strategy(payload)
        assert s.product == "MIS"
        assert s.square_off_time == "15:14 IST"

    def test_mis_with_daily_cadence_rejected(self):
        # MIS + 1d is degenerate (open at close, force-squared by
        # broker seconds later). Model_validator must reject.
        payload = _wrap({"type": "hold"})
        payload["product"] = "MIS"
        # interval stays "1d" from _wrap default
        with pytest.raises(ValidationError):
            parse_strategy(payload)

    def test_unknown_product_rejected(self):
        payload = _wrap({"type": "hold"})
        payload["product"] = "NRML"  # CNC/MIS only in v1
        with pytest.raises(ValidationError):
            parse_strategy(payload)

    def test_3m_cadence_rejected(self):
        # Only 1d / 15m / 5m / 1m are valid intervals. 3m / 30m / 1h
        # are common-mistake values that must still raise — keep
        # the literal-set narrow until there's data to justify them.
        payload = _wrap({"type": "hold"})
        payload["schedule"]["interval"] = "3m"
        with pytest.raises(ValidationError):
            parse_strategy(payload)


def test_risk_per_trade_negative_sl_rejected():
    bad = _wrap({"type": "hold"})
    bad["risk"]["per_trade"]["stop_loss_pct"] = -1
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_risk_portfolio_exposure_above_100_rejected():
    bad = _wrap({"type": "hold"})
    bad["risk"]["portfolio"]["max_exposure_pct"] = 150
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_missing_root_rejected():
    bad = _wrap({"type": "hold"})
    del bad["root"]
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_extra_top_level_key_rejected():
    bad = _wrap({"type": "hold"})
    bad["extra_field"] = "boom"
    with pytest.raises(ValidationError):
        parse_strategy(bad)


# ---- Recursion / depth --------------------------------------------

def test_deeply_nested_and_or():
    inner = {
        "type": "compare",
        "left": {"feature": "rsi"},
        "op": "<",
        "right": {"literal": 30},
    }
    nested = inner
    for _ in range(8):
        nested = {"type": "not", "operand": nested}
    s = parse_strategy(_wrap(nested))
    # Walk back to find the inner compare
    n = s.root
    depth = 0
    while n.type == "not":
        n = n.operand
        depth += 1
    assert depth == 8
    assert n.type == "compare"


# ---- JSON-schema export -------------------------------------------

def test_strategy_emits_json_schema():
    schema = Strategy.model_json_schema()
    assert "Strategy" in schema.get("title", "")
    # Discriminator field present somewhere
    schema_str = str(schema)
    assert "type" in schema_str
    assert "compare" in schema_str
    assert "buy" in schema_str
