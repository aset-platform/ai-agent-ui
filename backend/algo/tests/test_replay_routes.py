"""GET /v1/algo/replay/events — endpoint smokes."""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.replay import create_replay_router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(create_replay_router(), prefix="/v1")
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
        r = client.get("/v1/algo/replay/events")
    assert r.status_code == 200
    assert r.json() == []


def test_returns_decoded_events(app):
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[
            {
                "event_id": "e1",
                "ts_ns": 200,
                "ts_date": "2026-04-01",
                "mode": "paper",
                "strategy_id": None,
                "type": "signal_generated",
                "payload_json": '{"ticker":"X","side":"BUY","qty":5}',
            },
        ],
    ):
        client = TestClient(app)
        r = client.get("/v1/algo/replay/events")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["mode"] == "paper"


def test_invalid_mode_returns_empty(app):
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ):
        client = TestClient(app)
        r = client.get("/v1/algo/replay/events?mode=nonsense")
    assert r.status_code == 200
    assert r.json() == []


def test_invalid_type_returns_empty(app):
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ):
        client = TestClient(app)
        r = client.get("/v1/algo/replay/events?type=fake_event")
    assert r.status_code == 200
    assert r.json() == []


def test_filters_passed_through_to_query(app):
    captured: dict = {}

    def _fake(table_name, sql, params):
        captured["sql"] = sql
        captured["params"] = list(params)
        return []

    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        side_effect=_fake,
    ):
        sid = uuid4()
        client = TestClient(app)
        r = client.get(
            "/v1/algo/replay/events"
            f"?mode=paper&type=order_filled&strategy_id={sid}"
            "&ts_date=2026-04-01&limit=50",
        )
    assert r.status_code == 200
    assert "mode = ?" in captured["sql"]
    assert "type = ?" in captured["sql"]
    assert "strategy_id = ?" in captured["sql"]
    assert "ts_date = ?" in captured["sql"]
    assert "paper" in captured["params"]
    assert "order_filled" in captured["params"]
    assert str(sid) in captured["params"]
    assert "2026-04-01" in captured["params"]
    assert 50 in captured["params"]
