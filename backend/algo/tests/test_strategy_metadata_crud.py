"""HTTP round-trip for ``applicable_regimes`` via strategy CRUD.

Mirrors the in-memory async-session stub from
``test_strategies_routes.py`` and extends it to cover the
``algo.strategy_metadata`` table so POST/PUT/GET round-trips can be
asserted in isolation (no PG required).
"""
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
            "left": {"feature": "regime_label"},
            "op": "==",
            "right": {"literal": "bull"},
        },
        "then": {"type": "set_target_weight", "weight": 0.20},
        "else": {"type": "exit", "scope": "all_open"},
    },
    "risk": {
        "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
        "portfolio": {
            "max_exposure_pct": 80, "max_concentration_pct": 25,
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

    rows: dict = {"items": [], "metadata": {}}

    class _Stub:
        async def execute(self, q, params=None):  # noqa: ANN001
            sql = str(q).strip()
            params = params or {}

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

            # ---- algo.strategies ---------------------------------
            if sql.startswith("SELECT id, name, mode"):
                if "AND archived_at IS NULL" in sql:
                    return _Res([
                        r for r in rows["items"]
                        if r.get("archived_at") is None
                    ])
                return _Res(rows["items"])
            if sql.startswith("SELECT id, name, ast_json"):
                sid = params.get("sid")
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
                sid = params.get("sid")
                hit = [
                    r for r in rows["items"]
                    if str(r.get("id")) == str(sid)
                ]
                for h in hit:
                    h["name"] = params["name"]
                return _Res(hit)

            # ---- algo.strategy_metadata --------------------------
            if sql.startswith("INSERT INTO algo.strategy_metadata"):
                rows["metadata"][str(params["sid"])] = {
                    "applicable_regimes": list(params["regimes"]),
                    "expected_edge": params["edge"],
                    "description": params["descr"],
                }
                return _Res([])
            if sql.startswith("SELECT applicable_regimes"):
                row = rows["metadata"].get(str(params["sid"]))
                return _Res([row] if row else [])
            if sql.startswith("DELETE FROM algo.strategy_metadata"):
                rows["metadata"].pop(str(params["sid"]), None)
                return _Res([])

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
    monkeypatch.setattr(
        strat_mod, "_get_session_factory", _FakeFactory,
    )
    return app


def test_create_with_applicable_regimes(app: FastAPI) -> None:
    client = TestClient(app)
    body = {
        "payload": _VALID_PAYLOAD,
        "applicable_regimes": ["bull", "sideways"],
    }
    r = client.post("/v1/algo/strategies", json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    r = client.get(f"/v1/algo/strategies/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applicable_regimes"] == ["bull", "sideways"]
    assert "strategy" in body
    assert body["strategy"]["name"] == "Bullish + Quality v1"


def test_default_regime_agnostic(app: FastAPI) -> None:
    client = TestClient(app)
    body = {"payload": _VALID_PAYLOAD}
    r = client.post("/v1/algo/strategies", json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.get(f"/v1/algo/strategies/{sid}")
    assert r.status_code == 200, r.text
    assert set(r.json()["applicable_regimes"]) == {
        "bull", "sideways", "bear",
    }


def test_update_overwrites_metadata(app: FastAPI) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/algo/strategies",
        json={
            "payload": _VALID_PAYLOAD,
            "applicable_regimes": ["bull"],
        },
    )
    sid = r.json()["id"]
    new_payload = deepcopy(_VALID_PAYLOAD)
    new_payload["id"] = sid
    r = client.put(
        f"/v1/algo/strategies/{sid}",
        json={
            "payload": new_payload,
            "applicable_regimes": ["bear"],
        },
    )
    assert r.status_code == 204, r.text
    r = client.get(f"/v1/algo/strategies/{sid}")
    assert r.json()["applicable_regimes"] == ["bear"]
