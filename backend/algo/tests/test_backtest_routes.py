"""Endpoint smokes for /v1/algo/backtest/{run,runs/{id},runs}."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.types import (
    BacktestRun, BacktestSummary,
)
from backend.algo.routes.backtest import create_backtest_router


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


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_backtest_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t", role="superuser",
    )
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    factory = MagicMock()
    factory.__aenter__ = AsyncMock(return_value=fake_session)
    factory.__aexit__ = AsyncMock(return_value=None)
    factory_factory = MagicMock(return_value=factory)
    import backend.algo.routes.backtest as bt
    monkeypatch.setattr(bt, "_get_session_factory", factory_factory)
    return app


def test_post_run_returns_202(app):
    strategy_id = uuid4()
    pending_run = BacktestRun(
        run_id=uuid4(), strategy_id=strategy_id, status="pending",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        started_at=datetime.now(timezone.utc),
    )
    with patch(
        "backend.algo.routes.backtest.BacktestRunsRepo"
    ) as cls:
        cls.return_value.create_pending = AsyncMock(
            return_value=pending_run,
        )
        client = TestClient(app)
        r = client.post(
            "/v1/algo/backtest/run",
            json={
                "strategy_id": str(strategy_id),
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "initial_capital_inr": "100000.00",
            },
        )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["run_id"] == str(pending_run.run_id)


def test_get_run_404(app):
    with patch(
        "backend.algo.routes.backtest.BacktestRunsRepo"
    ) as cls:
        cls.return_value.get_by_id = AsyncMock(return_value=None)
        client = TestClient(app)
        r = client.get(f"/v1/algo/backtest/runs/{uuid4()}")
    assert r.status_code == 404


def test_get_run_returns_summary(app):
    strategy_id = uuid4()
    summary = _summary(uuid4(), strategy_id)
    with patch(
        "backend.algo.routes.backtest.BacktestRunsRepo"
    ) as cls:
        cls.return_value.get_by_id = AsyncMock(return_value=summary)
        client = TestClient(app)
        r = client.get(f"/v1/algo/backtest/runs/{summary.run_id}")
    assert r.status_code == 200
    assert r.json()["total_pnl_inr"] == "5000"


def test_list_runs_empty(app):
    with patch(
        "backend.algo.routes.backtest.BacktestRunsRepo"
    ) as cls:
        cls.return_value.list_by_user = AsyncMock(return_value=[])
        client = TestClient(app)
        r = client.get("/v1/algo/backtest/runs")
    assert r.status_code == 200
    assert r.json() == []
