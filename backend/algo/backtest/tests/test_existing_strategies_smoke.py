"""Regression smoke: all existing templates still parse cleanly
after the OrderIntent.exit_reason / Fill.exit_reason field additions.

Does NOT assert metric values — those legitimately change when
stop-loss enforcement becomes active. Numerical impact captured
in docs/research/2026-05-23-stop-loss-enforcement-impact.md.
"""

import json
from pathlib import Path

import pytest

from backend.algo.strategy.ast import parse_strategy


_TEMPLATES_DIR = (
    Path(__file__).parent.parent.parent / "strategy" / "templates"
)
_TEMPLATES = sorted(
    p.stem for p in _TEMPLATES_DIR.glob("*.json")
)


@pytest.mark.parametrize("template_name", _TEMPLATES)
def test_template_parses_after_exit_reason_schema_change(
    template_name,
):
    template_path = _TEMPLATES_DIR / f"{template_name}.json"
    template_dict = json.loads(template_path.read_text())
    strategy = parse_strategy(template_dict)
    assert strategy.name
    assert strategy.risk.per_trade.stop_loss_pct >= 0
