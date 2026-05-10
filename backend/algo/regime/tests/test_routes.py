"""Smoke tests for /v1/algo/regime/* endpoints."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from auth.models.response import UserContext
from backend.algo.routes.regime import create_regime_router


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_regime_router(), prefix="/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        user_id="11111111-1111-1111-1111-111111111111",
        email="t@t",
        role="superuser",
    )
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_current_returns_latest(monkeypatch, client: TestClient) -> None:
    from backend.algo.routes import regime as routes_mod
    from backend.algo.regime.repo import RegimeRow

    seed = RegimeRow(
        bar_date=date(2026, 5, 9),
        regime_label="BULL",
        stress_prob=0.20,
        rule_inputs={"vix": 14.0, "pct_above_50sma": 0.62},
        classifier_version="v1.0",
    )
    monkeypatch.setattr(
        routes_mod, "get_latest_regime", lambda: seed,
    )
    # Bypass cache for deterministic test
    monkeypatch.setattr(
        routes_mod, "_cache_get", lambda key: None,
    )
    monkeypatch.setattr(
        routes_mod, "_cache_set", lambda key, val, ttl: None,
    )

    r = client.get("/v1/algo/regime/current")
    assert r.status_code == 200
    body = r.json()
    assert body["regime_label"] == "BULL"
    assert body["stress_prob"] == pytest.approx(0.20)
    assert body["bar_date"] == "2026-05-09"


def test_current_404_when_empty(monkeypatch, client: TestClient) -> None:
    from backend.algo.routes import regime as routes_mod

    monkeypatch.setattr(
        routes_mod, "get_latest_regime", lambda: None,
    )
    monkeypatch.setattr(
        routes_mod, "_cache_get", lambda key: None,
    )
    monkeypatch.setattr(
        routes_mod, "_cache_set", lambda key, val, ttl: None,
    )
    r = client.get("/v1/algo/regime/current")
    assert r.status_code == 404


def test_history_returns_window(monkeypatch, client: TestClient) -> None:
    from backend.algo.routes import regime as routes_mod
    from backend.algo.regime.repo import RegimeRow

    rows = [
        RegimeRow(
            bar_date=date(2026, 5, 9) - timedelta(days=i),
            regime_label="BULL",
            stress_prob=0.1 + i * 0.01,
            rule_inputs={},
            classifier_version="v1.0",
        )
        for i in range(5)
    ]
    monkeypatch.setattr(
        routes_mod,
        "get_regime_history",
        lambda *a, **k: rows,
    )
    monkeypatch.setattr(
        routes_mod, "_cache_get", lambda key: None,
    )
    monkeypatch.setattr(
        routes_mod, "_cache_set", lambda key, val, ttl: None,
    )

    r = client.get("/v1/algo/regime/history?days=5")
    assert r.status_code == 200
    assert len(r.json()["rows"]) == 5


def test_classifier_health_reports_hmm_age(
    monkeypatch, client: TestClient,
) -> None:
    from backend.algo.routes import regime as routes_mod
    from backend.algo.regime.repo import HmmStateRow

    monkeypatch.setattr(
        routes_mod,
        "get_latest_hmm_state",
        lambda: HmmStateRow(
            trained_through=date(2026, 4, 1),
            transmat=[[0.95, 0.05], [0.10, 0.90]],
            means=[[0.001, 0.010], [-0.002, 0.025]],
            covars=[
                [[0.0, 0.0], [0.0, 0.0]],
                [[0.0, 0.0], [0.0, 0.0]],
            ],
            n_observations=1500,
        ),
    )
    monkeypatch.setattr(
        routes_mod, "get_latest_regime", lambda: None,
    )
    r = client.get("/v1/algo/regime/classifier-health")
    assert r.status_code == 200
    body = r.json()
    assert "hmm_trained_through" in body
    assert "hmm_age_days" in body
    assert body["hmm_age_days"] >= 0
