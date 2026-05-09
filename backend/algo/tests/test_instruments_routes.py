"""Endpoint smokes for /v1/algo/instruments."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.instruments import create_instruments_router


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_instruments_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t",
        role="superuser",
    )

    rows: dict = {"items": []}

    class _Stub:
        async def execute(self, q, params=None):
            sql = str(q)

            class _Res:
                def __init__(self, items):
                    self._items = items

                def mappings(self):
                    return self

                def all(self):
                    return self._items

                def first(self):
                    return self._items[0] if self._items else None

            if "SELECT COUNT(*)" in sql:
                return _Res([{"c": len(rows["items"])}])
            if "SELECT instrument_token" in sql:
                page = (params or {}).get("limit", 50)
                offset = (params or {}).get("offset", 0)
                return _Res(rows["items"][offset:offset + page])
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

    import backend.algo.routes.instruments as inst
    monkeypatch.setattr(
        inst, "_get_session_factory", lambda: _Factory(),
    )
    rows["items"] = [
        {
            "instrument_token": i,
            "tradingsymbol": f"SYM{i}",
            "exchange": "NSE",
            "segment": "NSE-EQ",
            "lot_size": 1,
            "tick_size": 0.05,
            "our_ticker": None,
            "loaded_at": None,
        }
        for i in range(7)
    ]
    return app


def test_list_returns_paginated(app):
    client = TestClient(app)
    r = client.get("/v1/algo/instruments?page=1&page_size=5")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 7
    assert len(body["rows"]) == 5


def test_list_rejects_unknown_exchange(app):
    client = TestClient(app)
    r = client.get("/v1/algo/instruments?exchange=XYZ")
    assert r.status_code == 422


def test_refresh_endpoint_calls_loader(app):
    client = TestClient(app)
    with patch(
        "backend.algo.routes.instruments.run_instruments_refresh",
        new=AsyncMock(return_value={"instruments_loaded": 42}),
    ):
        r = client.post("/v1/algo/instruments/refresh")
    assert r.status_code == 200
    assert r.json()["instruments_loaded"] == 42


def test_refresh_endpoint_502_on_error(app):
    client = TestClient(app)
    with patch(
        "backend.algo.routes.instruments.run_instruments_refresh",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        r = client.post("/v1/algo/instruments/refresh")
    assert r.status_code == 502
