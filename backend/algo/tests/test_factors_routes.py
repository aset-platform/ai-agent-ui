"""Smoke tests for /v1/algo/factors/* endpoints."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from auth.models.response import UserContext
from backend.algo.factors.repo import FactorRow
from backend.algo.routes.factors import create_factors_router


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_factors_router(), prefix="/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        user_id="11111111-1111-1111-1111-111111111111",
        email="t@t",
        role="superuser",
    )
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _seed_rows() -> list[FactorRow]:
    return [
        FactorRow(
            ticker="INFY.NS",
            bar_date=date(2026, 5, 8),
            values={
                "mom_12_1": 0.18, "f_score": 7.0,
                "realized_vol_60d": 0.22,
            },
            sector="IT",
        ),
        FactorRow(
            ticker="TCS.NS",
            bar_date=date(2026, 5, 8),
            values={
                "mom_12_1": 0.10, "f_score": 6.0,
                "realized_vol_60d": 0.18,
            },
            sector="IT",
        ),
    ]


def test_get_one_returns_latest(monkeypatch, client: TestClient) -> None:
    from backend.algo.routes import factors as routes_mod

    monkeypatch.setattr(
        routes_mod, "get_factors_window",
        lambda tickers, start, end: [_seed_rows()[0]],
    )
    r = client.get("/v1/algo/factors/INFY.NS")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticker"] == "INFY.NS"
    assert body["sector"] == "IT"
    assert body["values"]["mom_12_1"] == pytest.approx(0.18)


def test_get_one_404_when_no_data(
    monkeypatch, client: TestClient,
) -> None:
    from backend.algo.routes import factors as routes_mod
    monkeypatch.setattr(
        routes_mod, "get_factors_window",
        lambda tickers, start, end: [],
    )
    r = client.get("/v1/algo/factors/UNKNOWN.NS")
    assert r.status_code == 404


def test_get_bulk_returns_sorted_list(
    monkeypatch, client: TestClient,
) -> None:
    from backend.algo.routes import factors as routes_mod
    monkeypatch.setattr(
        routes_mod, "get_factors_window",
        lambda tickers, start, end: _seed_rows(),
    )
    r = client.get("/v1/algo/factors?tickers=TCS.NS,INFY.NS")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    # Sorted by ticker ASC
    assert body[0]["ticker"] == "INFY.NS"
    assert body[1]["ticker"] == "TCS.NS"


def test_get_bulk_dedups_tickers(
    monkeypatch, client: TestClient,
) -> None:
    from backend.algo.routes import factors as routes_mod
    from cache import get_cache

    # Cache may carry over from a prior test — purge bulk keys
    get_cache().invalidate("cache:factors:bulk:*")

    captured: list = []

    def fake_window(tickers, start, end):
        captured.extend(tickers)
        return _seed_rows()

    monkeypatch.setattr(
        routes_mod, "get_factors_window", fake_window,
    )
    # Use distinct ticker set so the cache key differs from
    # test_get_bulk_returns_sorted_list above.
    r = client.get(
        "/v1/algo/factors?tickers=AAA.NS,BBB.NS,AAA.NS,AAA.NS"
    )
    assert r.status_code == 200
    # Underlying call deduped + sorted
    assert sorted(captured) == ["AAA.NS", "BBB.NS"]


def test_get_bulk_400_when_empty(client: TestClient) -> None:
    r = client.get("/v1/algo/factors?tickers=,,,")
    assert r.status_code == 400


def test_get_bulk_400_when_too_many(client: TestClient) -> None:
    tickers = ",".join(f"T{i}.NS" for i in range(201))
    r = client.get(f"/v1/algo/factors?tickers={tickers}")
    assert r.status_code == 400
