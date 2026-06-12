from datetime import date
from decimal import Decimal

import backend.algo.paper.fixture_builder as fb
from backend.algo.paper.fixture_builder import _entry_fires

_COND = {"type": "and", "operands": [
    {"type": "compare", "op": "<=", "left": {"feature": "rsi_2"},
     "right": {"literal": 5}},
    {"type": "compare", "op": ">", "left": {"feature": "distance_from_sma200"},
     "right": {"literal": 0}},
    {"type": "compare", "op": "<", "left": {"feature": "stress_prob"},
     "right": {"literal": 0.5}},
    {"type": "compare", "op": ">=", "left": {"feature": "nifty_above_sma200"},
     "right": {"literal": 1}},
    {"type": "compare", "op": ">", "left": {"feature": "nifty_30d_return_pct"},
     "right": {"literal": -5}},
]}


def _f(rsi=3, dist=5, stress=0.2, nabove=1, n30=2.0):
    return {"rsi_2": Decimal(str(rsi)),
            "distance_from_sma200": Decimal(str(dist)),
            "stress_prob": Decimal(str(stress)),
            "nifty_above_sma200": Decimal(str(nabove)),
            "nifty_30d_return_pct": Decimal(str(n30))}


def test_entry_fires_all_gates_pass():
    assert _entry_fires(_COND, _f()) is True


def test_entry_fires_false_rsi_high():
    assert _entry_fires(_COND, _f(rsi=40)) is False


def test_entry_fires_false_missing_feature():
    assert _entry_fires(_COND, {}) is False  # KeyError swallowed


def test_scan_picks_only_firing_dates(monkeypatch):
    d1, d2 = date(2026, 6, 2), date(2026, 6, 3)
    monkeypatch.setattr(
        fb, "_assembled_features_by_date",
        lambda ticker, start, end: {d1: _f(rsi=3), d2: _f(rsi=40)},
    )
    out = fb._scan_trigger_dates(
        "TCS.NS", _COND, date(2026, 6, 1), date(2026, 6, 4),
        max_dates=2,
    )
    assert out == [d1]


def test_scan_respects_max_dates(monkeypatch):
    d1, d2, d3 = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)
    monkeypatch.setattr(
        fb, "_assembled_features_by_date",
        lambda t, s, e: {d1: _f(), d2: _f(), d3: _f()},
    )
    out = fb._scan_trigger_dates(
        "TCS.NS", _COND, d1, d3, max_dates=2,
    )
    assert out == [d2, d3]  # most-recent 2
