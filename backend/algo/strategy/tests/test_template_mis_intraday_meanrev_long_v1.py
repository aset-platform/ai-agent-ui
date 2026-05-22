"""Sanity tests for mis_intraday_meanrev_long_v1.json."""

import json
from pathlib import Path

import pytest

from backend.algo.strategy.ast import parse_strategy

_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "templates"
    / "mis_intraday_meanrev_long_v1.json"
)


@pytest.fixture
def template_dict() -> dict:
    return json.loads(_TEMPLATE_PATH.read_text())


def test_template_parses_cleanly(template_dict):
    s = parse_strategy(template_dict)
    assert s.product == "MIS"
    assert s.schedule.interval == "15m"
    assert s.universe.filter.is_fno is True
    assert s.universe.filter.market == "india"


def test_template_entry_cutoff_is_pinned(template_dict):
    s = parse_strategy(template_dict)
    # We set 13:45 explicitly to override the 60-min default.
    assert s.entry_cutoff_time == "13:45 IST"


def test_template_uses_only_stable_bakeoff_features(template_dict):
    """Spec §4: the AST must only reference the 5 stable features
    from the 2026-05-21 bake-off."""
    stable = {
        "market_breadth_pct_above_sma200",
        "stress_prob",
        "minutes_since_open",
        "rsi_5",
        "gap_pct",
    }
    used = set()

    def _walk(node):
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
    extra = used - stable
    assert not extra, f"AST references non-stable features: {extra}"
    # All 5 stable features should be present in v1.
    missing = stable - used
    assert not missing, f"AST is missing stable features: {missing}"


def test_template_risk_caps_are_conservative(template_dict):
    s = parse_strategy(template_dict)
    assert s.risk.per_trade.stop_loss_pct == 2.0
    assert s.risk.portfolio.max_exposure_pct == 40.0
    assert s.risk.daily.max_loss_pct == 3.0
    assert s.risk.daily.max_open_positions == 8
