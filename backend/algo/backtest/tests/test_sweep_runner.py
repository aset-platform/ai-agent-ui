"""Integration tests for run_sweep_job."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.backtest.sweep import run_sweep_job
from backend.algo.backtest.sweep_types import SweepConfig


@pytest.mark.asyncio
async def test_sweep_runs_serial_and_aggregates(monkeypatch):
    """Orchestrator calls run_walkforward_job N times in
    order with each variant's mutated cooldown."""
    sweep_id = uuid4()
    wf_calls: list[dict] = []

    async def fake_wf(
        *, walkforward_run_id, user_id, config,
        strategy, universe,
    ):
        wf_calls.append({
            "wf_id": walkforward_run_id,
            "cd": (
                strategy.risk.per_trade
                .cooldown_after_failed_exit_days
            ),
        })

    monkeypatch.setattr(
        "backend.algo.backtest.sweep.run_walkforward_job",
        fake_wf,
    )

    cfg = SweepConfig(
        base_strategy_id=uuid4(),
        period_start=date(2025, 11, 23),
        period_end=date(2026, 5, 23),
        swept_field="cooldown_days",
        swept_values=[3, 7, 14],
    )

    fake_strategy = MagicMock()
    fake_strategy.risk.per_trade \
        .cooldown_after_failed_exit_days = 7

    fake_session = AsyncMock()
    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__ = AsyncMock(
        return_value=fake_session,
    )
    fake_factory.return_value.__aexit__ = AsyncMock(
        return_value=None,
    )

    with patch(
        "backend.algo.backtest.sweep._session_factory",
        return_value=fake_factory,
    ), patch(
        "backend.algo.backtest.sweep.BacktestRunsRepo",
    ) as RepoCls:
        repo = RepoCls.return_value
        repo.create_pending = AsyncMock(
            return_value=MagicMock(run_id=uuid4()),
        )
        repo.mark_running = AsyncMock()
        repo.mark_failed = AsyncMock()
        repo.list_children_of_sweep = AsyncMock(
            return_value=[],
        )
        await run_sweep_job(
            sweep_run_id=sweep_id,
            user_id=uuid4(),
            config=cfg,
            base_strategy=fake_strategy,
            universe=["TICKER1.NS"],
        )

    # 3 variants → 3 walkforward calls
    assert len(wf_calls) == 3
    # In order: 3, 7, 14
    assert [c["cd"] for c in wf_calls] == [3, 7, 14]


@pytest.mark.asyncio
async def test_sweep_continues_when_one_variant_fails(
    monkeypatch,
):
    """Variant 2 raises; sweep should record failure and
    still attempt variants 3 + 4."""
    call_count = {"n": 0}

    async def flaky_wf(
        *, walkforward_run_id, user_id, config,
        strategy, universe,
    ):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("synthetic engine crash")

    monkeypatch.setattr(
        "backend.algo.backtest.sweep.run_walkforward_job",
        flaky_wf,
    )

    cfg = SweepConfig(
        base_strategy_id=uuid4(),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 1),
        swept_field="cooldown_days",
        swept_values=[3, 7, 14, 21],
    )
    fake_strategy = MagicMock()
    fake_strategy.risk.per_trade \
        .cooldown_after_failed_exit_days = 7

    fake_session = AsyncMock()
    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__ = AsyncMock(
        return_value=fake_session,
    )
    fake_factory.return_value.__aexit__ = AsyncMock(
        return_value=None,
    )

    with patch(
        "backend.algo.backtest.sweep._session_factory",
        return_value=fake_factory,
    ), patch(
        "backend.algo.backtest.sweep.BacktestRunsRepo",
    ) as RepoCls:
        repo = RepoCls.return_value
        repo.create_pending = AsyncMock(
            return_value=MagicMock(run_id=uuid4()),
        )
        repo.mark_running = AsyncMock()
        repo.mark_completed = AsyncMock()
        repo.mark_failed = AsyncMock()
        repo.list_children_of_sweep = AsyncMock(
            return_value=[],
        )
        await run_sweep_job(
            sweep_run_id=uuid4(),
            user_id=uuid4(),
            config=cfg,
            base_strategy=fake_strategy,
            universe=["TICKER1.NS"],
        )

    # All 4 variants ATTEMPTED (failure was caught)
    assert call_count["n"] == 4
    # Variant 2's walkforward row marked failed
    assert repo.mark_failed.await_count >= 1
