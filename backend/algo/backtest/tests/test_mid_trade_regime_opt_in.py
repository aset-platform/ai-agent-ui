"""Opt-in guard for mid_trade_regime_check (ASETPLTFRM-435).

The v4 mid-trade regime exit is a research primitive that's
fundamentally anti-thesis for mean-reversion strategies (v4 triage
documents the negative result). To prevent accidental activation,
this test asserts:

1. The AST default for ``mid_trade_regime_check`` is ``None``
2. Every shipped template EXCEPT the explicitly research-flagged
   v4 has ``mid_trade_regime_check is None`` after parse
3. The pure monitor function short-circuits on ``None`` (zero
   cost, zero behavior change)

Adding the field to a new template should fail this test unless
the template name has the ``research`` prefix — forcing operators
to make an explicit choice about whether their strategy class is
compatible with regime exits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.algo.backtest.regime_exit_monitor import (
    check_regime_exit_triggers,
)
from backend.algo.strategy.ast import Strategy, parse_strategy


_TEMPLATES_DIR = (
    Path(__file__).parent.parent.parent / "strategy" / "templates"
)
_TEMPLATE_PATHS = sorted(_TEMPLATES_DIR.glob("*.json"))


def test_ast_default_is_none():
    """The Strategy schema default — independent of any template."""
    # Use a minimal valid Strategy dict that doesn't set the field.
    minimal = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "minimal",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"]},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "hold"},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }
    s = parse_strategy(minimal)
    assert s.mid_trade_regime_check is None


@pytest.mark.parametrize(
    "template_path", _TEMPLATE_PATHS,
    ids=lambda p: p.stem,
)
def test_only_research_flagged_templates_set_mid_trade_check(
    template_path: Path,
):
    """Production templates must NOT set mid_trade_regime_check.

    The field is opt-in research — accidentally setting it on a
    mean-reversion strategy (or any strategy whose thesis aligns
    with regime-hostile entry conditions) regresses every gate. A
    template that sets the field must signal its research-only
    status in its filename via the ``research`` keyword.
    """
    d = json.loads(template_path.read_text())
    strategy = parse_strategy(d)
    is_research = "research" in template_path.stem.lower()
    has_field_set = strategy.mid_trade_regime_check is not None
    if has_field_set and not is_research:
        pytest.fail(
            f"Template {template_path.stem!r} sets "
            f"mid_trade_regime_check but doesn't flag itself as "
            f"research-only. Either remove the field or rename "
            f"the file to include 'research' (e.g., "
            f"`{template_path.stem}_research_regime_exit.json`). "
            f"See docs/research/"
            f"2026-05-23-rsi2-connors-v4-experiments.md for why "
            f"mid-trade regime exit is anti-thesis for mean-"
            f"reversion strategies."
        )


def test_check_function_short_circuits_when_check_is_none():
    """Pure-function-level guarantee: when the field is None, the
    monitor returns [] without evaluating anything. This is the
    runner-level zero-cost path."""
    triggers = check_regime_exit_triggers(
        open_positions={"AAA.NS": {"qty": 100}},
        bar_date=__import__("datetime").date(2025, 1, 15),
        market_features={},  # would raise if eval ran
        regime_check=None,
    )
    assert triggers == []
