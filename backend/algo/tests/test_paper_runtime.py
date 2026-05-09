"""End-to-end paper runtime over a replay tick fixture."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.paper.runtime import PaperRuntime
from backend.algo.stream.sources import ReplayTickSource
from backend.algo.strategy.ast import parse_strategy

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ticks_sample.jsonl"
)


def _strategy_payload(buy_qty: int = 5) -> dict:
    return {
        "id": str(uuid4()),
        "name": "buy on every bar",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {
                "ticker_type": ["stock"], "market": "india",
            },
        },
        "schedule": {
            "type": "bar_close", "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": buy_qty}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {
                "max_loss_pct": 2,
                "max_open_positions": 10,
            },
        },
    }


@pytest.mark.asyncio
async def test_runtime_emits_fills_for_buy_strategy():
    strategy = parse_strategy(_strategy_payload(buy_qty=5))
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
    )
    source = ReplayTickSource(_FIXTURE, pace="fast")
    with patch(
        "backend.algo.paper.runtime.flush_events",
    ) as flush:
        fills = await runtime.run(source)
    assert fills >= 1
    flush.assert_called_once()
    rows = flush.call_args.args[0]
    assert any(r["type"] == "signal_generated" for r in rows)
    assert any(r["type"] == "order_filled" for r in rows)


@pytest.mark.asyncio
async def test_runtime_kill_switch_blocks_all_signals():
    strategy = parse_strategy(_strategy_payload())
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
        kill_switch_active=True,
    )
    with patch(
        "backend.algo.paper.runtime.flush_events",
    ) as flush:
        fills = await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )
    assert fills == 0
    rows = flush.call_args.args[0]
    rejected = [r for r in rows if r["type"] == "signal_rejected"]
    assert len(rejected) >= 1
    payloads = [json.loads(r["payload_json"]) for r in rejected]
    assert any(p.get("reason") == "kill_switch" for p in payloads)


@pytest.mark.asyncio
async def test_runtime_max_qty_rejection_emits_signal_rejected():
    payload = _strategy_payload(buy_qty=200)  # > max_qty=100
    strategy = parse_strategy(payload)
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
    )
    with patch(
        "backend.algo.paper.runtime.flush_events",
    ) as flush:
        fills = await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )
    assert fills == 0
    rows = flush.call_args.args[0]
    rejected = [
        json.loads(r["payload_json"])
        for r in rows
        if r["type"] == "signal_rejected"
    ]
    assert all(p["reason"] == "max_qty" for p in rejected)


@pytest.mark.asyncio
async def test_runtime_skips_bar_when_strategy_needs_missing_feature():
    """Strategy referencing sma_50 against the 30-tick replay
    fixture (only ~3 bars) should not crash — missing-feature
    KeyError must be caught + bar skipped, same as backtest."""
    payload = _strategy_payload()
    payload["root"] = {
        "type": "if",
        "cond": {
            "type": "compare",
            "left": {"feature": "today_ltp"},
            "op": ">",
            "right": {"feature": "sma_50"},
        },
        "then": {"type": "buy", "qty": {"shares": 1}},
        "else": {"type": "hold"},
    }
    strategy = parse_strategy(payload)
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
    )
    with patch(
        "backend.algo.paper.runtime.flush_events",
    ) as flush:
        fills = await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )
    # No crash, no fills (sma_50 never available in 3 bars).
    assert fills == 0
    # flush_events may not be called if the events list is
    # empty (no fills, no rejections — every bar quietly skipped).
    if flush.call_args is not None:
        rows = flush.call_args.args[0]
        # Specifically verify NO fills slipped through.
        filled = [r for r in rows if r["type"] == "order_filled"]
        assert len(filled) == 0
