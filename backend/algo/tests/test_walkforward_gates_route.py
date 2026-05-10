"""Smoke tests for GET /v1/algo/walkforward/runs/{id}/gates."""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import pro_or_superuser
from auth.models.response import UserContext
from backend.algo.routes.walkforward import (
    create_walkforward_router,
    _get_session_factory,
)


def _build_app(summary_json: dict | None) -> FastAPI:
    """Build a tiny FastAPI app with the walkforward router and
    monkey-patched session factory + auth."""
    app = FastAPI()
    app.include_router(create_walkforward_router(), prefix="/v1")

    user = UserContext(
        user_id=str(uuid4()),
        email="t@t.com",
        role="superuser",
        tier="premium",
    )
    app.dependency_overrides[pro_or_superuser] = lambda: user

    class _StubSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self, *a, **kw):
            class _Result:
                def mappings(self_):
                    class _M:
                        def first(self__):
                            if summary_json is None:
                                return None
                            return {"summary_json": summary_json}
                    return _M()
            return _Result()

    def _factory():
        return _StubSession()

    from backend.algo.routes import walkforward as wf_mod
    wf_mod._get_session_factory = lambda: _factory  # type: ignore
    return app


def test_gates_all_passed():
    summary = {
        "aggregate": {
            "gates_passed": {
                "max_dd_ok": True,
                "recovery_ok": True,
                "per_regime_non_neg": True,
                "dsr_ok": True,
                "pbo_ok": True,
            }
        }
    }
    app = _build_app(summary)
    with TestClient(app) as client:
        r = client.get(f"/v1/algo/walkforward/runs/{uuid4()}/gates")
    assert r.status_code == 200
    body = r.json()
    assert body["overall_pass"] is True
    assert body["recommendations"] == []


def test_gates_partial_fail_returns_recommendations():
    summary = {
        "aggregate": {
            "gates_passed": {
                "max_dd_ok": True,
                "recovery_ok": False,
                "per_regime_non_neg": True,
                "dsr_ok": True,
                "pbo_ok": False,
            }
        }
    }
    app = _build_app(summary)
    with TestClient(app) as client:
        r = client.get(f"/v1/algo/walkforward/runs/{uuid4()}/gates")
    assert r.status_code == 200
    body = r.json()
    assert body["overall_pass"] is False
    # Two failed gates → two recommendations
    assert len(body["recommendations"]) == 2


def test_gates_legacy_run_recommends_rerun():
    """Pre-REGIME-5 summary_json without gates_passed."""
    summary = {"aggregate": {"avg_pnl_pct": "1.5"}}
    app = _build_app(summary)
    with TestClient(app) as client:
        r = client.get(f"/v1/algo/walkforward/runs/{uuid4()}/gates")
    assert r.status_code == 200
    body = r.json()
    assert body["overall_pass"] is False
    assert len(body["recommendations"]) == 1
    assert "predates" in body["recommendations"][0]


def test_gates_404_when_run_not_found():
    app = _build_app(None)
    with TestClient(app) as client:
        r = client.get(f"/v1/algo/walkforward/runs/{uuid4()}/gates")
    assert r.status_code == 404
