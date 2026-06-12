from decimal import Decimal
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
