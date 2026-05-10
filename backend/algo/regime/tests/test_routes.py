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


# ── /period-summary ───────────────────────────────────────────


def _seed_history(rows_spec: list[tuple[date, str, float | None]]):
    """Helper — convert list of (bar_date, label, stress) into
    list[RegimeRow]."""
    from backend.algo.regime.repo import RegimeRow
    return [
        RegimeRow(
            bar_date=d, regime_label=label,
            stress_prob=s, rule_inputs={},
            classifier_version="v1.0",
        )
        for d, label, s in rows_spec
    ]


def test_period_summary_dominant_bull_recommends_bull_template(
    monkeypatch, client: TestClient,
) -> None:
    from backend.algo.routes import regime as routes_mod
    rows = _seed_history(
        [(date(2026, 5, i + 1), "BULL", 0.2) for i in range(20)]
        + [(date(2026, 5, 21 + i), "SIDEWAYS", 0.3) for i in range(5)]
        + [(date(2026, 5, 26 + i), "BEAR", 0.7) for i in range(5)],
    )
    monkeypatch.setattr(
        routes_mod, "get_regime_history",
        lambda **_: rows,
    )
    r = client.get(
        "/v1/algo/regime/period-summary"
        "?start=2026-05-01&end=2026-05-30"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_days"] == 30
    assert body["counts"]["BULL"] == 20
    assert body["counts"]["SIDEWAYS"] == 5
    assert body["counts"]["BEAR"] == 5
    assert body["pct"]["BULL"] == pytest.approx(66.7, abs=0.5)
    assert body["dominant"] == "BULL"
    assert body["recommended_template"] == "regime_bull_momentum"
    assert body["avg_stress_prob"] is not None


def test_period_summary_mixed_no_recommendation(
    monkeypatch, client: TestClient,
) -> None:
    """No regime ≥ 50% → no template recommended."""
    from backend.algo.routes import regime as routes_mod
    from cache import get_cache
    get_cache().invalidate("cache:regime:period_summary:*")
    rows = _seed_history(
        [(date(2026, 6, i + 1), "BULL", 0.2) for i in range(10)]
        + [(date(2026, 6, 11 + i), "SIDEWAYS", 0.3) for i in range(10)]
        + [(date(2026, 6, 21 + i), "BEAR", 0.7) for i in range(10)],
    )
    monkeypatch.setattr(
        routes_mod, "get_regime_history", lambda **_: rows,
    )
    r = client.get(
        "/v1/algo/regime/period-summary"
        "?start=2026-06-01&end=2026-06-30"
    )
    body = r.json()
    assert body["dominant"] in {"BULL", "SIDEWAYS", "BEAR"}
    assert body["recommended_template"] is None


def test_period_summary_empty_history(
    monkeypatch, client: TestClient,
) -> None:
    from backend.algo.routes import regime as routes_mod
    monkeypatch.setattr(
        routes_mod, "get_regime_history", lambda **_: [],
    )
    r = client.get(
        "/v1/algo/regime/period-summary"
        "?start=2026-05-01&end=2026-05-10"
    )
    body = r.json()
    assert body["total_days"] == 0
    assert body["dominant"] is None
    assert body["recommended_template"] is None
    assert body["avg_stress_prob"] is None


def test_period_summary_400_when_end_before_start(
    monkeypatch, client: TestClient,
) -> None:
    from backend.algo.routes import regime as routes_mod
    monkeypatch.setattr(
        routes_mod, "get_regime_history", lambda **_: [],
    )
    # Bypass cache for this test (different start/end)
    r = client.get(
        "/v1/algo/regime/period-summary"
        "?start=2026-05-30&end=2026-05-01"
    )
    assert r.status_code == 400
