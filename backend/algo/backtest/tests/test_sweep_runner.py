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


@pytest.mark.asyncio
async def test_sweep_aggregates_real_summary_shape(
    monkeypatch,
):
    """The aggregation phase reads each variant's
    summary_json from PG. Verify run_sweep_job handles the
    actual WalkForwardResult shape (window_summaries as
    list[dict], aggregate as nested dict) without crashing.

    Variant rows have realistic summary_json payloads with
    aggregate metrics + window_summaries (each with an
    equity_curve list of dicts)."""

    async def fake_wf(**kwargs):
        return None  # No-op; real summary set via mock below

    monkeypatch.setattr(
        "backend.algo.backtest.sweep.run_walkforward_job",
        fake_wf,
    )

    cfg = SweepConfig(
        base_strategy_id=uuid4(),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 1),
        swept_field="cooldown_days",
        swept_values=[7, 14],
    )
    fake_strategy = MagicMock()
    fake_strategy.risk.per_trade \
        .cooldown_after_failed_exit_days = 7

    # Realistic summary_json fixtures for 2 variants —
    # mirrors what walkforward.py persists: nested
    # `aggregate` dict + `window_summaries` of dicts
    # whose `equity_curve` is a list[dict].
    def _variant_summary(seed_id):
        return {
            "aggregate": {
                "avg_pnl_pct": "1.23",
                "avg_win_rate_pct": "55.0",
                "avg_max_drawdown_pct": "4.5",
                "dsr": "0.5",
            },
            "window_summaries": [
                {
                    "test_start": "2025-02-01",
                    "test_end": "2025-02-28",
                    "total_pnl_pct": "1.0",
                    "win_rate_pct": "60.0",
                    "max_drawdown_pct": "3.0",
                    "total_trades": 10,
                    "equity_curve": [
                        {
                            "bar_date": "2025-02-01",
                            "equity_inr": "100000",
                        },
                        {
                            "bar_date": "2025-02-15",
                            "equity_inr": "101000",
                        },
                        {
                            "bar_date": "2025-02-28",
                            "equity_inr": "101000",
                        },
                    ],
                },
                {
                    "test_start": "2025-03-01",
                    "test_end": "2025-03-31",
                    "total_pnl_pct": "0.5",
                    "win_rate_pct": "50.0",
                    "max_drawdown_pct": "2.5",
                    "total_trades": 8,
                    "equity_curve": [
                        {
                            "bar_date": "2025-03-01",
                            "equity_inr": "100000",
                        },
                        {
                            "bar_date": "2025-03-15",
                            "equity_inr": "100500",
                        },
                        {
                            "bar_date": "2025-03-31",
                            "equity_inr": "100500",
                        },
                    ],
                },
            ],
        }

    fake_session = AsyncMock()
    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__ = AsyncMock(
        return_value=fake_session,
    )
    fake_factory.return_value.__aexit__ = AsyncMock(
        return_value=None,
    )

    # Track variant IDs we created
    variant_ids = [uuid4(), uuid4()]
    create_pending_call = {"n": 0}

    async def fake_create_pending(*args, **kwargs):
        idx = create_pending_call["n"]
        create_pending_call["n"] += 1
        return MagicMock(run_id=variant_ids[idx])

    with patch(
        "backend.algo.backtest.sweep._session_factory",
        return_value=fake_factory,
    ), patch(
        "backend.algo.backtest.sweep.BacktestRunsRepo",
    ) as RepoCls:
        repo = RepoCls.return_value
        repo.create_pending = AsyncMock(
            side_effect=fake_create_pending,
        )
        repo.mark_running = AsyncMock()
        repo.mark_failed = AsyncMock()
        repo.list_children_of_sweep = AsyncMock(
            return_value=[
                {
                    "id": variant_ids[0],
                    "status": "completed",
                    "summary_json": _variant_summary(0),
                    "error_text": None,
                },
                {
                    "id": variant_ids[1],
                    "status": "completed",
                    "summary_json": _variant_summary(1),
                    "error_text": None,
                },
            ],
        )

        # Should NOT raise. Should complete the sweep.
        await run_sweep_job(
            sweep_run_id=uuid4(),
            user_id=uuid4(),
            config=cfg,
            base_strategy=fake_strategy,
            universe=["TICKER1.NS"],
        )

    # No mark_failed call (both variants completed)
    assert repo.mark_failed.await_count == 0
    # Final UPDATE happened (session.execute called
    # with the UPDATE that writes summary_json)
    update_calls = [
        c for c in fake_session.execute.call_args_list
        if "UPDATE" in str(c[0][0])
    ]
    assert len(update_calls) >= 1
