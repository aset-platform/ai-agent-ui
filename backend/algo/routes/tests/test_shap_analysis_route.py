"""Route-layer tests for /v1/algo/strategies/{strategy_id}/shap
(ASETPLTFRM-414 / FE-12).

Patches out the Iceberg loader + ``train_classifier`` +
``compute_shap_for_trades`` so the suite is fast + hermetic.
Real shap.TreeExplainer is never invoked — we only test the
route's wiring (auth, cache, error mapping, response shape).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pytest
from cache import get_cache
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user, pro_or_superuser
from auth.models.response import UserContext
from backend.algo.features.importance import (
    InsufficientDataError,
    TrainedClassifier,
)
from backend.algo.features.shap_analysis import (
    FillShap,
    ShapAnalysisResult,
)
from backend.algo.routes import shap_analysis as route_mod
from backend.algo.routes.shap_analysis import (
    create_shap_analysis_router,
)

_STRATEGY_ID = "44444444-4444-4444-4444-444444444444"


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
        create_shap_analysis_router(),
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
    try:
        get_cache().invalidate("cache:shap:*")
    except Exception:  # noqa: BLE001
        pass


def _fake_trained() -> TrainedClassifier:
    return TrainedClassifier(
        model=object(),  # type: ignore[arg-type]
        feature_columns=["X1", "X2"],
        X=np.zeros((3, 2)),
        y=np.array([0, 1, 0]),
        n_trades_used=3,
        fill_ids=["fa", "fb", "fc"],
        classifier_version="sklearn-1.8.0-gbc-n100-d3",
        strategy_id_seen=_STRATEGY_ID,
        period_start_seen=date(2026, 1, 1),
        period_end_seen=date(2026, 3, 31),
    )


def _fake_shap_result() -> ShapAnalysisResult:
    return ShapAnalysisResult(
        strategy_id=_STRATEGY_ID,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        n_fills=2,
        n_features=2,
        classifier_version="sklearn-1.8.0-gbc-n100-d3",
        top_features_by_mean_abs_shap=[
            ("X1", 0.42),
            ("X2", 0.07),
        ],
        per_fill=[
            FillShap(
                fill_id="fa",
                shap_values={"X1": 0.4, "X2": 0.05},
                base_value=0.5,
                prediction=0.81,
            ),
            FillShap(
                fill_id="fb",
                shap_values={"X1": -0.44, "X2": 0.09},
                base_value=0.5,
                prediction=0.12,
            ),
        ],
        computed_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
    )


def test_route_loads_classifier_and_computes_shap(
    monkeypatch: pytest.MonkeyPatch,
    superuser_client: TestClient,
) -> None:
    monkeypatch.setattr(
        route_mod,
        "_load_snapshots",
        lambda **kw: [
            {"features_json": "{}", "realised_pnl_inr": 1.0} for _ in range(40)
        ],
    )
    monkeypatch.setattr(
        route_mod,
        "_train_blocking",
        lambda **kw: _fake_trained(),
    )
    monkeypatch.setattr(
        route_mod,
        "_shap_blocking",
        lambda **kw: _fake_shap_result(),
    )

    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}/shap"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy_id"] == _STRATEGY_ID
    assert body["n_fills"] == 2
    assert body["n_features"] == 2
    assert len(body["per_fill"]) == 2
    assert body["per_fill"][0]["fill_id"] == "fa"
    assert body["per_fill"][0]["shap_values"]["X1"] == pytest.approx(0.4)
    assert body["top_features_by_mean_abs_shap"][0][0] == "X1"
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

    def _raise(**_kw: Any) -> Any:
        raise InsufficientDataError("only 0 labeled trade(s)")

    monkeypatch.setattr(route_mod, "_train_blocking", _raise)
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}/shap"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code == 422
    assert "insufficient" in r.json()["detail"].lower()


def test_route_returns_400_on_unknown_fill_ids(
    monkeypatch: pytest.MonkeyPatch,
    superuser_client: TestClient,
) -> None:
    monkeypatch.setattr(
        route_mod,
        "_load_snapshots",
        lambda **kw: [
            {"features_json": "{}", "realised_pnl_inr": 1.0} for _ in range(40)
        ],
    )
    monkeypatch.setattr(
        route_mod,
        "_train_blocking",
        lambda **kw: _fake_trained(),
    )

    def _raise_unknown(**_kw: Any) -> Any:
        raise ValueError("fill_ids not found in training set: bogus-id")

    monkeypatch.setattr(
        route_mod,
        "_shap_blocking",
        _raise_unknown,
    )
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}/shap"
        "?period_start=2026-01-01&period_end=2026-03-31"
        "&fill_ids=bogus-id"
    )
    assert r.status_code == 400
    assert "bogus-id" in r.json()["detail"]


def test_route_caches_response(
    monkeypatch: pytest.MonkeyPatch,
    superuser_client: TestClient,
) -> None:
    load_calls: list[int] = []
    train_calls: list[int] = []
    shap_calls: list[int] = []

    def _load(**_kw: Any) -> Any:
        load_calls.append(1)
        return [{"features_json": "{}", "realised_pnl_inr": 1.0}]

    def _train(**_kw: Any) -> Any:
        train_calls.append(1)
        return _fake_trained()

    def _shap(**_kw: Any) -> Any:
        shap_calls.append(1)
        return _fake_shap_result()

    monkeypatch.setattr(route_mod, "_load_snapshots", _load)
    monkeypatch.setattr(route_mod, "_train_blocking", _train)
    monkeypatch.setattr(route_mod, "_shap_blocking", _shap)

    url = (
        f"/v1/algo/strategies/{_STRATEGY_ID}/shap"
        "?period_start=2026-01-01&period_end=2026-03-31"
        "&top_n=10&min_trades=30"
    )
    r1 = superuser_client.get(url)
    assert r1.status_code == 200, r1.text

    r2 = superuser_client.get(url)
    assert r2.status_code == 200, r2.text

    if get_cache().__class__.__name__ != "_NoOpCache":
        assert len(load_calls) == 1
        assert len(train_calls) == 1
        assert len(shap_calls) == 1

    assert r1.json() == r2.json()


def test_route_requires_auth() -> None:
    app = FastAPI()
    app.include_router(
        create_shap_analysis_router(),
        prefix="/v1",
    )
    client = TestClient(app)
    r = client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}/shap"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code in (401, 403)


def test_route_pro_or_superuser_only() -> None:
    app = FastAPI()
    app.include_router(
        create_shap_analysis_router(),
        prefix="/v1",
    )
    app.dependency_overrides[get_current_user] = _general_user
    client = TestClient(app)
    r = client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}/shap"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code == 403


def test_route_rejects_inverted_window(
    superuser_client: TestClient,
) -> None:
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}/shap"
        "?period_start=2026-03-31&period_end=2026-01-01"
    )
    assert r.status_code == 400


def test_route_returns_503_when_shap_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    superuser_client: TestClient,
) -> None:
    """When ``compute_shap_for_trades`` raises RuntimeError
    (shap package missing), the route should surface 503."""
    monkeypatch.setattr(
        route_mod,
        "_load_snapshots",
        lambda **kw: [
            {"features_json": "{}", "realised_pnl_inr": 1.0} for _ in range(40)
        ],
    )
    monkeypatch.setattr(
        route_mod,
        "_train_blocking",
        lambda **kw: _fake_trained(),
    )

    def _raise_runtime(**_kw: Any) -> Any:
        raise RuntimeError("the 'shap' package is not installed")

    monkeypatch.setattr(
        route_mod,
        "_shap_blocking",
        _raise_runtime,
    )
    r = superuser_client.get(
        f"/v1/algo/strategies/{_STRATEGY_ID}/shap"
        "?period_start=2026-01-01&period_end=2026-03-31"
    )
    assert r.status_code == 503
    assert "shap" in r.json()["detail"].lower()
