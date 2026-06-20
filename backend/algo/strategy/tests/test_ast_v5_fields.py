"""Tests for v5 trailing-stop fields on RiskPerTrade."""
import copy
import json
from pathlib import Path

from backend.algo.strategy.ast import parse_strategy

_BASE = {
    "id": "00000000-0000-0000-0000-000000000099",
    "name": "test",
    "universe": {
        "type": "scope",
        "scope": "discovery",
        "filter": {"ticker_type": ["stock"], "market": "india"},
    },
    "schedule": {
        "type": "bar_close",
        "interval": "1d",
        "time": "15:25 IST",
    },
    "rebalance": {"type": "daily", "max_positions": 5},
    "product": "CNC",
    "root": {"type": "hold"},
    "risk": {
        "per_trade": {"stop_loss_pct": 5.0, "max_qty": 10000},
        "portfolio": {
            "max_exposure_pct": 100.0,
            "max_concentration_pct": 25.0,
        },
        "daily": {"max_loss_pct": 5.0, "max_open_positions": 5},
    },
}


def _with_trailing(extra: dict) -> dict:
    d = copy.deepcopy(_BASE)
    d["risk"]["per_trade"].update(extra)
    return d


def test_v5_fields_all_none_by_default():
    s = parse_strategy(_BASE)
    assert s.risk.per_trade.phase1_ratchet_trigger_pct is None
    assert s.risk.per_trade.phase1_ratchet_new_stop_pct is None
    assert s.risk.per_trade.trailing_trigger_pct is None
    assert s.risk.per_trade.trailing_atr_multiplier is None


def test_v5_fields_parse_correctly():
    d = _with_trailing({
        "phase1_ratchet_trigger_pct": 2.0,
        "phase1_ratchet_new_stop_pct": 3.0,
        "trailing_trigger_pct": 5.0,
        "trailing_atr_multiplier": 1.5,
    })
    s = parse_strategy(d)
    assert s.risk.per_trade.phase1_ratchet_trigger_pct == 2.0
    assert s.risk.per_trade.phase1_ratchet_new_stop_pct == 3.0
    assert s.risk.per_trade.trailing_trigger_pct == 5.0
    assert s.risk.per_trade.trailing_atr_multiplier == 1.5


def test_v3_template_still_parses_cleanly():
    p = (
        Path(__file__).parent.parent
        / "templates"
        / "rsi2_connors_daily_v3.json"
    )
    s = parse_strategy(json.loads(p.read_text()))
    assert s.risk.per_trade.trailing_trigger_pct is None
