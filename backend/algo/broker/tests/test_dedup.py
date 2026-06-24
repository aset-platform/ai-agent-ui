"""Tests for Task 1.4: content-addressed dedup key (symbol/side/
minute, no qty) + fail-closed for large notional.

Covers:
  (a) ``build_dedup_key`` is content-addressed on
      ``(user, strategy, symbol, side, minute_bucket)``. Same params
      in the same minute → same key; different minute → different
      key; qty does NOT affect the key.
  (b) On Redis error with notional >= ``ALGO_DEDUP_FAILCLOSED_INR``
      the order is BLOCKED (raises ``DedupUnavailableError``).
  (c) On Redis error with notional < threshold the order proceeds
      (fail-open) with a warning.

Also verifies the cross-call duplicate-blocked path still works
end-to-end through ``KiteClient.place_order`` (same signal firing
twice in the same minute → second blocked).

The dedup gate is enabled by ``ALGO_DEDUP_TTL_S > 0``. Tests that
rely on the gate set ``ALGO_DEDUP_TTL_S=60`` explicitly via
``patch.dict`` rather than depending on the ``algo_dedup_enabled``
marker (whose fixture does not cover ``broker/tests``).
"""
from __future__ import annotations

import os
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
# (a) build_dedup_key — content-addressed (symbol/side/minute,
#     no qty)
# -----------------------------------------------------------------


class TestBuildDedupKey:
    def test_same_params_same_minute_produce_same_key(self):
        """Same (user,strategy,symbol,side) within the same minute
        bucket → identical dedup key (cross-call duplicate guard).
        """
        now = 1_700_000_000.0  # arbitrary fixed instant
        k1 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="INFY",
            side="BUY",
            now_unix=now,
        )
        # Same minute (now + 30s is still within the same bucket).
        k2 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="INFY",
            side="BUY",
            now_unix=now + 30.0,
        )
        assert k1 == k2

    def test_different_minute_produces_different_key(self):
        """Same params but a different minute bucket → different key."""
        now = 1_700_000_000.0
        k1 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="INFY",
            side="BUY",
            now_unix=now,
        )
        # +60s crosses into the next minute bucket.
        k2 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="INFY",
            side="BUY",
            now_unix=now + 60.0,
        )
        assert k1 != k2

    def test_key_does_not_vary_with_qty(self):
        """qty is NOT a parameter of build_dedup_key — two
        submissions with different computed qty in the same minute
        resolve to the same key (so the second is deduped).
        """
        now = 1_700_000_000.0
        # build_dedup_key has no qty arg by design; the same call
        # signature for any qty yields the same key in a minute.
        k1 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="TCS",
            side="BUY",
            now_unix=now,
        )
        k2 = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="TCS",
            side="BUY",
            now_unix=now,
        )
        assert k1 == k2

    def test_different_side_produces_different_key(self):
        """BUY vs SELL in the same minute → different keys."""
        now = 1_700_000_000.0
        buy = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="TCS",
            side="BUY",
            now_unix=now,
        )
        sell = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="TCS",
            side="SELL",
            now_unix=now,
        )
        assert buy != sell

    def test_key_prefix_and_minute_bucket(self):
        """Key has the expected prefix and embeds the minute bucket."""
        now = 1_700_000_000.0
        key = build_dedup_key(
            user_id="u1",
            strategy_id="s1",
            symbol="WIPRO",
            side="BUY",
            now_unix=now,
        )
        assert key.startswith("algo:placeorder:dedup:")
        assert key.endswith(str(int(now // 60)))


# -----------------------------------------------------------------
# (b) Redis error + notional >= threshold → BLOCKED (fail-closed)
# -----------------------------------------------------------------


@pytest.mark.algo_dedup_enabled
class TestFailClosedLargeNotional:
    """On Redis error, orders with notional >= threshold are blocked."""

    @pytest.fixture(autouse=True)
    def _enable_gate(self):
        # The dedup gate is enabled only when ALGO_DEDUP_TTL_S > 0;
        # set it explicitly (the marker's fixture is not wired here).
        with patch.dict(os.environ, {"ALGO_DEDUP_TTL_S": "60"}):
            yield

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

    @pytest.fixture(autouse=True)
    def _enable_gate(self):
        with patch.dict(os.environ, {"ALGO_DEDUP_TTL_S": "60"}):
            yield

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
    """Content-addressed cross-call guard via the minute bucket.

    Two place_order calls for the same (user,strategy,symbol,side) in
    the same minute → second blocked, regardless of internal_order_id
    or qty.
    """

    @pytest.fixture(autouse=True)
    def _enable_gate(self):
        with patch.dict(os.environ, {"ALGO_DEDUP_TTL_S": "60"}):
            yield

    def test_same_signal_twice_same_minute_blocked(self):
        """Same signal firing twice in the same minute → second
        blocked even though each call generates a fresh uuid4.
        """
        client = _make_client(FakeRedis())
        # Pin the clock so both calls land in the same minute bucket.
        with patch("time.time", return_value=1_700_000_000.0):
            _call_place(client, [])
            client._kc.place_order.assert_called_once()
            with pytest.raises(DuplicateOrderError):
                _call_place(client, [])
        assert client._kc.place_order.call_count == 1

    def test_qty_recompute_retry_same_minute_blocked(self):
        """A retry with a recomputed qty in the same minute hits the
        same key → blocked (finding #19 goal; qty not in key).
        """
        client = _make_client(FakeRedis())
        with patch("time.time", return_value=1_700_000_000.0):
            _call_place(client, [], quantity=10)
            with pytest.raises(DuplicateOrderError):
                _call_place(client, [], quantity=12)
        assert client._kc.place_order.call_count == 1

    def test_different_minute_both_succeed(self):
        """Same params but separated across a minute boundary →
        both orders go through.
        """
        client = _make_client(FakeRedis())
        with patch("time.time", return_value=1_700_000_000.0):
            _call_place(client, [])
        with patch("time.time", return_value=1_700_000_060.0):
            _call_place(client, [])
        assert client._kc.place_order.call_count == 2

    def test_ttl_zero_disables_dedup(self):
        """ALGO_DEDUP_TTL_S=0 → gate disabled → both pass even in
        the same minute with identical params.
        """
        client = _make_client(FakeRedis())
        with patch.dict(os.environ, {"ALGO_DEDUP_TTL_S": "0"}):
            with patch("time.time", return_value=1_700_000_000.0):
                _call_place(client, [])
                _call_place(client, [])
        assert client._kc.place_order.call_count == 2
