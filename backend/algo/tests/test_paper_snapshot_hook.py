"""FE-5 hook test: paper runtime writes one
``stocks.trade_feature_snapshots`` row per executed fill.

Mirrors the backtest hook test — patches the snapshot writer
and replays a small tick fixture through PaperRuntime.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.paper.runtime import PaperRuntime
from backend.algo.strategy.ast import parse_strategy
from backend.algo.stream.sources import ReplayTickSource

_FIXTURE = Path(__file__).parent / "fixtures" / "ticks_sample.jsonl"


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "buy on every bar",
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
            "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {
            "type": "daily",
            "max_positions": 1,
        },
        "root": {"type": "buy", "qty": {"shares": 5}},
        "risk": {
            "per_trade": {
                "stop_loss_pct": 5,
                "max_qty": 100,
            },
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
async def test_paper_runtime_calls_snapshot_per_fill() -> None:
    """Every paper fill MUST result in a snapshot write.
    Each call carries ``mode='paper'`` + the right
    ticker / qty + a non-None features dict."""
    strategy = parse_strategy(_strategy_payload())
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
    )
    with (
        patch(
            "backend.algo.paper.runtime.flush_events",
        ),
        patch(
            "backend.algo.features.snapshots." "write_trade_feature_snapshot",
        ) as snap_mock,
    ):
        fills = await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )

    assert fills >= 1
    assert snap_mock.call_count == fills
    for call in snap_mock.call_args_list:
        kw = call.kwargs
        assert kw["mode"] == "paper"
        assert kw["side"] == "BUY"
        assert kw["qty"] == 5
        assert kw["features"] is not None
        assert kw["run_id"]
        assert kw["strategy_id"]


@pytest.mark.asyncio
async def test_paper_snapshot_failure_does_not_break_runtime() -> None:
    """Writer raise MUST be swallowed — paper runtime keeps
    booking fills + emitting events."""
    strategy = parse_strategy(_strategy_payload())
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
    )
    with (
        patch(
            "backend.algo.paper.runtime.flush_events",
        ),
        patch(
            "backend.algo.features.snapshots." "write_trade_feature_snapshot",
            side_effect=RuntimeError("simulated outage"),
        ),
    ):
        fills = await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )
    assert fills >= 1
