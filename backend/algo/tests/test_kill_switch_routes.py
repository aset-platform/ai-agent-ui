"""Endpoint smokes for /v1/algo/kill-switch/{,arm,disarm}."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.kill_switch import create_kill_switch_router


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_kill_switch_router(), prefix="/v1")
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
    import backend.algo.routes.kill_switch as ks
    monkeypatch.setattr(ks, "_get_session_factory", factory_factory)
    monkeypatch.setattr(ks, "_get_redis", lambda: None)
    return app


def test_get_returns_inactive_default(app):
    with patch(
        "backend.algo.routes.kill_switch.KillSwitchRepo",
    ) as cls:
        cls.return_value.get = AsyncMock(return_value={
            "user_id": uuid4(),
            "active": False,
            "set_by": None, "set_at": None, "reason": None,
        })
        client = TestClient(app)
        r = client.get("/v1/algo/kill-switch")
    assert r.status_code == 200
    assert r.json()["active"] is False


def test_arm_sets_active(app):
    with patch(
        "backend.algo.routes.kill_switch.KillSwitchRepo",
    ) as cls:
        repo = cls.return_value
        repo.arm = AsyncMock()
        repo.get = AsyncMock(return_value={
            "user_id": uuid4(),
            "active": True,
            "set_by": uuid4(),
            "set_at": datetime.now(timezone.utc),
            "reason": "manual",
        })
        client = TestClient(app)
        r = client.post(
            "/v1/algo/kill-switch/arm",
            json={"reason": "manual"},
        )
    assert r.status_code == 200
    assert r.json()["active"] is True


def test_disarm_clears(app):
    with patch(
        "backend.algo.routes.kill_switch.KillSwitchRepo",
    ) as cls:
        repo = cls.return_value
        repo.disarm = AsyncMock()
        repo.get = AsyncMock(return_value={
            "user_id": uuid4(),
            "active": False,
            "set_by": None, "set_at": None, "reason": None,
        })
        client = TestClient(app)
        r = client.post("/v1/algo/kill-switch/disarm")
    assert r.status_code == 200
    assert r.json()["active"] is False
