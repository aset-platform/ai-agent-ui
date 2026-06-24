"""Tests for transactional freeze-chunk partial-failure (C1).

When a multi-chunk (freeze-split) order has some chunks already
live on the exchange and a later chunk raises, ``place_order`` must
NOT let the bare SDK error propagate as a generic failure (which the
runtime would blind-retry as the full qty → duplicate exposure).
Instead it raises ``PartialChunkPlacementError`` carrying the
already-live order ids and emits ``order_partial_chunk_failure``.

Sibling to ``backend/algo/tests/test_kite_client_dedup_freeze.py``;
kept in the broker ``tests/`` package because it exercises only the
chunk-loop failure slice and mocks ``_place_single_chunk`` directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.exceptions import PartialChunkPlacementError
from backend.algo.broker.freeze_cache import build_freeze_key
from backend.algo.broker.kite_client import KiteClient

UTC = timezone.utc


class FakeRedis:
    """Minimal in-memory redis for the freeze-cache hash read."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    def hget(self, key: str, field: str):
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key: str):
        return self.hashes.get(key, {})

    def get(self, *_a, **_kw):
        return None

    def set(self, *_a, **_kw):
        return True

    def exists(self, *_a, **_kw):
        return False


@pytest.fixture()
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture()
def kite_client(fake_redis):
    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        client = KiteClient(
            api_key="test_api_key",
            access_token="test_access_token",
            dry_run=False,
            redis_client=fake_redis,
        )
        client._kc = kc_instance
        yield client, kc_instance


@pytest.fixture()
def events_buffer() -> list:
    return []


def _fresh_ts():
    return datetime.now(UTC) - timedelta(seconds=1)


def _call_place(client, events_buffer, **overrides):
    kwargs = dict(
        tradingsymbol="ITC",
        exchange="NSE",
        transaction_type="BUY",
        quantity=3500,
        order_type="LIMIT",
        price=307.35,
        last_price=307.30,
        last_price_ts=_fresh_ts(),
        liquidity_bucket="largecap",
        slippage_bps_applied=20,
        events_sink=events_buffer.append,
        strategy_id="strat-1",
        user_id="user-1",
        daily_cap_remaining=10,
    )
    kwargs.update(overrides)
    return client.place_order(**kwargs)


def test_partial_chunk_failure_raises_with_placed_ids(
    kite_client, events_buffer, fake_redis,
):
    """Chunks 0,1 succeed; chunk 2 raises mid-loop.

    ``place_order`` must raise ``PartialChunkPlacementError`` whose
    ``placed_order_ids`` are exactly the two live chunk ids and
    ``failed_chunk`` is 2, and emit ``order_partial_chunk_failure``.
    """
    client, _mock_kc = kite_client
    # freeze_qty=1000, qty=3500 → 4 chunks (1000/1000/1000/500).
    fake_redis.hset(build_freeze_key(), mapping={"ITC": "1000"})

    side_effects = [
        "OID0",
        "OID1",
        RuntimeError("kite rejected chunk 2"),
        "OID3",
    ]
    with patch.object(
        client,
        "_place_single_chunk",
        side_effect=side_effects,
    ) as mock_chunk:
        with pytest.raises(PartialChunkPlacementError) as exc_info:
            _call_place(client, events_buffer)

    err = exc_info.value
    assert err.placed_order_ids == ["OID0", "OID1"]
    assert err.failed_chunk == 2
    assert isinstance(err.cause, RuntimeError)
    # Loop stopped at the failing chunk — chunk 3 never attempted.
    assert mock_chunk.call_count == 3

    failures = [
        e for e in events_buffer
        if e["type"] == "order_partial_chunk_failure"
    ]
    assert len(failures) == 1
    payload = json.loads(failures[0]["payload_json"])
    assert payload["symbol"] == "ITC"
    assert payload["placed_order_ids"] == ["OID0", "OID1"]
    assert payload["failed_chunk"] == 2
    assert payload["total_chunks"] == 4


def test_first_chunk_failure_raises_with_empty_placed(
    kite_client, events_buffer, fake_redis,
):
    """Chunk 0 raises → no live chunks, empty placed_order_ids."""
    client, _mock_kc = kite_client
    fake_redis.hset(build_freeze_key(), mapping={"ITC": "1000"})

    with patch.object(
        client,
        "_place_single_chunk",
        side_effect=RuntimeError("kite rejected chunk 0"),
    ):
        with pytest.raises(PartialChunkPlacementError) as exc_info:
            _call_place(client, events_buffer)

    err = exc_info.value
    assert err.placed_order_ids == []
    assert err.failed_chunk == 0
    failures = [
        e for e in events_buffer
        if e["type"] == "order_partial_chunk_failure"
    ]
    assert len(failures) == 1


def test_all_chunks_succeed_returns_first_id(
    kite_client, events_buffer, fake_redis,
):
    """No failure → returns first chunk id, no partial event."""
    client, _mock_kc = kite_client
    fake_redis.hset(build_freeze_key(), mapping={"ITC": "1000"})

    with patch.object(
        client,
        "_place_single_chunk",
        side_effect=["OID0", "OID1", "OID2", "OID3"],
    ):
        result = _call_place(client, events_buffer)

    assert result == "OID0"
    failures = [
        e for e in events_buffer
        if e["type"] == "order_partial_chunk_failure"
    ]
    assert failures == []
