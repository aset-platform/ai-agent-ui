"""Async unit tests for InstrumentsRepo."""
from __future__ import annotations

import pytest

from backend.algo.instruments.repo import InstrumentsRepo


class _StubSession:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def execute(self, q, params=None):
        sql = str(q)
        params = params or {}

        class _Res:
            def __init__(self, items):
                self._items = items

            def mappings(self):
                return self

            def all(self):
                return self._items

            def first(self):
                return self._items[0] if self._items else None

        if "INSERT INTO algo.instruments" in sql:
            tok = params["instrument_token"]
            existing = [
                r for r in self.rows
                if r["instrument_token"] == tok
            ]
            if existing:
                existing[0].update(params)
                return _Res(existing)
            self.rows.append(dict(params))
            return _Res([self.rows[-1]])
        if "SELECT COUNT(*)" in sql:
            return _Res([{"c": len(self.rows)}])
        if "SELECT instrument_token" in sql:
            limit = params.get("limit", len(self.rows))
            offset = params.get("offset", 0)
            slice_ = self.rows[offset:offset + limit]
            return _Res([dict(r) for r in slice_])
        return _Res([])

    async def commit(self):
        return None


def _row(token: int, sym: str, exchange: str = "NSE") -> dict:
    return {
        "instrument_token": token,
        "tradingsymbol": sym,
        "exchange": exchange,
        "segment": f"{exchange}-EQ",
        "lot_size": 1,
        "tick_size": 0.05,
        "our_ticker": None,
    }


@pytest.mark.asyncio
async def test_bulk_upsert_inserts_new_rows():
    sess = _StubSession()
    repo = InstrumentsRepo()
    n = await repo.bulk_upsert(sess, [_row(1, "RELIANCE"), _row(2, "TCS")])
    assert n == 2
    assert len(sess.rows) == 2


@pytest.mark.asyncio
async def test_bulk_upsert_updates_existing():
    sess = _StubSession()
    repo = InstrumentsRepo()
    await repo.bulk_upsert(sess, [_row(1, "RELIANCE")])
    # Re-upsert with changed lot_size
    r = _row(1, "RELIANCE")
    r["lot_size"] = 50
    await repo.bulk_upsert(sess, [r])
    assert sess.rows[0]["lot_size"] == 50
    assert len(sess.rows) == 1


@pytest.mark.asyncio
async def test_count_instruments_returns_total():
    sess = _StubSession()
    repo = InstrumentsRepo()
    await repo.bulk_upsert(
        sess, [_row(i, f"S{i}") for i in range(7)],
    )
    n = await repo.count_instruments(sess)
    assert n == 7


@pytest.mark.asyncio
async def test_list_instruments_paginates():
    sess = _StubSession()
    repo = InstrumentsRepo()
    await repo.bulk_upsert(
        sess, [_row(i, f"S{i}") for i in range(20)],
    )
    page = await repo.list_instruments(sess, limit=5, offset=5)
    assert len(page) == 5
