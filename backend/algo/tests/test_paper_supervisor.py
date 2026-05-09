"""PaperSupervisor — start / stop / list_active."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.paper.supervisor import (
    PaperSupervisor, build_replay_source,
)
from backend.algo.strategy.ast import parse_strategy

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ticks_sample.jsonl"
)


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "buy small",
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
        "root": {"type": "buy", "qty": {"shares": 5}},
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
async def test_start_then_list_then_stop():
    sv = PaperSupervisor()
    user_id = uuid4()
    strategy = parse_strategy(_strategy_payload())
    from backend.algo.stream.sources import ReplayTickSource
    src = ReplayTickSource(_FIXTURE, pace="fast")

    with patch(
        "backend.algo.paper.runtime.flush_events",
    ):
        row = await sv.start_run(
            user_id=user_id,
            strategy=strategy,
            source=src,
            initial_capital_inr=Decimal("100000"),
        )
        assert row["status"] in {"running", "completed"}
        active = sv.list_active(user_id=user_id)
        assert len(active) == 1
        stopped = await sv.stop_run(
            user_id=user_id, strategy_id=strategy.id,
        )
        assert stopped is True
        assert sv.list_active(user_id=user_id) == []


@pytest.mark.asyncio
async def test_start_run_idempotent_collision_raises():
    sv = PaperSupervisor()
    user_id = uuid4()
    strategy = parse_strategy(_strategy_payload())
    from backend.algo.stream.sources import ReplayTickSource

    with patch(
        "backend.algo.paper.runtime.flush_events",
    ):
        await sv.start_run(
            user_id=user_id, strategy=strategy,
            source=ReplayTickSource(_FIXTURE, pace="fast"),
            initial_capital_inr=Decimal("100000"),
        )
        with pytest.raises(RuntimeError, match="already active"):
            await sv.start_run(
                user_id=user_id, strategy=strategy,
                source=ReplayTickSource(_FIXTURE, pace="fast"),
                initial_capital_inr=Decimal("100000"),
            )
        await sv.stop_run(
            user_id=user_id, strategy_id=strategy.id,
        )


@pytest.mark.asyncio
async def test_stop_run_returns_false_when_unknown():
    sv = PaperSupervisor()
    out = await sv.stop_run(user_id=uuid4(), strategy_id=uuid4())
    assert out is False


@pytest.mark.asyncio
async def test_list_active_filters_by_user():
    sv = PaperSupervisor()
    user_a, user_b = uuid4(), uuid4()
    s_a = parse_strategy(_strategy_payload())
    s_b = parse_strategy(_strategy_payload())
    from backend.algo.stream.sources import ReplayTickSource

    with patch(
        "backend.algo.paper.runtime.flush_events",
    ):
        await sv.start_run(
            user_id=user_a, strategy=s_a,
            source=ReplayTickSource(_FIXTURE, pace="fast"),
            initial_capital_inr=Decimal("100000"),
        )
        await sv.start_run(
            user_id=user_b, strategy=s_b,
            source=ReplayTickSource(_FIXTURE, pace="fast"),
            initial_capital_inr=Decimal("100000"),
        )
        active_a = sv.list_active(user_id=user_a)
        active_b = sv.list_active(user_id=user_b)
        assert len(active_a) == 1
        assert len(active_b) == 1
        assert active_a[0]["user_id"] == str(user_a)
        await sv.stop_run(user_id=user_a, strategy_id=s_a.id)
        await sv.stop_run(user_id=user_b, strategy_id=s_b.id)


def test_build_replay_source_rejects_path_traversal():
    with pytest.raises(ValueError, match="must live under"):
        build_replay_source("../../etc/passwd")


def test_build_replay_source_rejects_missing():
    with pytest.raises(FileNotFoundError):
        build_replay_source("does_not_exist.jsonl")


def test_build_replay_source_accepts_valid_fixture():
    src = build_replay_source("ticks_sample.jsonl")
    assert src is not None
