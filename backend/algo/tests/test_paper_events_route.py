"""GET /v1/algo/paper/events — endpoint smoke."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.paper import create_paper_router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(create_paper_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t", role="superuser",
    )
    return app


def test_returns_empty_when_table_missing(app):
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        side_effect=FileNotFoundError(),
    ):
        client = TestClient(app)
        r = client.get("/v1/algo/paper/events")
    assert r.status_code == 200
    assert r.json() == []


def test_returns_decoded_events_newest_first(app):
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[
            {
                "event_id": "e1",
                "ts_ns": 200,
                "ts_date": "2026-04-01",
                "strategy_id": None,
                "type": "signal_generated",
                "payload_json": '{"ticker":"X","side":"BUY","qty":5}',
            },
            {
                "event_id": "e2",
                "ts_ns": 100,
                "ts_date": "2026-04-01",
                "strategy_id": None,
                "type": "order_filled",
                "payload_json": '{"ticker":"X","side":"BUY","qty":5}',
            },
        ],
    ):
        client = TestClient(app)
        r = client.get("/v1/algo/paper/events")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert rows[0]["payload"]["ticker"] == "X"
