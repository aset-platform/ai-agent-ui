"""GET /v1/algo/performance/runs — endpoint smokes."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.performance import (
    create_performance_router,
)


def _row(strategy_id, status="completed", with_summary=True):
    sj = (
        {
            "total_pnl_inr": "5000",
            "total_pnl_pct": "5.0",
            "total_trades": 3,
            "win_rate_pct": "66.6",
            "max_drawdown_pct": "2.5",
        }
        if with_summary
        else None
    )
    return {
        "id": uuid4(),
        "strategy_id": strategy_id,
        "strategy_name": "Strat A",
        "mode": "backtest",
        "status": status,
        "period_start": date(2026, 4, 1),
        "period_end": date(2026, 4, 30),
        "started_at": datetime.now(timezone.utc),
        "completed_at": datetime.now(timezone.utc),
        "summary_json": sj,
    }


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(
        create_performance_router(), prefix="/v1",
    )
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t", role="superuser",
    )
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    factory = MagicMock()
    factory.__aenter__ = AsyncMock(return_value=fake_session)
    factory.__aexit__ = AsyncMock(return_value=None)
    # The route does ``async with factory() as session`` — make
    # calling factory() return the same async-context-manager mock.
    factory.return_value = factory
    factory_factory = MagicMock(return_value=factory)
    import backend.algo.routes.performance as perf_mod
    monkeypatch.setattr(
        perf_mod, "_get_session_factory", factory_factory,
    )
    return app, fake_session


def test_list_runs_empty(app):
    a, fake_session = app
    class _Res:
        def mappings(self): return self
        def all(self): return []
    fake_session.execute = AsyncMock(return_value=_Res())
    client = TestClient(a)
    r = client.get("/v1/algo/performance/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_runs_returns_decoded(app):
    a, fake_session = app
    sid = uuid4()
    items = [_row(sid, with_summary=True)]

    class _Res:
        def mappings(self): return self
        def all(self): return items
    fake_session.execute = AsyncMock(return_value=_Res())
    client = TestClient(a)
    r = client.get("/v1/algo/performance/runs")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["total_pnl_inr"] == "5000"
    assert rows[0]["strategy_id"] == str(sid)


def test_list_runs_handles_pending_with_no_summary(app):
    a, fake_session = app
    sid = uuid4()
    items = [_row(sid, status="pending", with_summary=False)]

    class _Res:
        def mappings(self): return self
        def all(self): return items
    fake_session.execute = AsyncMock(return_value=_Res())
    client = TestClient(a)
    r = client.get("/v1/algo/performance/runs")
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["total_pnl_inr"] is None
    assert rows[0]["status"] == "pending"
