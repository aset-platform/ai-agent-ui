"""Endpoint smokes for /v1/algo/backtest/{run,runs/{id}}."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.types import BacktestSummary
from backend.algo.routes.backtest import create_backtest_router


def _make_summary(strategy_id, run_id=None) -> BacktestSummary:
    return BacktestSummary(
        run_id=run_id or uuid4(),
        strategy_id=strategy_id,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 30),
        initial_capital_inr=Decimal("100000.00"),
        final_equity_inr=Decimal("105000.00"),
        total_pnl_inr=Decimal("5000.00"),
        total_pnl_pct=Decimal("5.00"),
        total_fees_inr=Decimal("100.00"),
        total_trades=2,
        winning_trades=1,
        losing_trades=1,
        win_rate_pct=Decimal("50.00"),
        max_drawdown_pct=Decimal("3.20"),
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
        email="t@t",
        role="superuser",
    )

    class _Stub:
        async def execute(self, *a, **kw):
            class _Res:
                def mappings(self):
                    return self
                def first(self):
                    return None
            return _Res()
        async def commit(self):
            return None

    class _Factory:
        def __call__(self):
            return self
        async def __aenter__(self):
            return _Stub()
        async def __aexit__(self, *args):
            return None

    import backend.algo.routes.backtest as bt
    monkeypatch.setattr(bt, "_get_session_factory", lambda: _Factory())
    return app


def test_run_returns_404_when_strategy_missing(app):
    client = TestClient(app)
    r = client.post(
        "/v1/algo/backtest/run",
        json={
            "strategy_id": str(uuid4()),
            "period_start": "2024-01-01",
            "period_end": "2024-01-30",
            "initial_capital_inr": "100000.00",
        },
    )
    assert r.status_code == 404


def test_run_succeeds_when_strategy_present(app):
    strategy_id = uuid4()
    summary = _make_summary(strategy_id)
    fake_strategy = type("S", (), {"id": strategy_id, "root": None})()
    with patch(
        "backend.algo.routes.backtest.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.routes.backtest.run_backtest",
        return_value=summary,
    ):
        client = TestClient(app)
        r = client.post(
            "/v1/algo/backtest/run",
            json={
                "strategy_id": str(strategy_id),
                "period_start": "2024-01-01",
                "period_end": "2024-01-30",
                "initial_capital_inr": "100000.00",
            },
        )
    assert r.status_code == 200
    assert r.json()["run_id"] == str(summary.run_id)


def test_get_runs_404_for_unknown(app):
    client = TestClient(app)
    r = client.get(f"/v1/algo/backtest/runs/{uuid4()}")
    assert r.status_code == 404


def test_get_runs_returns_persisted_summary(app):
    strategy_id = uuid4()
    summary = _make_summary(strategy_id)
    fake_strategy = type("S", (), {"id": strategy_id, "root": None})()
    with patch(
        "backend.algo.routes.backtest.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.routes.backtest.run_backtest",
        return_value=summary,
    ):
        client = TestClient(app)
        client.post(
            "/v1/algo/backtest/run",
            json={
                "strategy_id": str(strategy_id),
                "period_start": "2024-01-01",
                "period_end": "2024-01-30",
                "initial_capital_inr": "100000.00",
            },
        )
        r = client.get(f"/v1/algo/backtest/runs/{summary.run_id}")
    assert r.status_code == 200
    assert r.json()["total_pnl_inr"] == "5000.00"


def test_run_400_on_inverted_period(app):
    strategy_id = uuid4()
    fake_strategy = type("S", (), {"id": strategy_id, "root": None})()
    with patch(
        "backend.algo.routes.backtest.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.routes.backtest.run_backtest",
        side_effect=ValueError("period_start after period_end"),
    ):
        client = TestClient(app)
        r = client.post(
            "/v1/algo/backtest/run",
            json={
                "strategy_id": str(strategy_id),
                "period_start": "2024-06-01",
                "period_end": "2024-01-01",
                "initial_capital_inr": "100000.00",
            },
        )
    assert r.status_code == 400
