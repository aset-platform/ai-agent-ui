"""Endpoint smokes for /v1/algo/paper/runs lifecycle."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.paper import create_paper_router


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_paper_router(), prefix="/v1")
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
    import backend.algo.routes.paper as paper_mod
    monkeypatch.setattr(
        paper_mod, "_get_session_factory", factory_factory,
    )
    return app


def test_start_run_returns_404_when_strategy_missing(app):
    with patch(
        "backend.algo.strategy.repo.get_strategy",
        new=AsyncMock(return_value=None),
    ):
        client = TestClient(app)
        r = client.post(
            "/v1/algo/paper/runs",
            json={
                "strategy_id": str(uuid4()),
                "fixture_path": "ticks_sample.jsonl",
            },
        )
    assert r.status_code == 404


def test_start_run_400_on_invalid_fixture_path(app):
    fake_strategy = type("S", (), {
        "id": uuid4(), "name": "x", "root": None,
    })()
    with patch(
        "backend.algo.strategy.repo.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ):
        client = TestClient(app)
        r = client.post(
            "/v1/algo/paper/runs",
            json={
                "strategy_id": str(fake_strategy.id),
                "fixture_path": "../../etc/passwd",
            },
        )
    assert r.status_code == 400


def test_start_run_201_on_happy_path(app):
    strategy_id = uuid4()
    fake_strategy = type("S", (), {
        "id": strategy_id, "name": "x", "root": None,
    })()
    sv = MagicMock()
    sv.start_run = AsyncMock(return_value={
        "user_id": "00000000-0000-0000-0000-000000000001",
        "strategy_id": str(strategy_id),
        "strategy_name": "x",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    })
    with patch(
        "backend.algo.strategy.repo.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.paper.supervisor.get_supervisor",
        return_value=sv,
    ), patch(
        "backend.algo.paper.supervisor.build_replay_source",
        return_value=MagicMock(),
    ), patch(
        "backend.algo.paper.kill_switch_repo.KillSwitchRepo"
    ) as ks_cls:
        ks_cls.return_value.is_active = AsyncMock(return_value=False)
        client = TestClient(app)
        r = client.post(
            "/v1/algo/paper/runs",
            json={
                "strategy_id": str(strategy_id),
                "fixture_path": "ticks_sample.jsonl",
            },
        )
    assert r.status_code == 201
    sv.start_run.assert_awaited_once()


def test_start_run_409_on_collision(app):
    strategy_id = uuid4()
    fake_strategy = type("S", (), {
        "id": strategy_id, "name": "x", "root": None,
    })()
    sv = MagicMock()
    sv.start_run = AsyncMock(
        side_effect=RuntimeError("Run already active"),
    )
    with patch(
        "backend.algo.strategy.repo.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.paper.supervisor.get_supervisor",
        return_value=sv,
    ), patch(
        "backend.algo.paper.supervisor.build_replay_source",
        return_value=MagicMock(),
    ), patch(
        "backend.algo.paper.kill_switch_repo.KillSwitchRepo"
    ) as ks_cls:
        ks_cls.return_value.is_active = AsyncMock(return_value=False)
        client = TestClient(app)
        r = client.post(
            "/v1/algo/paper/runs",
            json={
                "strategy_id": str(strategy_id),
                "fixture_path": "ticks_sample.jsonl",
            },
        )
    assert r.status_code == 409


def test_stop_run_404_when_unknown(app):
    sv = MagicMock()
    sv.stop_run = AsyncMock(return_value=False)
    with patch(
        "backend.algo.paper.supervisor.get_supervisor",
        return_value=sv,
    ):
        client = TestClient(app)
        r = client.delete(f"/v1/algo/paper/runs/{uuid4()}")
    assert r.status_code == 404


def test_stop_run_200(app):
    sv = MagicMock()
    sv.stop_run = AsyncMock(return_value=True)
    with patch(
        "backend.algo.paper.supervisor.get_supervisor",
        return_value=sv,
    ):
        client = TestClient(app)
        r = client.delete(f"/v1/algo/paper/runs/{uuid4()}")
    assert r.status_code == 200
    assert r.json() == {"stopped": True}


def test_list_runs_empty(app):
    sv = MagicMock()
    sv.list_active = MagicMock(return_value=[])
    with patch(
        "backend.algo.paper.supervisor.get_supervisor",
        return_value=sv,
    ):
        client = TestClient(app)
        r = client.get("/v1/algo/paper/runs")
    assert r.status_code == 200
    assert r.json() == []
