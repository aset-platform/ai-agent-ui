"""Async job wrapper — happy + error path."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.backtest.job import run_backtest_job
from backend.algo.backtest.types import (
    BacktestRequest, BacktestSummary,
)


def _summary(run_id, strategy_id) -> BacktestSummary:
    return BacktestSummary(
        run_id=run_id, strategy_id=strategy_id, status="completed",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        initial_capital_inr=Decimal("100000"),
        final_equity_inr=Decimal("105000"),
        total_pnl_inr=Decimal("5000"),
        total_pnl_pct=Decimal("5"),
        total_fees_inr=Decimal("100"),
        total_trades=0, winning_trades=0, losing_trades=0,
        win_rate_pct=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        fee_rates_version="2026-04-01",
    )


def _fake_session():
    session = MagicMock()
    session.commit = AsyncMock()
    return session


@asynccontextmanager
async def _stub_factory():
    yield _fake_session()


@pytest.mark.asyncio
async def test_job_happy_path_marks_completed():
    run_id = uuid4()
    strategy_id = uuid4()
    user_id = uuid4()
    request = BacktestRequest(
        strategy_id=strategy_id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )

    class _U:
        scope = "watchlist"

    fake_strategy = type("S", (), {
        "id": strategy_id, "root": None,
        "universe": _U(),
    })()

    repo = MagicMock()
    repo.mark_running = AsyncMock()
    repo.mark_completed = AsyncMock()
    repo.mark_failed = AsyncMock()

    with patch(
        "backend.algo.backtest.job.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.backtest.job.resolve_universe",
        new=AsyncMock(return_value=["TCS.NS"]),
    ), patch(
        "backend.algo.backtest.job.run_backtest",
        return_value=_summary(run_id, strategy_id),
    ), patch(
        "backend.algo.backtest.job.BacktestRunsRepo",
        return_value=repo,
    ), patch(
        "backend.algo.backtest.job._session_factory",
        new=_stub_factory,
    ):
        await run_backtest_job(
            run_id=run_id, user_id=user_id, request=request,
        )

    repo.mark_running.assert_awaited_once()
    repo.mark_completed.assert_awaited_once()
    repo.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_error_path_marks_failed_when_strategy_missing():
    run_id = uuid4()
    request = BacktestRequest(
        strategy_id=uuid4(),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    repo = MagicMock()
    repo.mark_running = AsyncMock()
    repo.mark_completed = AsyncMock()
    repo.mark_failed = AsyncMock()

    with patch(
        "backend.algo.backtest.job.get_strategy",
        new=AsyncMock(return_value=None),
    ), patch(
        "backend.algo.backtest.job.BacktestRunsRepo",
        return_value=repo,
    ), patch(
        "backend.algo.backtest.job._session_factory",
        new=_stub_factory,
    ):
        await run_backtest_job(
            run_id=run_id, user_id=uuid4(), request=request,
        )

    repo.mark_failed.assert_awaited_once()
    repo.mark_completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_swallows_runner_exception_and_marks_failed():
    run_id = uuid4()
    strategy_id = uuid4()
    request = BacktestRequest(
        strategy_id=strategy_id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )

    class _U:
        scope = "watchlist"

    fake_strategy = type("S", (), {
        "id": strategy_id, "root": None,
        "universe": _U(),
    })()

    repo = MagicMock()
    repo.mark_running = AsyncMock()
    repo.mark_completed = AsyncMock()
    repo.mark_failed = AsyncMock()

    with patch(
        "backend.algo.backtest.job.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.backtest.job.resolve_universe",
        new=AsyncMock(return_value=["TCS.NS"]),
    ), patch(
        "backend.algo.backtest.job.run_backtest",
        side_effect=RuntimeError("boom"),
    ), patch(
        "backend.algo.backtest.job.BacktestRunsRepo",
        return_value=repo,
    ), patch(
        "backend.algo.backtest.job._session_factory",
        new=_stub_factory,
    ):
        # MUST NOT raise.
        await run_backtest_job(
            run_id=run_id, user_id=uuid4(), request=request,
        )

    repo.mark_failed.assert_awaited()
    repo.mark_completed.assert_not_awaited()
