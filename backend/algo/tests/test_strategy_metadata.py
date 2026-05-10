"""Round-trip tests for the strategy_metadata PG repo.

The PG side is exercised against a small in-memory async-session
stub mirroring the pattern used in test_strategies_routes.py — the
real Alembic migration is asserted by import alone (the file must
exist and load cleanly) plus integration via the running container
(verified out-of-band by ``alembic upgrade head``).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.algo.strategy.metadata_repo import (
    StrategyMetadata,
    delete_metadata,
    get_metadata,
    upsert_metadata,
)


class _FakeRes:
    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items or []

    def mappings(self) -> "_FakeRes":
        return self

    def first(self) -> dict | None:
        return self._items[0] if self._items else None


class _FakeSession:
    """In-memory stand-in for ``AsyncSession`` covering INSERT
    ON CONFLICT, SELECT, and DELETE for one PG table."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def execute(self, q, params=None):  # noqa: ANN001
        sql = str(q).strip()
        params = params or {}
        if sql.startswith("INSERT INTO algo.strategy_metadata"):
            self.rows[params["sid"]] = {
                "applicable_regimes": list(params["regimes"]),
                "expected_edge": params["edge"],
                "description": params["descr"],
            }
            return _FakeRes()
        if sql.startswith("SELECT applicable_regimes"):
            row = self.rows.get(params["sid"])
            return _FakeRes([row] if row else [])
        if sql.startswith("DELETE FROM algo.strategy_metadata"):
            self.rows.pop(params["sid"], None)
            return _FakeRes()
        return _FakeRes()

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_upsert_then_get() -> None:
    s = _FakeSession()
    sid = uuid4()
    await upsert_metadata(
        s, sid,
        StrategyMetadata(
            applicable_regimes=["bull", "sideways"],
            expected_edge=0.18,
            description="bull/sideways momentum strategy",
        ),
    )
    got = await get_metadata(s, sid)
    assert got is not None
    assert got.applicable_regimes == ["bull", "sideways"]
    assert float(got.expected_edge) == pytest.approx(0.18)
    assert got.description == "bull/sideways momentum strategy"


@pytest.mark.asyncio
async def test_get_missing_returns_none() -> None:
    s = _FakeSession()
    got = await get_metadata(s, uuid4())
    assert got is None


@pytest.mark.asyncio
async def test_upsert_overwrites_existing() -> None:
    s = _FakeSession()
    sid = uuid4()
    await upsert_metadata(
        s, sid,
        StrategyMetadata(applicable_regimes=["bull"]),
    )
    await upsert_metadata(
        s, sid,
        StrategyMetadata(applicable_regimes=["bear"], description="x"),
    )
    got = await get_metadata(s, sid)
    assert got is not None
    assert got.applicable_regimes == ["bear"]
    assert got.description == "x"


@pytest.mark.asyncio
async def test_delete_metadata() -> None:
    s = _FakeSession()
    sid = uuid4()
    await upsert_metadata(s, sid, StrategyMetadata())
    await delete_metadata(s, sid)
    assert await get_metadata(s, sid) is None


def test_default_regime_agnostic() -> None:
    md = StrategyMetadata()
    assert md.applicable_regimes == ["bull", "sideways", "bear"]
    assert md.expected_edge is None
    assert md.description == ""
