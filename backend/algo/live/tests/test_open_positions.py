"""Tests for open_algo_positions."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.algo.live.open_positions import (
    open_algo_positions,
)


def _event_row(
    sym: str, side: str, qty: int,
    ts_ns: int, dry_run: bool = False,
) -> dict:
    import json
    return {
        "ts_ns": ts_ns,
        "payload_json": json.dumps({
            "symbol": sym,
            "side": side,
            "qty": qty,
            "dry_run": dry_run,
        }),
    }


@pytest.mark.asyncio
async def test_empty_when_no_events(monkeypatch):
    monkeypatch.setattr(
        "backend.algo.live.open_positions.query_iceberg_table",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.get_cache",
        lambda: None,
    )
    out = await open_algo_positions(uuid4())
    assert out == set()


@pytest.mark.asyncio
async def test_net_long_only_returned(monkeypatch):
    rows = [
        _event_row("A.NS", "BUY", 10, ts_ns=1),
        _event_row("B.NS", "BUY", 10, ts_ns=2),
        _event_row("C.NS", "BUY", 10, ts_ns=3),
        _event_row("B.NS", "SELL", 10, ts_ns=4),
    ]
    monkeypatch.setattr(
        "backend.algo.live.open_positions.query_iceberg_table",
        lambda *a, **kw: rows,
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.get_cache",
        lambda: None,
    )
    out = await open_algo_positions(uuid4())
    assert out == {"A.NS", "C.NS"}


@pytest.mark.asyncio
async def test_dry_run_fills_ignored(monkeypatch):
    rows = [
        _event_row("A.NS", "BUY", 10, ts_ns=1, dry_run=True),
        _event_row("B.NS", "BUY", 5, ts_ns=2),
    ]
    monkeypatch.setattr(
        "backend.algo.live.open_positions.query_iceberg_table",
        lambda *a, **kw: rows,
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.get_cache",
        lambda: None,
    )
    out = await open_algo_positions(uuid4())
    assert out == {"B.NS"}


@pytest.mark.asyncio
async def test_cache_hit_skips_iceberg(monkeypatch):
    import json
    fake_cache = MagicMock()
    fake_cache.get = MagicMock(
        return_value=json.dumps(["AAPL.NS", "MSFT"]),
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.get_cache",
        lambda: fake_cache,
    )
    iceberg_spy = MagicMock(
        side_effect=AssertionError("should not be called"),
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.query_iceberg_table",
        iceberg_spy,
    )
    out = await open_algo_positions(uuid4())
    assert out == {"AAPL.NS", "MSFT"}
    iceberg_spy.assert_not_called()
