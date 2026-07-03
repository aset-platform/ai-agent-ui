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


def test_summary_requires_valid_mode(app):
    a, _ = app
    client = TestClient(a)
    r = client.get("/v1/algo/performance/summary?mode=bogus")
    assert r.status_code == 422


def test_summary_backtest_mode_aggregates_trade_list(app, monkeypatch):
    a, fake_session = app
    sid = uuid4()
    row = {
        "strategy_id": sid,
        "strategy_name": "RSI(2) v5",
        "summary_json": {
            "max_drawdown_pct": "4.2",
            "trade_list": [
                {
                    "ticker": "ITC", "realised_pnl_inr": 500,
                    "closed_at": "2026-06-10",
                    "qty": 10, "avg_price": 300.0,
                    "fill_price": 350.0,
                },
                {
                    "ticker": "TCS", "realised_pnl_inr": -200,
                    "closed_at": "2026-06-12",
                    "qty": 2, "avg_price": 3600.0,
                    "fill_price": 3500.0,
                },
            ],
        },
    }

    class _Res:
        def mappings(self):
            return self

        def all(self):
            return [row]
    fake_session.execute = AsyncMock(return_value=_Res())

    import backend.algo.routes.performance as perf_mod
    monkeypatch.setattr(
        perf_mod, "get_cache",
        lambda: MagicMock(get=lambda k: None, set=lambda *a, **k: None),
    )

    client = TestClient(a)
    r = client.get(
        f"/v1/algo/performance/summary?mode=backtest"
        f"&strategy_id={sid}&lookback=all",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "backtest"
    assert len(body["strategies"]) == 1
    s = body["strategies"][0]
    assert s["total_trades"] == 2
    assert s["wins"] == 1
    assert s["losses"] == 1
    assert s["win_rate_pct"] == 50.0
    assert s["biggest_win"]["ticker"] == "ITC"
    assert s["biggest_loss"]["ticker"] == "TCS"
    assert s["max_drawdown_pct"] == 4.2
    assert s["total_invested_inr"] == 10200.0
    assert s["total_gain_inr"] == 10500.0
    assert s["profit_pct"] == 2.94
    assert "profit_factor" not in s
    assert len(body["trades"]) == 2


def test_summary_live_mode_reads_closed_trades(app, monkeypatch):
    a, fake_session = app
    sid = uuid4()
    row = {
        "strategy_id": sid,
        "strategy_name": "RSI(2) v5",
        "ticker": "SHAILY",
        "realised_pnl_inr": -890.0,
        "closed_at": date(2026, 6, 25),
        "qty": 3,
        "avg_price": 1200.0,
        "fill_price": 903.33,
        "opened_at": date(2026, 6, 20),
        "return_pct": -24.7,
        "exit_reason": "stop_loss",
        "opened_at_ts_ns": None,
        "closed_at_ts_ns": None,
    }

    class _Res:
        def mappings(self):
            return self

        def all(self):
            return [row]
    fake_session.execute = AsyncMock(return_value=_Res())

    import backend.algo.routes.performance as perf_mod
    monkeypatch.setattr(
        perf_mod, "get_cache",
        lambda: MagicMock(get=lambda k: None, set=lambda *a, **k: None),
    )

    client = TestClient(a)
    r = client.get(
        f"/v1/algo/performance/summary?mode=live"
        f"&strategy_id={sid}&lookback=30d",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["strategies"][0]
    assert s["total_trades"] == 1
    assert s["losses"] == 1
    assert s["max_drawdown_pct"] is None
    assert s["total_invested_inr"] == 3600.0
    assert s["total_gain_inr"] == 2709.99
    assert s["profit_pct"] == -24.72
    assert body["trades"][0]["ticker"] == "SHAILY"
    assert body["trades"][0]["holding_days"] == 5


def test_summary_no_strategy_id_omits_trades_list(app, monkeypatch):
    a, fake_session = app

    class _Res:
        def mappings(self):
            return self

        def all(self):
            return []
    fake_session.execute = AsyncMock(return_value=_Res())

    import backend.algo.routes.performance as perf_mod
    monkeypatch.setattr(
        perf_mod, "get_cache",
        lambda: MagicMock(get=lambda k: None, set=lambda *a, **k: None),
    )

    client = TestClient(a)
    r = client.get("/v1/algo/performance/summary?mode=live")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trades"] == []
