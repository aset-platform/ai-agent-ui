"""Route-layer tests for /v1/algo/strategies/{strategy_id}/
feature-importance (ASETPLTFRM-413 / FE-11).

Auth gating, 422-on-insufficient-data, cache short-circuit,
and basic response-shape sanity checks. The sklearn fit and
Iceberg scan are patched out so this suite is fast + has no
external deps.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from cache import get_cache
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user, pro_or_superuser
from auth.models.response import UserContext
from backend.algo.features.importance import (
    FeatureImportanceResult,
    FeatureScore,
    InsufficientDataError,
)
from backend.algo.routes import feature_importance as route_mod
from backend.algo.routes.feature_importance import (
    create_feature_importance_router,
)

_STRATEGY_ID = "33333333-3333-3333-3333-333333333333"


def _superuser() -> UserContext:
    return UserContext(
        user_id="11111111-1111-1111-1111-111111111111",
        email="su@t",
        role="superuser",
    )


def _general_user() -> UserContext:
    return UserContext(
        user_id="22222222-2222-2222-2222-222222222222",
        email="g@t",
        role="general",
    )


@pytest.fixture
def superuser_app() -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_feature_importance_router(),
        prefix="/v1",
    )
    app.dependency_overrides[pro_or_superuser] = _superuser
    app.dependency_overrides[get_current_user] = _superuser
    return app


@pytest.fixture
def superuser_client(superuser_app: FastAPI) -> TestClient:
    return TestClient(superuser_app)


@pytest.fixture(autouse=True)
def _flush_cache() -> None:
    """Ensure no cache state leaks between tests."""
    try:
        get_cache().invalidate("cache:feature_importance:*")
    except Exception:  # noqa: BLE001
        pass


def _fake_result() -> FeatureImportanceResult:
    return FeatureImportanceResult(
        strategy_id=_STRATEGY_ID,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        n_trades_used=42,
        n_features=5,
        top_features=[
            FeatureScore(name="perfect_signal", importance=0.82),
            FeatureScore(name="rsi_14", importance=0.08),
        ],
        classifier_version="sklearn-1.8.0-gbc-n100-d3",
        fitted_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
    )


def test_route_loads_snapshots_and_returns_top_features(
    monkeypatch: pytest.MonkeyPatch,
    superuser_client: TestClient,
) -> None:
    monkeypatch.setattr(
        route_mod,
        "_load_snapshots",
        lambda **kw: [
            {"features_json": "{}", "realised_pnl_inr": 5.0} for _ in range(40)
        ],
    )
    monkeypatch.setattr(
        route_mod,
        "_train_blocking",
        lambda **kw: _fake_result(),
    )
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}"
        "/feature-importance"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy_id"] == _STRATEGY_ID
    assert body["n_trades_used"] == 42
    assert body["n_features"] == 5
    assert len(body["top_features"]) == 2
    assert body["top_features"][0]["name"] == "perfect_signal"
    assert body["top_features"][0]["importance"] == pytest.approx(0.82)
    assert "sklearn-" in body["classifier_version"]


def test_route_returns_422_on_insufficient_data(
    monkeypatch: pytest.MonkeyPatch,
    superuser_client: TestClient,
) -> None:
    monkeypatch.setattr(
        route_mod,
        "_load_snapshots",
        lambda **kw: [],
    )

    def _raise(**_kw):
        raise InsufficientDataError("only 0 labeled trade(s) available")

    monkeypatch.setattr(route_mod, "_train_blocking", _raise)
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}"
        "/feature-importance"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code == 422
    body = r.json()
    assert "labeled" in body["detail"].lower()


def test_route_caches_response(
    monkeypatch: pytest.MonkeyPatch,
    superuser_client: TestClient,
) -> None:
    """Second call must hit the cache and skip both load +
    fit. We track call counts on both shims to confirm."""
    load_calls: list[int] = []
    train_calls: list[int] = []

    def _load(**_kw):
        load_calls.append(1)
        return [{"features_json": "{}", "realised_pnl_inr": 1.0}]

    def _train(**_kw):
        train_calls.append(1)
        return _fake_result()

    monkeypatch.setattr(route_mod, "_load_snapshots", _load)
    monkeypatch.setattr(route_mod, "_train_blocking", _train)

    url = (
        f"/v1/algo/strategies/{_STRATEGY_ID}"
        "/feature-importance"
        "?period_start=2026-01-01&period_end=2026-03-31"
        "&top_n=10&min_trades=30"
    )
    r1 = superuser_client.get(url)
    assert r1.status_code == 200, r1.text

    r2 = superuser_client.get(url)
    assert r2.status_code == 200, r2.text

    # First call did the work; second was a cache hit.
    if get_cache().__class__.__name__ != "_NoOpCache":
        assert len(load_calls) == 1, (
            "second call should have hit cache, not re-loaded "
            f"snapshots (calls={len(load_calls)})"
        )
        assert len(train_calls) == 1, (
            "second call should have hit cache, not re-fit "
            f"(calls={len(train_calls)})"
        )

    # Response shape stable across cache hit / miss.
    assert r1.json() == r2.json()


def test_route_requires_auth() -> None:
    """No auth override → anon request → 401."""
    app = FastAPI()
    app.include_router(
        create_feature_importance_router(),
        prefix="/v1",
    )
    client = TestClient(app)
    r = client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}"
        "/feature-importance"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    # Either 401 (no token) or 403 (token but anon dep
    # rejects). Both are acceptable auth-failure shapes.
    assert r.status_code in (401, 403)


def test_route_pro_or_superuser_only() -> None:
    """A 'general' user must be rejected with 403."""
    app = FastAPI()
    app.include_router(
        create_feature_importance_router(),
        prefix="/v1",
    )
    # We only override get_current_user — pro_or_superuser
    # chains through it and applies the real role check.
    app.dependency_overrides[get_current_user] = _general_user
    client = TestClient(app)
    r = client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}"
        "/feature-importance"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code == 403


def test_route_rejects_inverted_window(
    superuser_client: TestClient,
) -> None:
    """period_end < period_start → 400 before any I/O."""
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}"
        "/feature-importance"
        "?period_start=2026-03-31&period_end=2026-01-01"
    )
    assert r.status_code == 400


def test_route_validates_top_n_range(
    superuser_client: TestClient,
) -> None:
    """``top_n`` must be in [1, 50]; FastAPI's ``Query``
    validator returns 422 on out-of-bounds."""
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}"
        "/feature-importance"
        "?period_start=2026-01-01&period_end=2026-03-31"
        "&top_n=0"
    )
    assert r.status_code == 422


def test_route_500_on_iceberg_load_failure(
    monkeypatch: pytest.MonkeyPatch,
    superuser_client: TestClient,
) -> None:
    def _boom(**_kw):
        raise RuntimeError("iceberg down")

    monkeypatch.setattr(route_mod, "_load_snapshots", _boom)
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}"
        "/feature-importance"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code == 500
