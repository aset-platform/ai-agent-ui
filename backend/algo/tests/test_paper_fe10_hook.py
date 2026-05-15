"""FE-10 hook test: paper runtime calls the live-emitter once
per closed bar when the strategy runs an intraday cadence.

Mirrors the FE-5 paper snapshot hook test — patches the emitter
and replays a small tick fixture through PaperRuntime.

Daily-cadence strategies MUST NOT trigger the emitter (FE-3 daily
compute owns daily features); we cover that as a separate test.
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


def _strategy_payload(interval: str = "1d") -> dict:
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
            "interval": interval,
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
async def test_paper_runtime_does_not_call_fe10_emitter_for_daily() -> None:
    """``schedule.interval='1d'`` strategy → emitter NEVER called.

    Daily features are FE-3's responsibility; the bar-close
    handler must short-circuit on the cadence guard BEFORE
    invoking the emitter. (The emitter itself also no-ops on
    ``'1d'`` — this test pins the runtime-side guard so a
    regression in the emitter's daily handling can't sneak
    daily-cadence writes through.)
    """
    strategy = parse_strategy(_strategy_payload(interval="1d"))
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
        ),
        patch(
            "backend.algo.features.live_emitter." "emit_features_for_bar",
        ) as emit_mock,
    ):
        fills = await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )
    assert fills >= 0  # fixture may not produce fills with intraday
    emit_mock.assert_not_called()


@pytest.mark.asyncio
async def test_paper_runtime_calls_fe10_emitter_on_intraday_bar() -> None:
    """Intraday cadence → emitter called per closed bar with
    ``mode='paper'``, the right ticker, the cumulative history,
    and the matching ``interval_sec``.
    """
    strategy = parse_strategy(_strategy_payload(interval="15m"))
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
        ),
        patch(
            "backend.algo.features.live_emitter." "emit_features_for_bar",
        ) as emit_mock,
    ):
        await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )

    assert (
        emit_mock.call_count >= 1
    ), "expected at least one FE-10 emission for intraday cadence"
    for call in emit_mock.call_args_list:
        kw = call.kwargs
        assert kw["mode"] == "paper"
        assert kw["cadence_interval"] == "15m"
        assert kw["interval_sec"] == 900
        assert kw["ticker"]
        # history is the cumulative bar series for the ticker;
        # at minimum the bar that just closed is its last entry.
        assert isinstance(kw["history"], list)
        assert kw["history"]
