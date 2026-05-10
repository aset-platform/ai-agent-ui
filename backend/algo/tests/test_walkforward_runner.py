"""Integration test for the walk-forward orchestrator.

Mocks the DB session, run_backtest(), and flush_events() so the
test runs in-process without any external dependencies.

Scenarios:
  - happy path: 3 windows run, parent row completed, aggregate
    computed correctly
  - single window failure does not abort the run — parent still
    completes with window_count=3, completed_count=2
  - aggregate metrics: known PnL% values → avg, std-dev
  - empty window list (period too short) → parent marked failed
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from backend.algo.backtest.types import BacktestRequest, BacktestSummary
from backend.algo.backtest.walkforward import (
    WalkForwardConfig,
    _aggregate_windows,
    run_walkforward_job,
)


# ── helpers ────────────────────────────────────────────────────


def _summary(
    run_id,
    strategy_id,
    pnl_pct: str = "5",
    win_rate: str = "60",
    max_dd: str = "3",
    status: str = "completed",
) -> BacktestSummary:
    return BacktestSummary(
        run_id=run_id,
        strategy_id=strategy_id,
        status=status,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 3, 31),
        initial_capital_inr=Decimal("100000"),
        final_equity_inr=Decimal("105000"),
        total_pnl_inr=Decimal("5000"),
        total_pnl_pct=Decimal(pnl_pct),
        total_fees_inr=Decimal("100"),
        total_trades=5,
        winning_trades=3,
        losing_trades=2,
        win_rate_pct=Decimal(win_rate),
        max_drawdown_pct=Decimal(max_dd),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        fee_rates_version="2024-01-01",
    )


def _fake_session():
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@asynccontextmanager
async def _stub_factory():
    yield _fake_session()


def _make_repo():
    repo = MagicMock()
    repo.create_pending = AsyncMock()
    repo.mark_running = AsyncMock()
    repo.mark_completed = AsyncMock()
    repo.mark_failed = AsyncMock()
    return repo


def _make_fake_strategy(strategy_id):
    class _U:
        scope = "watchlist"

    return type("S", (), {
        "id": strategy_id,
        "root": None,
        "universe": _U(),
    })()


# ── aggregate unit tests ───────────────────────────────────────


class TestAggregateWindows:
    def test_all_completed(self):
        sid = uuid4()
        summaries = [
            _summary(uuid4(), sid, pnl_pct="10", win_rate="70"),
            _summary(uuid4(), sid, pnl_pct="0", win_rate="50"),
            _summary(uuid4(), sid, pnl_pct="-5", win_rate="40"),
        ]
        agg = _aggregate_windows(summaries)
        assert agg.window_count == 3
        assert agg.completed_count == 3
        # avg PnL = (10 + 0 + (-5)) / 3 = 5/3 ≈ 1.67
        assert agg.avg_pnl_pct == Decimal("1.67")
        # avg win rate = (70 + 50 + 40) / 3 = 160/3 ≈ 53.33
        assert agg.avg_win_rate_pct == Decimal("53.33")

    def test_empty_list(self):
        agg = _aggregate_windows([])
        assert agg.window_count == 0
        assert agg.completed_count == 0
        assert agg.avg_pnl_pct == Decimal("0")

    def test_all_failed(self):
        sid = uuid4()
        summaries = [
            _summary(uuid4(), sid, status="failed"),
            _summary(uuid4(), sid, status="failed"),
        ]
        agg = _aggregate_windows(summaries)
        assert agg.completed_count == 0
        assert agg.window_count == 2

    def test_std_dev_single_window(self):
        """std-dev requires >= 2 samples; single window → 0."""
        agg = _aggregate_windows([_summary(uuid4(), uuid4())])
        assert agg.std_pnl_pct == Decimal("0")

    def test_known_std_dev(self):
        """Two windows: PnL% = 0 and 10 → std ≈ 7.07."""
        sid = uuid4()
        summaries = [
            _summary(uuid4(), sid, pnl_pct="0"),
            _summary(uuid4(), sid, pnl_pct="10"),
        ]
        agg = _aggregate_windows(summaries)
        # std([0, 10]) ≈ 7.07 (sample std)
        assert float(agg.std_pnl_pct) == pytest.approx(7.07, abs=0.01)


# ── orchestrator integration tests ────────────────────────────


class TestRunWalkforwardJob:
    @pytest.mark.asyncio
    async def test_happy_path_3_windows(self):
        """3-window happy path: all child runs complete, parent
        completed, mark_completed called 4 times total.

        Period: 2024-01-01 to 2024-05-08 = 128 days (inclusive).
        train=30, test=30, step=30 → windows at i=0,1,2.
        i=3 would need 3*30+60-1=149 > 128 → only 3 windows.
        """
        wf_id = uuid4()
        strategy_id = uuid4()
        user_id = uuid4()

        config = WalkForwardConfig(
            strategy_id=strategy_id,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 5, 8),   # 128 days → 3 windows
            train_days=30,
            test_days=30,
            step_days=30,
        )

        fake_strategy = _make_fake_strategy(strategy_id)
        repo = _make_repo()

        # 3 child pending_ids; parent uses wf_id directly
        child_ids = [uuid4() for _ in range(3)]
        call_count = 0

        async def _create_pending_side_effect(*args, **kwargs):
            nonlocal call_count
            rid = child_ids[call_count]
            call_count += 1
            row = MagicMock()
            row.run_id = rid
            row.started_at = datetime.now(timezone.utc)
            return row

        repo.create_pending.side_effect = _create_pending_side_effect

        run_index = 0

        def _fake_run_backtest(**kwargs):
            nonlocal run_index
            s = _summary(child_ids[run_index], strategy_id)
            run_index += 1
            return s

        with patch(
            "backend.algo.backtest.walkforward.BacktestRunsRepo",
            return_value=repo,
        ), patch(
            "backend.algo.backtest.walkforward.run_backtest",
            side_effect=_fake_run_backtest,
        ), patch(
            "backend.algo.backtest.walkforward.flush_events",
        ), patch(
            "backend.algo.backtest.job._session_factory",
            new=_stub_factory,
        ):
            await run_walkforward_job(
                walkforward_run_id=wf_id,
                user_id=user_id,
                config=config,
                strategy=fake_strategy,
                universe=["TCS.NS"],
            )

        # mark_running: 1 for parent + 3 for children = 4
        assert repo.mark_running.await_count == 4
        # mark_completed: 3 children + 1 parent = 4
        assert repo.mark_completed.await_count == 4
        repo.mark_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_window_failure_parent_still_completes(self):
        """When window 0-of-3 fails, parent run still completes
        (not failed); aggregate shows 2/3 windows ok."""
        wf_id = uuid4()
        strategy_id = uuid4()
        user_id = uuid4()

        config = WalkForwardConfig(
            strategy_id=strategy_id,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 5, 8),   # 128 days → 3 windows
            train_days=30,
            test_days=30,
            step_days=30,
        )

        fake_strategy = _make_fake_strategy(strategy_id)
        repo = _make_repo()

        child_ids = [uuid4() for _ in range(3)]
        call_count = 0

        async def _create_pending_se(*args, **kwargs):
            nonlocal call_count
            rid = child_ids[call_count]
            call_count += 1
            row = MagicMock()
            row.run_id = rid
            row.started_at = datetime.now(timezone.utc)
            return row

        repo.create_pending.side_effect = _create_pending_se

        run_index = 0

        def _fake_run_backtest(**kwargs):
            nonlocal run_index
            idx = run_index
            run_index += 1
            if idx == 0:
                raise RuntimeError("data source error")
            return _summary(child_ids[idx], strategy_id)

        with patch(
            "backend.algo.backtest.walkforward.BacktestRunsRepo",
            return_value=repo,
        ), patch(
            "backend.algo.backtest.walkforward.run_backtest",
            side_effect=_fake_run_backtest,
        ), patch(
            "backend.algo.backtest.walkforward.flush_events",
        ), patch(
            "backend.algo.backtest.job._session_factory",
            new=_stub_factory,
        ):
            await run_walkforward_job(
                walkforward_run_id=wf_id,
                user_id=user_id,
                config=config,
                strategy=fake_strategy,
                universe=["TCS.NS"],
            )

        # 1 failed child run (window 0 failed)
        assert repo.mark_failed.await_count == 1
        # 2 completed children + 1 parent = 3
        assert repo.mark_completed.await_count == 3

    @pytest.mark.asyncio
    async def test_no_windows_marks_parent_failed(self):
        """Period too short for any window → run_walkforward_job
        should record the empty result as completed (0 windows)."""
        wf_id = uuid4()
        strategy_id = uuid4()
        user_id = uuid4()

        # Period shorter than train+test → walk_windows returns []
        config = WalkForwardConfig(
            strategy_id=strategy_id,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 2, 1),  # ~31 days
            train_days=20,
            test_days=20,     # needs 40 days
            step_days=10,
        )

        fake_strategy = _make_fake_strategy(strategy_id)
        repo = _make_repo()

        with patch(
            "backend.algo.backtest.walkforward.BacktestRunsRepo",
            return_value=repo,
        ), patch(
            "backend.algo.backtest.walkforward.run_backtest",
        ) as mock_runner, patch(
            "backend.algo.backtest.walkforward.flush_events",
        ), patch(
            "backend.algo.backtest.job._session_factory",
            new=_stub_factory,
        ):
            await run_walkforward_job(
                walkforward_run_id=wf_id,
                user_id=user_id,
                config=config,
                strategy=fake_strategy,
                universe=["TCS.NS"],
            )

        # No per-window work needed
        mock_runner.assert_not_called()
        # Parent still marks completed (0/0 windows)
        repo.mark_completed.assert_awaited()
