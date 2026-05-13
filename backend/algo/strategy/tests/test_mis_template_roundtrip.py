"""ASETPLTFRM-391 — MIS RSI scalper template round-trip.

Mirrors the shape of ``frontend/components/algo-trading/builder/
templates.ts::mis_rsi_scalper`` and asserts that the backend AST
validator accepts the exact payload the frontend would POST.

If this test fails after a templates.ts edit, the two sides have
drifted — sync them up.
"""
from __future__ import annotations

from uuid import uuid4

from backend.algo.strategy.ast import parse_strategy


def _mis_rsi_scalper_payload() -> dict:
    """The MIS template the Builder ships under key="mis_rsi_scalper".

    Kept in sync with templates.ts manually. The shape is asserted
    rather than imported because the frontend template lives in TS
    and is intentionally serialised through the wire format to the
    backend.
    """
    return {
        "id": str(uuid4()),
        "name": "MIS RSI Scalper (5m)",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {
                "ticker_type": ["stock"],
                "market": "india",
            },
        },
        "schedule": {
            "type": "bar_close",
            "interval": "5m",
            "time": "15:14 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 3},
        "risk": {
            "per_trade": {"stop_loss_pct": 1.0, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 50,
                "max_concentration_pct": 20,
            },
            "daily": {
                "max_loss_pct": 1.5,
                "max_open_positions": 3,
            },
        },
        "product": "MIS",
        "square_off_time": "15:14 IST",
        "root": {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "rsi"},
                "op": ">",
                "right": {"literal": 70},
            },
            "then": {"type": "exit", "scope": "this_symbol"},
            "else": {
                "type": "if",
                "cond": {
                    "type": "compare",
                    "left": {"feature": "rsi"},
                    "op": "<",
                    "right": {"literal": 30},
                },
                "then": {
                    "type": "set_target_weight",
                    "weight": 0.20,
                },
                "else": {"type": "hold"},
            },
        },
    }


def test_mis_rsi_scalper_template_parses_cleanly():
    """The Builder's POSTed payload must round-trip through the AST
    validator without raising and surface the MIS / intraday fields
    intact for downstream runtime + safety code.
    """
    s = parse_strategy(_mis_rsi_scalper_payload())
    assert s.product == "MIS"
    assert s.square_off_time == "15:14 IST"
    assert s.schedule.interval == "5m"
    assert s.rebalance.max_positions == 3


def test_mis_rsi_scalper_has_nested_rsi_logic():
    """The scalper's branching shape (exit on >70, target_weight on
    <30, hold otherwise) is the heart of the template; pin it so a
    typo in templates.ts doesn't slip through.
    """
    s = parse_strategy(_mis_rsi_scalper_payload())
    # Outer if: RSI > 70 → exit
    outer = s.root
    assert outer.type == "if"
    assert outer.cond.op == ">"
    assert outer.cond.right.literal == 70
    assert outer.then.type == "exit"
    # Inner else-if: RSI < 30 → set_target_weight 0.20
    inner = outer.else_
    assert inner.type == "if"
    assert inner.cond.op == "<"
    assert inner.cond.right.literal == 30
    assert inner.then.type == "set_target_weight"
    assert float(inner.then.weight) == 0.20
    assert inner.else_.type == "hold"
