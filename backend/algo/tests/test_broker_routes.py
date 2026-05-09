# backend/algo/tests/test_broker_routes.py
"""Endpoint smoke tests for /v1/algo/broker/*. KiteConnect SDK is
mocked end-to-end so the tests run without real network calls.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault(
    "BYO_SECRET_KEY",
    "Q3RZ8h3tQq2c5rVH0hWv0cHXh2OtdJv6f4M6Y9pQ8mE=",
)
# Required by the OAuth callback — matched against Kite's
# response in the route handler.
os.environ.setdefault("ALGO_KITE_API_SECRET", "fake_secret_for_tests")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from auth.dependencies import pro_or_superuser  # noqa: E402
from auth.models import UserContext  # noqa: E402
from backend.algo.routes.broker import create_broker_router  # noqa: E402


@pytest.fixture
def app(monkeypatch):
    """Build a FastAPI app with the broker router + stubbed deps."""
    app = FastAPI()
    app.include_router(create_broker_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t",
        role="superuser",
    )

    # Stub the session factory + repo + KiteClient.
    rows: dict = {"items": []}

    class _Stub:
        async def execute(self, q, params=None):
            sql = str(q)

            class _Res:
                def __init__(self, items):
                    self._items = items

                def mappings(self):
                    return self

                def first(self):
                    return self._items[0] if self._items else None

                @property
                def rowcount(self):
                    return len(self._items)

            if "INSERT INTO algo.broker_credentials" in sql:
                rows["items"] = [dict(params)]
                return _Res(rows["items"])
            if "SELECT" in sql:
                return _Res(rows["items"])
            if "UPDATE" in sql:
                if rows["items"]:
                    rows["items"][0].update(
                        {k: v for k, v in (params or {}).items()},
                    )
                return _Res(rows["items"])
            if "DELETE" in sql:
                before = len(rows["items"])
                rows["items"] = []
                return _Res([None] * before)
            return _Res([])

        async def commit(self):
            return None

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _Stub()

        async def __aexit__(self, *args):
            return None

    import backend.algo.routes.broker as broker_routes
    monkeypatch.setattr(
        broker_routes, "_get_session_factory", lambda: _Factory(),
    )
    return app


def test_status_returns_disconnected_when_no_creds(app):
    client = TestClient(app)
    r = client.get("/v1/algo/broker/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "disconnected"
    assert body["kite_user_id"] is None


def test_post_api_key_persists_and_status_flips(app):
    client = TestClient(app)
    r = client.post(
        "/v1/algo/broker/api-key",
        json={"api_key": "api_key_xyz"},
    )
    assert r.status_code == 204
    r = client.get("/v1/algo/broker/status")
    body = r.json()
    # api_key set but no access_token yet → key_set, awaiting login
    assert body["status"] == "key_set"


def test_login_url_requires_api_key(app):
    client = TestClient(app)
    r = client.get("/v1/algo/broker/login")
    assert r.status_code == 400


def test_login_url_returns_url_when_api_key_present(app):
    client = TestClient(app)
    client.post(
        "/v1/algo/broker/api-key", json={"api_key": "api_key_xyz"},
    )
    with patch(
        "backend.algo.routes.broker.KiteClient",
    ) as MockKite:
        MockKite.return_value.login_url.return_value = (
            "https://kite.zerodha.com/connect/login?api_key=xxx"
        )
        r = client.get("/v1/algo/broker/login")
    assert r.status_code == 200
    assert "kite.zerodha.com" in r.json()["url"]


def test_callback_exchanges_request_token(app):
    client = TestClient(app)
    client.post(
        "/v1/algo/broker/api-key", json={"api_key": "api_key_xyz"},
    )
    with patch(
        "backend.algo.routes.broker.KiteClient",
    ) as MockKite:
        MockKite.return_value.generate_session.return_value = {
            "access_token": "tok123",
            "user_id": "AB1234",
        }
        r = client.get(
            "/v1/algo/broker/callback?request_token=req_abc",
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "connected"
    assert body["kite_user_id"] == "AB1234"


def test_callback_400_when_no_api_key(app):
    client = TestClient(app)
    r = client.get("/v1/algo/broker/callback?request_token=req_abc")
    assert r.status_code == 400


def test_delete_removes_credentials(app):
    client = TestClient(app)
    client.post(
        "/v1/algo/broker/api-key", json={"api_key": "api_key_xyz"},
    )
    r = client.delete("/v1/algo/broker")
    assert r.status_code == 204
    r = client.get("/v1/algo/broker/status")
    assert r.json()["status"] == "disconnected"
