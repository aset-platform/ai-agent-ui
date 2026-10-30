"""Sanity tests for rsi2_connors_daily_v5.json."""
import json
from pathlib import Path

import pytest

from backend.algo.strategy.ast import parse_strategy

_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "templates"
    / "rsi2_connors_daily_v5.json"
)


@pytest.fixture
def td() -> dict:
    return json.loads(_TEMPLATE_PATH.read_text())


def test_template_parses(td):
    s = parse_strategy(td)
    assert s.product == "CNC"
    assert s.schedule.interval == "1d"


def test_v5_trailing_fields_present(td):
    s = parse_strategy(td)
    assert s.risk.per_trade.phase1_ratchet_trigger_pct == 2.0
    assert s.risk.per_trade.phase1_ratchet_new_stop_pct == 3.0
    assert s.risk.per_trade.trailing_trigger_pct == 5.0
    assert s.risk.per_trade.trailing_atr_multiplier == 1.5


def test_else_branch_is_hold(td):
    """v5 removes SMA5 exit; GTT owns all exits."""
    else_branch = td["root"]["else"]
    assert else_branch["type"] == "hold"


def test_entry_conditions_identical_to_v3(td):
    entry = td["root"]["cond"]["operands"]
    features = {op["left"]["feature"] for op in entry}
    assert features == {
        "rsi_2", "distance_from_sma200", "stress_prob",
        "nifty_distance_from_sma200_pct", "nifty_30d_return_pct",
    }


def test_nifty_regime_gate_uses_distance_pct(td):
    """v5 gates on the nifty_distance_from_sma200_pct regime band,
    not the old binary nifty_above_sma200 flag."""
    entry = td["root"]["cond"]["operands"]
    gate = next(
        op for op in entry
        if op["left"]["feature"] == "nifty_distance_from_sma200_pct"
    )
    assert gate["op"] == ">"
    assert gate["right"]["literal"] == -5
    assert not any(
        op["left"]["feature"] == "nifty_above_sma200" for op in entry
    )


def test_risk_fields(td):
    s = parse_strategy(td)
    assert s.risk.per_trade.stop_loss_pct == 5.0
    assert s.risk.per_trade.max_holding_days == 5
    assert s.risk.per_trade.cooldown_after_failed_exit_days == 7
