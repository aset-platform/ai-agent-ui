# backend/algo/tests/test_strategies_routes.py
"""Endpoint smoke tests for /v1/algo/strategies/*."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.strategies import create_strategies_router


_VALID_PAYLOAD = {
    "id": str(uuid4()),
    "name": "Bullish + Quality v1",
    "universe": {
        "type": "scope", "scope": "watchlist",
        "filter": {"ticker_type": ["stock"], "market": "india"},
    },
    "schedule": {
        "type": "bar_close", "interval": "1d", "time": "15:25 IST",
    },
    "rebalance": {"type": "daily", "max_positions": 10},
    "root": {
        "type": "if",
        "cond": {
            "type": "compare",
            "left": {"feature": "today_ltp"},
            "op": ">",
            "right": {"feature": "sma_50"},
        },
        "then": {"type": "set_target_weight", "weight": 0.20},
        "else": {"type": "exit", "scope": "all_open"},
    },
    "risk": {
        "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
        "portfolio": {
            "max_exposure_pct": 80,
            "max_concentration_pct": 25,
        },
        "daily": {"max_loss_pct": 2, "max_open_positions": 10},
    },
}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(create_strategies_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="11111111-1111-1111-1111-111111111111",
        email="t@t",
        role="superuser",
    )

    # In-memory async-session stub.
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

                def __iter__(self):
                    return iter(self._items)

                def first(self):
                    return self._items[0] if self._items else None

                @property
                def rowcount(self):
                    return len(self._items)

            if sql.startswith("SELECT id, name, mode"):
                # Respect archived_at IS NULL filter unless
                # the SQL includes no such filter (include_archived).
                if "AND archived_at IS NULL" in sql:
                    return _Res([
                        r for r in rows["items"]
                        if r.get("archived_at") is None
                    ])
                return _Res(rows["items"])
            if sql.startswith("SELECT id, name, ast_json"):
                sid = params.get("sid") if params else None
                hit = [
                    r for r in rows["items"]
                    if str(r.get("id")) == str(sid)
                ]
                return _Res(hit)
            if sql.startswith("INSERT INTO algo.strategies"):
                rows["items"].append({
                    "id": params["id"],
                    "name": params["name"],
                    "ast_json": _VALID_PAYLOAD,
                    "mode": "draft",
                    "status": "active",
                    "created_at": None,
                    "updated_at": None,
                    "archived_at": None,
                })
                return _Res([])
            if sql.startswith("UPDATE algo.strategies SET name"):
                sid = params.get("sid") if params else None
                hit = [
                    r for r in rows["items"]
                    if str(r.get("id")) == str(sid)
                ]
                for h in hit:
                    h["name"] = params["name"]
                return _Res(hit)
            if sql.startswith(
                "UPDATE algo.strategies SET archived_at"
            ):
                sid = params.get("sid") if params else None
                hit = [
                    r for r in rows["items"]
                    if str(r.get("id")) == str(sid)
                    and r.get("archived_at") is None
                ]
                for h in hit:
                    h["archived_at"] = params["now"]
                return _Res(hit)
            if sql.startswith("DELETE FROM algo.strategies"):
                sid = params.get("sid") if params else None
                before = len(rows["items"])
                rows["items"] = [
                    r for r in rows["items"]
                    if not (
                        str(r.get("id")) == str(sid)
                        and r.get("archived_at") is not None
                    )
                ]
                return _Res(
                    [None] * (before - len(rows["items"]))
                )
            return _Res([])

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class _FakeFactory:
        def __call__(self):
            return _Stub()

    import backend.algo.routes.strategies as strat_mod
    monkeypatch.setattr(strat_mod, "_get_session_factory", _FakeFactory)
    return app


def test_post_then_list(app: FastAPI):
    client = TestClient(app)
    r = client.post(
        "/v1/algo/strategies",
        json={"payload": _VALID_PAYLOAD},
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    listing = client.get("/v1/algo/strategies").json()
    assert any(s["id"] == new_id for s in listing["strategies"])


def test_post_invalid_ast_returns_400(app: FastAPI):
    client = TestClient(app)
    bad = deepcopy(_VALID_PAYLOAD)
    bad["root"]["cond"]["left"]["feature"] = "not_a_feature"
    r = client.post(
        "/v1/algo/strategies", json={"payload": bad},
    )
    assert r.status_code == 400


def test_get_404_on_missing(app: FastAPI):
    client = TestClient(app)
    r = client.get(f"/v1/algo/strategies/{uuid4()}")
    assert r.status_code == 404


def test_clone_returns_fresh_id_and_suffixed_name(app: FastAPI):
    """Cloning copies the AST under a fresh row id with a
    ``(Copy)`` name suffix — see backend/algo/strategy/repo.py
    ``create_strategy``, which re-mints the uuid regardless of
    the incoming AST id."""
    client = TestClient(app)
    src = client.post(
        "/v1/algo/strategies", json={"payload": _VALID_PAYLOAD},
    )
    assert src.status_code == 201, src.text
    source_id = src.json()["id"]

    cloned = client.post(
        f"/v1/algo/strategies/{source_id}/clone",
    )
    assert cloned.status_code == 201, cloned.text
    new_id = cloned.json()["id"]
    assert new_id != source_id

    listing = client.get("/v1/algo/strategies").json()
    names = {s["id"]: s["name"] for s in listing["strategies"]}
    assert names[new_id] == f"{_VALID_PAYLOAD['name']} (Copy)"


def test_clone_missing_source_returns_404(app: FastAPI):
    client = TestClient(app)
    r = client.post(f"/v1/algo/strategies/{uuid4()}/clone")
    assert r.status_code == 404


def test_archive_then_list_excludes(app: FastAPI):
    client = TestClient(app)
    r = client.post(
        "/v1/algo/strategies", json={"payload": _VALID_PAYLOAD},
    )
    new_id = r.json()["id"]
    r = client.delete(f"/v1/algo/strategies/{new_id}")
    assert r.status_code == 204
    listing = client.get("/v1/algo/strategies").json()
    assert all(s["id"] != new_id for s in listing["strategies"])
