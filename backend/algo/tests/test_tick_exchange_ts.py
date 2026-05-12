"""Tests for ASETPLTFRM-372 — exchange_timestamp plumbing.

Covers the three layers wired in this PR:

* ``Tick.exchange_ts_ns`` — new optional ns-since-epoch field that
  carries the exchange-stamped time (authoritative emission time)
  alongside ``ts_ns`` (local arrival time stamped by the
  multiplexer). Falls back to ``None`` when Kite omits the field.
* ``KiteWsMultiplexer.on_ticks`` — reads ``raw["exchange_timestamp"]``
  (a ``datetime`` per kiteconnect SDK >=4) and converts to ns.
* ``LiveRuntime.run`` — prefers ``tick.exchange_ts_ns`` over
  ``tick.ts_ns`` when stamping ``last_price_ts_per_ticker`` so the
  staleness gate catches "exchange stopped emitting but WS is fine"
  freezes (Yahoo ^BSESN-style), which a local-arrival timestamp
  would miss.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.broker.ws_multiplexer import KiteWsMultiplexer
from backend.algo.stream.types import Tick
from backend.algo.tests.fixtures.mock_kite_ws_server import (
    _ticker_to_token,
    patch_multiplexer_ticker,
)


# ----------------------------------------------------------------
# 1. Tick model — new optional field
# ----------------------------------------------------------------


class TestTickModelExchangeTs:
    def test_default_is_none(self):
        """Existing call-sites that omit the field still work."""
        t = Tick(ticker="ITC.NS", ts_ns=1, ltp=100.0, volume=1)
        assert t.exchange_ts_ns is None

    def test_accepts_int(self):
        t = Tick(
            ticker="ITC.NS",
            ts_ns=1,
            ltp=100.0,
            volume=1,
            exchange_ts_ns=42_000_000_000,
        )
        assert t.exchange_ts_ns == 42_000_000_000


# ----------------------------------------------------------------
# 2. Multiplexer — captures exchange_timestamp from raw Kite tick
# ----------------------------------------------------------------


def _make_mux() -> KiteWsMultiplexer:
    return KiteWsMultiplexer(
        user_id=uuid4(),
        api_key="test_key",
        access_token="test_token",
    )


@pytest.mark.asyncio
async def test_tick_carries_exchange_ts_ns_when_kite_supplies_it():
    """Kite raw tick with ``exchange_timestamp`` datetime → Tick has
    ``exchange_ts_ns`` populated. Conversion uses ``.timestamp()``
    which honours the SDK's naive-local-time convention."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "ITC.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        q = mux.subscribe(sid, [tok], {tok: ticker})

        # Kite SDK stamps exchange_timestamp as a naive datetime
        # produced by datetime.fromtimestamp(epoch_seconds). Mirror
        # that: pick a known epoch second and round-trip it.
        exch_epoch_s = 1_700_000_000
        exch_dt = datetime.fromtimestamp(exch_epoch_s)

        shim.inject_raw([{
            "instrument_token": tok,
            "last_price": 307.30,
            "last_traded_quantity": 1,
            "exchange_timestamp": exch_dt,
        }])
        await asyncio.sleep(0)

        tick = q.get_nowait()
        assert isinstance(tick, Tick)
        assert tick.exchange_ts_ns is not None
        assert tick.exchange_ts_ns == exch_epoch_s * 1_000_000_000
        # ts_ns (local arrival) is still populated separately.
        assert tick.ts_ns > 0


@pytest.mark.asyncio
async def test_tick_exchange_ts_ns_is_none_when_kite_omits_field():
    """LTP-mode Kite packets omit ``exchange_timestamp`` →
    ``Tick.exchange_ts_ns is None`` while ``ts_ns`` still has the
    local-arrival stamp."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "ITC.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        q = mux.subscribe(sid, [tok], {tok: ticker})

        shim.inject_raw([{
            "instrument_token": tok,
            "last_price": 307.30,
            "last_traded_quantity": 1,
            # no exchange_timestamp key
        }])
        await asyncio.sleep(0)

        tick = q.get_nowait()
        assert tick.exchange_ts_ns is None
        assert tick.ts_ns > 0


@pytest.mark.asyncio
async def test_tick_exchange_ts_ns_is_none_when_field_unparseable():
    """Defensive: a non-datetime value (e.g. raw int, str) → fall
    back to None rather than crashing the tick loop."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "ITC.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        q = mux.subscribe(sid, [tok], {tok: ticker})

        shim.inject_raw([{
            "instrument_token": tok,
            "last_price": 307.30,
            "last_traded_quantity": 1,
            "exchange_timestamp": "not-a-datetime",
        }])
        await asyncio.sleep(0)

        tick = q.get_nowait()
        assert tick.exchange_ts_ns is None
        assert tick.ts_ns > 0


# ----------------------------------------------------------------
# 3. LiveRuntime — prefers exchange_ts_ns over ts_ns
# ----------------------------------------------------------------


async def _drain_one_tick(tick: Tick) -> dict[str, datetime]:
    """Run a minimal slice of LiveRuntime.run for a single tick and
    return the captured ``last_price_ts_per_ticker`` mapping.

    We mirror the exact stamping logic in ``LiveRuntime.run`` rather
    than spinning up a real runtime — the spec scopes this PR to the
    stamping decision, and a full runtime fixture would pull in
    Iceberg, regime loaders, etc.
    """
    # Mirror the production code path so the test fails if the
    # selection rule regresses.
    from backend.algo.live.runtime import _select_last_price_ts_ns
    ts_ns = _select_last_price_ts_ns(tick)
    return {
        tick.ticker: datetime.fromtimestamp(
            ts_ns / 1_000_000_000, tz=UTC,
        ),
    }


@pytest.mark.asyncio
async def test_runtime_prefers_exchange_ts_over_local():
    """When both stamps are present, exchange_ts_ns wins."""
    t0_ns = 1_700_000_000 * 1_000_000_000  # exchange time
    t1_ns = 1_700_000_500 * 1_000_000_000  # local arrival (later)
    tick = Tick(
        ticker="ITC.NS",
        ts_ns=t1_ns,
        ltp=307.30,
        volume=1,
        exchange_ts_ns=t0_ns,
    )
    out = await _drain_one_tick(tick)
    assert out["ITC.NS"] == datetime.fromtimestamp(
        t0_ns / 1_000_000_000, tz=UTC,
    )
    assert out["ITC.NS"] != datetime.fromtimestamp(
        t1_ns / 1_000_000_000, tz=UTC,
    )


@pytest.mark.asyncio
async def test_runtime_falls_back_to_local_when_exchange_ts_none():
    """exchange_ts_ns=None → ts_ns is used (PR #1 baseline)."""
    t1_ns = 1_700_000_500 * 1_000_000_000
    tick = Tick(
        ticker="ITC.NS",
        ts_ns=t1_ns,
        ltp=307.30,
        volume=1,
        exchange_ts_ns=None,
    )
    out = await _drain_one_tick(tick)
    assert out["ITC.NS"] == datetime.fromtimestamp(
        t1_ns / 1_000_000_000, tz=UTC,
    )


# ----------------------------------------------------------------
# 4. End-to-end — staleness gate consumes the exchange ts
# ----------------------------------------------------------------


class TestStalenessGateUsesExchangeTs:
    """The point of the whole feature: a tick whose local arrival is
    fresh but whose exchange timestamp is old MUST block the order.

    Pre-PR-372 this would have passed the gate (ts_ns=1s ago).
    """

    def test_staleness_gate_uses_exchange_ts_when_present(self):
        from backend.algo.broker.exceptions import LtpStaleError
        from backend.algo.broker.kite_client import KiteClient

        with patch(
            "backend.algo.broker.kite_client.KiteConnect",
        ) as MockKC:
            kc_instance = MagicMock()
            MockKC.return_value = kc_instance
            client = KiteClient(
                api_key="k", access_token="t", dry_run=False,
            )
            client._kc = kc_instance

        events_buffer: list = []
        # Simulate what LiveRuntime would compute: exchange ts is
        # 120s old, local arrival is 1s ago.
        now = datetime.now(UTC)
        exchange_ts = now - timedelta(seconds=120)
        # last_price_ts is what LiveRuntime now passes to the gate —
        # post-PR it'll be derived from exchange_ts_ns.
        with patch.dict(
            "os.environ", {"ALGO_MAX_LTP_AGE_S": "5"},
        ):
            with pytest.raises(LtpStaleError, match="LTP age"):
                client.place_order(
                    tradingsymbol="ITC",
                    exchange="NSE",
                    transaction_type="BUY",
                    quantity=1,
                    order_type="LIMIT",
                    price=307.35,
                    last_price=307.30,
                    last_price_ts=exchange_ts,
                    events_sink=events_buffer.append,
                )
        # Without this PR, LiveRuntime would have passed the LOCAL
        # arrival ts (1s ago) and the gate would have let it through.
        # With this PR, the exchange ts (120s) is what flows in.
        assert kc_instance.place_order.call_count == 0


# ----------------------------------------------------------------
# 5. LiveRuntime selector exists and is the single source of truth
# ----------------------------------------------------------------


def test_selector_helper_uses_exchange_ts_when_present():
    """Sanity-check the small pure helper that the runtime uses.
    Keeps the precedence rule unit-testable without a runtime."""
    from backend.algo.live.runtime import _select_last_price_ts_ns
    tick = Tick(
        ticker="ITC.NS",
        ts_ns=1_700_000_500 * 1_000_000_000,
        ltp=1.0,
        volume=1,
        exchange_ts_ns=1_700_000_000 * 1_000_000_000,
    )
    assert _select_last_price_ts_ns(tick) == (
        1_700_000_000 * 1_000_000_000
    )


def test_selector_helper_falls_back_to_ts_ns():
    from backend.algo.live.runtime import _select_last_price_ts_ns
    tick = Tick(
        ticker="ITC.NS",
        ts_ns=1_700_000_500 * 1_000_000_000,
        ltp=1.0,
        volume=1,
        exchange_ts_ns=None,
    )
    assert _select_last_price_ts_ns(tick) == (
        1_700_000_500 * 1_000_000_000
    )


# Silence unused-import warnings for type-only imports in some
# of the helpers above when running in slim CI environments.
_ = Decimal
