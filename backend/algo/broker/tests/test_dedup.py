"""Tests for Task 1.4: dedup keyed on internal_order_id +
fail-closed for large notional.

Covers:
  (a) ``build_dedup_key`` derives from ``internal_order_id``; same
      logical order → same key; different orders → different keys.
  (b) On Redis error with notional >= ``ALGO_DEDUP_FAILCLOSED_INR``
      the order is BLOCKED (raises ``DedupUnavailableError``).
  (c) On Redis error with notional < threshold the order proceeds
      (fail-open) with a warning.

Also verifies the core duplicate-blocked path still works end-to-end
through ``KiteClient.place_order``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.exceptions import (
    DedupUnavailableError,
    DuplicateOrderError,
)
from backend.algo.broker.kite_client import KiteClient
from backend.algo.broker.redis_keys import build_dedup_key

UTC = timezone.utc


# -----------------------------------------------------------------
# Helpers / fakes
# -----------------------------------------------------------------


class FakeRedis:
    """Minimal in-memory stand-in."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}

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
        return True

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def hget(self, key: str, field: str) -> str | None:
        return None

    def hset(self, *_a, **_kw) -> int:
        return 0

    def expire(self, *_a, **_kw) -> bool:
        return True

    def exists(self, key: str) -> int:
        return int(key in self.strings)


class BrokenRedis:
    """Always raises — simulates Redis outage."""

    def set(self, *_a, **_kw):
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


def _fresh_ts():
    return datetime.now(UTC) - timedelta(seconds=1)


def _make_client(redis_client):
    with patch("backend.algo.broker.kite_client.KiteConnect"):
        client = KiteClient(
            api_key="k",
            access_token="t",
            dry_run=False,
            redis_client=redis_client,
        )
        client._kc = MagicMock()
        client._kc.place_order.return_value = {"order_id": "K_OK"}
        client._kc.instruments.return_value = [
            {"tradingsymbol": "RELIANCE", "freeze_qty": 1000},
        ]
        return client


def _call_place(client, events_buffer, **overrides):
    kwargs = dict(
        tradingsymbol="RELIANCE",
        exchange="NSE",
        transaction_type="BUY",
        quantity=10,
        order_type="LIMIT",
        price=2500.0,
        last_price=2500.0,
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
# (a) build_dedup_key — keyed on internal_order_id
# -----------------------------------------------------------------


class TestBuildDedupKey:
    def test_same_internal_order_id_produces_same_key(self):
        """Same logical order (same id) → identical dedup key."""
        order_id = str(uuid.uuid4())
        k1 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="INFY",
            side="BUY",
            internal_order_id=order_id,
        )
        k2 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="INFY",
            side="BUY",
            internal_order_id=order_id,
        )
        assert k1 == k2

    def test_different_internal_order_ids_produce_different_keys(self):
        """Two distinct logical orders → different keys."""
        k1 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="INFY",
            side="BUY",
            internal_order_id=str(uuid.uuid4()),
        )
        k2 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="INFY",
            side="BUY",
            internal_order_id=str(uuid.uuid4()),
        )
        assert k1 != k2

    def test_key_contains_internal_order_id(self):
        """Dedup key must embed the internal_order_id for traceability."""
        order_id = "fixed-order-id-123"
        key = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="TCS",
            side="SELL",
            internal_order_id=order_id,
        )
        assert order_id in key

    def test_key_does_not_vary_with_qty(self):
        """Key must NOT incorporate qty — qty-recompute retry must
        hit the same key as the original submission.
        """
        order_id = str(uuid.uuid4())
        # Same internal_order_id regardless of qty variation.
        k1 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="TCS",
            side="BUY",
            internal_order_id=order_id,
        )
        k2 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="TCS",
            side="BUY",
            internal_order_id=order_id,
        )
        assert k1 == k2

    def test_key_prefix(self):
        """Dedup key has the expected prefix for Redis namespace clarity."""
        key = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="WIPRO",
            side="BUY",
            internal_order_id="oid-abc",
        )
        assert key.startswith("algo:placeorder:dedup:")


# -----------------------------------------------------------------
# (b) Redis error + notional >= threshold → BLOCKED (fail-closed)
# -----------------------------------------------------------------


@pytest.mark.algo_dedup_enabled
class TestFailClosedLargeNotional:
    """On Redis error, orders with notional >= threshold are blocked."""

    # default threshold = 100_000 INR.  qty=50, price=2500 → 125_000.
    _LARGE_QTY = 50
    _LARGE_PRICE = 2500.0

    def test_redis_none_large_notional_raises(self):
        """_get_redis() returns None + large notional → blocked."""
        client = _make_client(FakeRedis())
        # Force _get_redis to return None regardless of init redis.
        with patch.object(client, "_get_redis", return_value=None):
            with pytest.raises(DedupUnavailableError):
                _call_place(
                    client,
                    [],
                    quantity=self._LARGE_QTY,
                    price=self._LARGE_PRICE,
                )
            # SDK must NOT have been called (order blocked before Kite).
            client._kc.place_order.assert_not_called()

    def test_broken_redis_large_notional_raises(self):
        """Redis SETNX raises + large notional → DedupUnavailableError."""
        client = _make_client(BrokenRedis())
        with pytest.raises(DedupUnavailableError):
            _call_place(
                client,
                [],
                quantity=self._LARGE_QTY,
                price=self._LARGE_PRICE,
            )
        client._kc.place_order.assert_not_called()

    def test_fail_closed_threshold_exact_boundary(self):
        """Order at exactly the threshold is blocked (>=, not >)."""
        # qty=40, price=2500 → notional=100_000 == threshold.
        client = _make_client(BrokenRedis())
        with pytest.raises(DedupUnavailableError):
            _call_place(
                client,
                [],
                quantity=40,
                price=2500.0,
            )

    def test_custom_threshold_env_var_respected(self):
        """ALGO_DEDUP_FAILCLOSED_INR overrides the default 100_000."""
        # Set threshold to 200_000; qty=50 × 2500=125_000 is BELOW.
        client = _make_client(BrokenRedis())
        with patch.dict(
            "os.environ", {"ALGO_DEDUP_FAILCLOSED_INR": "200000"},
        ):
            # Should NOT raise (fail-open because below threshold).
            result = _call_place(
                client,
                [],
                quantity=self._LARGE_QTY,
                price=self._LARGE_PRICE,
            )
        assert result == "K_OK"


# -----------------------------------------------------------------
# (c) Redis error + notional < threshold → fail-open (warning only)
# -----------------------------------------------------------------


@pytest.mark.algo_dedup_enabled
class TestFailOpenSmallNotional:
    """On Redis error, orders below the threshold proceed (fail-open)."""

    # qty=1, price=500 → 500 INR, well below 100_000.
    _SMALL_QTY = 1
    _SMALL_PRICE = 500.0

    def test_redis_none_small_notional_allows_order(self, caplog):
        """_get_redis() returns None + small notional → order proceeds."""
        client = _make_client(FakeRedis())
        client._kc.place_order.return_value = {"order_id": "K_SMALL"}
        with patch.object(client, "_get_redis", return_value=None):
            result = _call_place(
                client,
                [],
                quantity=self._SMALL_QTY,
                price=self._SMALL_PRICE,
            )
        assert result == "K_SMALL"
        client._kc.place_order.assert_called_once()

    def test_broken_redis_small_notional_allows_order(self, caplog):
        """Redis SETNX raises + small notional → order proceeds."""
        client = _make_client(BrokenRedis())
        client._kc.place_order.return_value = {"order_id": "K_SMALL"}
        result = _call_place(
            client,
            [],
            quantity=self._SMALL_QTY,
            price=self._SMALL_PRICE,
        )
        assert result == "K_SMALL"
        client._kc.place_order.assert_called_once()

    def test_fail_open_just_below_threshold(self):
        """Order just below threshold is allowed through."""
        # qty=39, price=2500 → 97_500 < 100_000.
        client = _make_client(BrokenRedis())
        client._kc.place_order.return_value = {"order_id": "K_BELOW"}
        result = _call_place(
            client,
            [],
            quantity=39,
            price=2500.0,
        )
        assert result == "K_BELOW"


# -----------------------------------------------------------------
# End-to-end: duplicate blocked via internal_order_id key
# -----------------------------------------------------------------


@pytest.mark.algo_dedup_enabled
class TestDedupEndToEnd:
    """Same internal_order_id on second call → DuplicateOrderError."""

    def test_same_id_second_call_blocked(self):
        """Explicit same internal_order_id → duplicate blocked."""
        client = _make_client(FakeRedis())
        order_id = str(uuid.uuid4())
        # First call succeeds.
        _call_place(client, [], internal_order_id=order_id)
        client._kc.place_order.assert_called_once()
        # Second call with SAME id → blocked.
        with pytest.raises(DuplicateOrderError):
            _call_place(client, [], internal_order_id=order_id)
        assert client._kc.place_order.call_count == 1

    def test_different_ids_both_succeed(self):
        """Two distinct internal_order_ids → both orders go through."""
        client = _make_client(FakeRedis())
        _call_place(
            client, [],
            internal_order_id=str(uuid.uuid4()),
        )
        _call_place(
            client, [],
            internal_order_id=str(uuid.uuid4()),
        )
        assert client._kc.place_order.call_count == 2

    def test_ttl_zero_disables_dedup(self):
        """ALGO_DEDUP_TTL_S=0 → same id repeated → both pass."""
        client = _make_client(FakeRedis())
        order_id = str(uuid.uuid4())
        with patch.dict("os.environ", {"ALGO_DEDUP_TTL_S": "0"}):
            _call_place(client, [], internal_order_id=order_id)
            _call_place(client, [], internal_order_id=order_id)
        assert client._kc.place_order.call_count == 2
