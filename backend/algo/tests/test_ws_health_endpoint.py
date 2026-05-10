"""Tests for GET /v1/algo/live/ws-health — OBS-1.

Endpoint contract:
  - Always returns 200 for an authenticated pro/superuser.
  - Reports ``connected: false`` + zeros when no multiplexer is
    registered for the user (does NOT spin one up).
  - Reflects the live multiplexer's ``health_snapshot()`` when one
    exists, plus a derived ``tick_age_seconds`` (now - last_tick_at).
  - Returns 403 for non-pro/non-superuser callers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.broker import ws_registry
from backend.algo.routes.live import create_live_router

UTC = timezone.utc
SUPERUSER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _clear_registry():
    ws_registry._registry.clear()
    yield
    ws_registry._registry.clear()


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(create_live_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id=SUPERUSER_ID,
        email="t@t",
        role="superuser",
    )
    return app


def _seed_mux(
    *,
    user_id: UUID,
    connected: bool = True,
    subscriber_count: int = 2,
    subscribed_tokens: int = 4,
    last_tick_at: datetime | None = None,
    tick_count_today: int = 17,
) -> MagicMock:
    """Insert a stub mux into the registry for the given user."""
    mux = MagicMock()
    mux.health_snapshot.return_value = {
        "connected": connected,
        "subscriber_count": subscriber_count,
        "subscribed_tokens": subscribed_tokens,
        "last_tick_at": last_tick_at,
        "tick_count_today": tick_count_today,
    }
    ws_registry._registry[user_id] = mux
    return mux


def test_no_mux_returns_disconnected(app):
    """No multiplexer registered → all-zero disconnected snapshot."""
    client = TestClient(app)
    r = client.get("/v1/algo/live/ws-health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "connected": False,
        "subscriber_count": 0,
        "subscribed_tokens": 0,
        "last_tick_at": None,
        "tick_age_seconds": None,
        "tick_count_today": 0,
    }


def test_with_mux_returns_snapshot(app):
    """Seeded mux → values mirrored, tick_age computed."""
    uid = UUID(SUPERUSER_ID)
    last_tick = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=12,
    )
    _seed_mux(
        user_id=uid,
        connected=True,
        subscriber_count=2,
        subscribed_tokens=4,
        last_tick_at=last_tick,
        tick_count_today=17,
    )
    client = TestClient(app)
    r = client.get("/v1/algo/live/ws-health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["subscriber_count"] == 2
    assert body["subscribed_tokens"] == 4
    assert body["tick_count_today"] == 17
    assert body["last_tick_at"] is not None
    assert body["last_tick_at"].endswith("Z")
    # Tolerate a few seconds of drift in CI.
    assert 10 <= body["tick_age_seconds"] <= 30


def test_endpoint_does_not_create_mux(app):
    """A poll for a user with no mux must not allocate one."""
    assert len(ws_registry._registry) == 0
    client = TestClient(app)
    client.get("/v1/algo/live/ws-health")
    assert len(ws_registry._registry) == 0


def test_age_seconds_computed_from_last_tick(app):
    """tick_age_seconds matches (now - last_tick_at) within drift."""
    uid = UUID(SUPERUSER_ID)
    last_tick = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=120,
    )
    _seed_mux(user_id=uid, last_tick_at=last_tick)
    client = TestClient(app)
    body = client.get("/v1/algo/live/ws-health").json()
    assert 118 <= body["tick_age_seconds"] <= 130


def test_unauthorized_without_pro_or_superuser_returns_403():
    """A general (non-pro) user must be rejected by pro_or_superuser.

    We don't have a fully-wired auth stack in the unit-test app,
    so we install an override that raises the same 403 the real
    guard would — confirming the route IS protected by the
    ``pro_or_superuser`` dependency (i.e. that 403s propagate).
    """
    from fastapi import HTTPException

    app = FastAPI()
    app.include_router(create_live_router(), prefix="/v1")

    def _general_user_blocked():
        raise HTTPException(
            status_code=403,
            detail="pro or superuser required",
        )

    app.dependency_overrides[pro_or_superuser] = _general_user_blocked
    client = TestClient(app)
    r = client.get("/v1/algo/live/ws-health")
    assert r.status_code == 403
