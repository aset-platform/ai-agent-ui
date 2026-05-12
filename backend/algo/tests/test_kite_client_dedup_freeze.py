"""Tests for KiteClient pre-submit dedup + freeze chunking (PR #4).

Sibling file to ``test_kite_client_safety.py`` (PR #1) — kept separate
because PR #4 exercises a totally different gate (Redis dedup + freeze
SDK call) and the fixtures need a mocked Redis client too. Keeping PR
#1 tests untouched means CI signal stays scoped to the dedup/freeze
slice when this PR lands.

Covers spec §3.4 (pre-submit dedup) and §3.5 (freeze cache + chunking
with defensive bucket-keyed defaults).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.exceptions import (
    DuplicateOrderError,
    FreezeChunkExceedsDailyCapError,
)
from backend.algo.broker.kite_client import KiteClient

UTC = timezone.utc


# -----------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------


class FakeRedis:
    """In-memory stand-in for the subset of redis.Redis we use.

    Supports the exact methods called from PR #4 code paths:
        - set(key, val, nx=True, ex=N) — SETNX with TTL
        - hget(key, field)
        - hset(key, mapping={...})
        - expire(key, ttl)
        - exists(key)
        - get(key)
    """

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}

    def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def hset(
        self,
        key: str,
        mapping: dict[str, str] | None = None,
        **_kwargs,
    ) -> int:
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            for k, v in mapping.items():
                bucket[str(k)] = str(v)
        return len(bucket)

    def expire(self, key: str, ttl: int) -> bool:
        self.ttls[key] = ttl
        return key in self.hashes or key in self.strings

    def exists(self, key: str) -> int:
        return int(key in self.hashes or key in self.strings)


class FlakyRedis:
    """Raises on every call — simulates Redis outage."""

    def set(self, *_a, **_kw):  # noqa: D401
        raise ConnectionError("redis down")

    def get(self, *_a, **_kw):
        raise ConnectionError("redis down")

    def hget(self, *_a, **_kw):
        raise ConnectionError("redis down")

    def hset(self, *_a, **_kw):
        raise ConnectionError("redis down")

    def expire(self, *_a, **_kw):
        raise ConnectionError("redis down")

    def exists(self, *_a, **_kw):
        raise ConnectionError("redis down")


@pytest.fixture()
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture()
def kite_client(fake_redis):
    """KiteClient wired with mocked SDK + injected fake redis."""
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
    """Sane default place_order call; tests override specific kwargs."""
    kwargs = dict(
        tradingsymbol="ITC",
        exchange="NSE",
        transaction_type="BUY",
        quantity=8,
        order_type="LIMIT",
        price=307.35,
        last_price=307.30,
        last_price_ts=_fresh_ts(),
        liquidity_bucket="largecap",
        slippage_bps_applied=20,
        events_sink=events_buffer.append,
        strategy_id="strat-1",
        user_id="user-1",
    )
    kwargs.update(overrides)
    return client.place_order(**kwargs)


# -----------------------------------------------------------------
# 3.4 Pre-submit duplicate guard
# -----------------------------------------------------------------


@pytest.mark.algo_dedup_enabled
class TestPreSubmitDedup:
    def test_within_minute_duplicate_blocked(
        self, kite_client, events_buffer,
    ):
        """Same params inside the same minute → second call raises."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        # First call succeeds.
        _call_place(client, events_buffer)
        mock_kc.place_order.assert_called_once()
        # Second call (same minute, same params) blocked.
        with pytest.raises(DuplicateOrderError):
            _call_place(client, events_buffer)
        # SDK only called once total (no second submission).
        assert mock_kc.place_order.call_count == 1
        # order_duplicate_blocked event present.
        blocked = [
            e for e in events_buffer
            if e["type"] == "order_duplicate_blocked"
        ]
        assert len(blocked) == 1
        p = json.loads(blocked[0]["payload_json"])
        assert p["symbol"] == "ITC"
        assert p["side"] == "BUY"
        assert p["qty"] == 8

    def test_cross_minute_same_params_both_succeed(
        self, kite_client, events_buffer,
    ):
        """Bumping the minute_bucket lets the second call through."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        # First call at t0.
        with patch(
            "backend.algo.broker.redis_keys.time.time",
            return_value=1_700_000_000.0,
        ):
            _call_place(client, events_buffer)
        # Second call 70s later → different minute bucket.
        with patch(
            "backend.algo.broker.redis_keys.time.time",
            return_value=1_700_000_070.0,
        ):
            _call_place(client, events_buffer)
        assert mock_kc.place_order.call_count == 2

    def test_dry_run_skips_dedup_entirely(self, fake_redis, events_buffer):
        """Dry-run never consults Redis — back-to-back is fine."""
        with patch("backend.algo.broker.kite_client.KiteConnect"):
            client = KiteClient(
                api_key="k",
                access_token="t",
                dry_run=True,
                redis_client=fake_redis,
            )
            for _ in range(3):
                _call_place(client, events_buffer)
        # No dedup keys written.
        assert not any(
            k.startswith("algo:placeorder:dedup:")
            for k in fake_redis.strings
        )

    def test_ttl_zero_disables_dedup(
        self, kite_client, events_buffer,
    ):
        """ALGO_DEDUP_TTL_S=0 → same-minute repeats pass through."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        with patch.dict(
            "os.environ", {"ALGO_DEDUP_TTL_S": "0"},
        ):
            _call_place(client, events_buffer)
            _call_place(client, events_buffer)
        assert mock_kc.place_order.call_count == 2

    def test_redis_unavailable_graceful_degradation(
        self, events_buffer, caplog,
    ):
        """Redis raising → log warning, allow order (never fail-closed)."""
        with patch("backend.algo.broker.kite_client.KiteConnect"):
            client = KiteClient(
                api_key="k",
                access_token="t",
                dry_run=False,
                redis_client=FlakyRedis(),
            )
            mock_kc = MagicMock()
            mock_kc.place_order.return_value = {"order_id": "K_OK"}
            client._kc = mock_kc
            oid = _call_place(client, events_buffer)
        assert oid == "K_OK"
        mock_kc.place_order.assert_called_once()


# -----------------------------------------------------------------
# 3.5 Freeze cache + chunking
# -----------------------------------------------------------------


class TestFreezeCacheAndChunking:
    def test_cache_hit_skips_instruments_call(
        self, kite_client, events_buffer, fake_redis,
    ):
        """Pre-populated Redis hash → no SDK instruments() call."""
        from backend.algo.broker.freeze_cache import build_freeze_key
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        key = build_freeze_key()
        fake_redis.hset(key, mapping={"ITC": "1000"})
        # quantity < freeze_qty → no chunking, single submission.
        _call_place(client, events_buffer, quantity=8)
        mock_kc.instruments.assert_not_called()
        assert mock_kc.place_order.call_count == 1

    def test_cache_miss_populates_from_sdk(
        self, kite_client, events_buffer, fake_redis,
    ):
        """No hash → SDK instruments() called, hash populated."""
        from backend.algo.broker.freeze_cache import build_freeze_key
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        mock_kc.instruments.return_value = [
            {"tradingsymbol": "ITC", "freeze_qty": 1000},
            {"tradingsymbol": "TCS", "freeze_qty": 250},
        ]
        _call_place(client, events_buffer, quantity=8)
        mock_kc.instruments.assert_called_once_with("NSE")
        key = build_freeze_key()
        assert fake_redis.hget(key, "ITC") == "1000"
        assert fake_redis.hget(key, "TCS") == "250"

    def test_freeze_qty_null_falls_back_to_bucket_default(
        self, kite_client, events_buffer,
    ):
        """Kite returns freeze_qty=null → default for largecap = 500k."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        mock_kc.instruments.return_value = [
            {"tradingsymbol": "ITC", "freeze_qty": None},
        ]
        _call_place(
            client, events_buffer,
            quantity=8, liquidity_bucket="largecap",
        )
        # First-use fallback event emitted.
        fb = [
            e for e in events_buffer
            if e["type"] == "freeze_qty_fallback_applied"
        ]
        assert len(fb) == 1
        p = json.loads(fb[0]["payload_json"])
        assert p["symbol"] == "ITC"
        assert p["fallback_freeze_qty"] == 500_000
        assert p["bucket"] == "largecap"

    def test_freeze_qty_null_event_emitted_only_once_per_day(
        self, kite_client, events_buffer,
    ):
        """Second order on same ticker/date → no second fallback event.

        Use different qty per call so the dedup gate doesn't block
        the second submission (we're isolating the fallback-event
        suppression, not exercising dedup here).
        """
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        mock_kc.instruments.return_value = [
            {"tradingsymbol": "ITC", "freeze_qty": None},
        ]
        _call_place(client, events_buffer, quantity=8)
        _call_place(client, events_buffer, quantity=9)
        fb = [
            e for e in events_buffer
            if e["type"] == "freeze_qty_fallback_applied"
        ]
        assert len(fb) == 1

    def test_freeze_qty_zero_also_falls_back(
        self, kite_client, events_buffer,
    ):
        """freeze_qty=0 must trigger fallback path too."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        mock_kc.instruments.return_value = [
            {"tradingsymbol": "ITC", "freeze_qty": 0},
        ]
        _call_place(
            client, events_buffer,
            quantity=8, liquidity_bucket="midcap",
        )
        fb = [
            e for e in events_buffer
            if e["type"] == "freeze_qty_fallback_applied"
        ]
        assert len(fb) == 1
        p = json.loads(fb[0]["payload_json"])
        assert p["fallback_freeze_qty"] == 100_000

    def test_quantity_exceeds_freeze_qty_chunks(
        self, kite_client, events_buffer, fake_redis,
    ):
        """qty=3500 freeze=1000 → ceil(3.5)=4 chunks of 1000/1000/1000/500."""
        from backend.algo.broker.freeze_cache import build_freeze_key
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        fake_redis.hset(build_freeze_key(), mapping={"ITC": "1000"})
        _call_place(
            client, events_buffer,
            quantity=3500, daily_cap_remaining=10,
        )
        assert mock_kc.place_order.call_count == 4
        # Chunked event emitted exactly once.
        chunked = [
            e for e in events_buffer
            if e["type"] == "order_freeze_chunked"
        ]
        assert len(chunked) == 1
        p = json.loads(chunked[0]["payload_json"])
        assert p["symbol"] == "ITC"
        assert p["total_qty"] == 3500
        assert p["freeze_qty"] == 1000
        assert p["chunk_qtys"] == [1000, 1000, 1000, 500]
        # Each submission emitted with chunk_index + chunk_total.
        submitted = [
            e for e in events_buffer
            if e["type"] == "order_submitted_live"
        ]
        assert len(submitted) == 4
        for idx, ev in enumerate(submitted):
            payload = json.loads(ev["payload_json"])
            assert payload["context"]["chunk_index"] == idx
            assert payload["context"]["chunk_total"] == 4

    def test_chunks_exceeding_daily_cap_raises_before_submit(
        self, kite_client, events_buffer, fake_redis,
    ):
        """ceil(qty/freeze)=4 > remaining=2 → raise BEFORE any SDK call."""
        from backend.algo.broker.freeze_cache import build_freeze_key
        client, mock_kc = kite_client
        fake_redis.hset(build_freeze_key(), mapping={"ITC": "1000"})
        with pytest.raises(FreezeChunkExceedsDailyCapError):
            _call_place(
                client, events_buffer,
                quantity=3500, daily_cap_remaining=2,
            )
        mock_kc.place_order.assert_not_called()

    def test_quantity_within_freeze_no_chunking(
        self, kite_client, events_buffer, fake_redis,
    ):
        """qty <= freeze_qty → single submission, chunk_index=None."""
        from backend.algo.broker.freeze_cache import build_freeze_key
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        fake_redis.hset(build_freeze_key(), mapping={"ITC": "1000"})
        _call_place(client, events_buffer, quantity=8)
        assert mock_kc.place_order.call_count == 1
        chunked = [
            e for e in events_buffer
            if e["type"] == "order_freeze_chunked"
        ]
        assert chunked == []
        submitted = [
            e for e in events_buffer
            if e["type"] == "order_submitted_live"
        ]
        assert len(submitted) == 1
        p = json.loads(submitted[0]["payload_json"])
        assert p["context"]["chunk_index"] is None
        assert p["context"]["chunk_total"] is None

    def test_unknown_bucket_falls_back_to_unknown_default(
        self, kite_client, events_buffer,
    ):
        """liquidity_bucket=None → defaults table key 'unknown' = 50k."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "K1"}
        mock_kc.instruments.return_value = [
            {"tradingsymbol": "ITC", "freeze_qty": None},
        ]
        _call_place(
            client, events_buffer,
            quantity=8, liquidity_bucket=None,
        )
        fb = [
            e for e in events_buffer
            if e["type"] == "freeze_qty_fallback_applied"
        ]
        assert len(fb) == 1
        p = json.loads(fb[0]["payload_json"])
        assert p["bucket"] == "unknown"
        assert p["fallback_freeze_qty"] == 50_000
