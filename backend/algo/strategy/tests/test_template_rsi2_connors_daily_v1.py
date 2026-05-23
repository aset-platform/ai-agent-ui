"""Sanity tests for rsi2_connors_daily_v1.json."""

import json
from pathlib import Path

import pytest

from backend.algo.strategy.ast import parse_strategy

_TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "templates"
    / "rsi2_connors_daily_v1.json"
)


@pytest.fixture
def template_dict() -> dict:
    return json.loads(_TEMPLATE_PATH.read_text())


def test_template_parses_cleanly(template_dict):
    s = parse_strategy(template_dict)
    assert s.product == "CNC"
    assert s.schedule.interval == "1d"
    assert s.universe.filter.market == "india"
    assert s.universe.filter.ticker_type == ["stock"]


def test_template_entry_thresholds_match_spec(template_dict):
    """Entry condition: rsi_2<=5, distance_from_sma200>0, stress_prob<0.5."""
    entry = template_dict["root"]["cond"]["operands"]
    thresholds = {
        op["left"]["feature"]: (op["op"], op["right"]["literal"])
        for op in entry
    }
    assert thresholds["rsi_2"] == ("<=", 5)
    assert thresholds["distance_from_sma200"] == (">", 0.0)
    assert thresholds["stress_prob"] == ("<", 0.5)


def test_template_exit_is_distance_from_sma5_cross_up(template_dict):
    """Exit branch fires when distance_from_sma5 > 0."""
    exit_branch = template_dict["root"]["else"]
    assert exit_branch["type"] == "if"
    cond = exit_branch["cond"]
    assert cond["left"]["feature"] == "distance_from_sma5"
    assert cond["op"] == ">"
    assert cond["right"]["literal"] == 0.0
    assert exit_branch["then"]["type"] == "exit"
    assert exit_branch["else"]["type"] == "hold"


def test_template_risk_caps_are_conservative(template_dict):
    s = parse_strategy(template_dict)
    assert s.risk.per_trade.stop_loss_pct == 3.0
    assert s.risk.portfolio.max_exposure_pct == 100.0
    assert s.risk.portfolio.max_concentration_pct == 25.0
    assert s.risk.daily.max_loss_pct == 5.0
    assert s.risk.daily.max_open_positions == 5


def test_template_uses_only_expected_features(template_dict):
    """The AST references exactly the 4 features in the spec:
    rsi_2, distance_from_sma200, stress_prob, distance_from_sma5."""
    expected = {
        "rsi_2",
        "distance_from_sma200",
        "stress_prob",
        "distance_from_sma5",
    }
    used: set[str] = set()

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "feature" and isinstance(v, str):
                    used.add(v)
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(template_dict["root"])
    extra = used - expected
    assert not extra, f"AST references unexpected features: {extra}"
    missing = expected - used
    assert not missing, f"AST missing expected features: {missing}"
