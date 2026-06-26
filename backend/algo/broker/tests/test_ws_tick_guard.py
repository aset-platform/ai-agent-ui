"""Tests for Phase 5 WS resiliency tasks.

5.1 — Guard bad/zero-price ticks so one bad packet can't kill the
      on_ticks loop.
5.2 — Gap-fill task tracking: single-flight, cancel on reconnect,
      cancel in close().
5.3 — Batch per-tick LTP writes into one pipeline per on_ticks batch;
      prune backpressure dicts on unsubscribe.
"""
from __future__ import annotations

import asyncio
import sys
import time
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from backend.algo.broker.ws_multiplexer import KiteWsMultiplexer

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_USER = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_SID_1 = uuid4()
_SID_2 = uuid4()


class _FakeTicker:
    """Minimal KiteTicker shim — callbacks wired externally by _wire()."""

    MODE_LTP = "ltp"

    def __init__(self, *_a, **_kw):
        self.subscribed: list[list[int]] = []
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self.on_error = None

    def connect(self, threaded=False):
        pass

    def subscribe(self, tokens):
        self.subscribed.append(list(tokens))

    def set_mode(self, mode, tokens):
        pass

    def unsubscribe(self, tokens):
        pass

    def close(self):
        pass

    def fire_ticks(self, raw_ticks: list[dict]):
        """Simulate Kite calling on_ticks from the WS thread."""
        if self.on_ticks:
            self.on_ticks(self, raw_ticks)


def _make_mux(loop: asyncio.AbstractEventLoop) -> tuple[
    KiteWsMultiplexer, _FakeTicker,
]:
    """Build a multiplexer + fake ticker (no real kiteconnect)."""
    fake_kt = _FakeTicker()
    mux = KiteWsMultiplexer(
        user_id=_USER,
        api_key="test_key",
        access_token="test_token",
    )
    mux._loop = loop
    return mux, fake_kt


def _wire(
    mux: KiteWsMultiplexer,
    fake_kt: _FakeTicker,
    ltp_cache=None,
) -> None:
    """Run _build_ticker with kiteconnect patched to return fake_kt,
    then mark connected so on_ticks is active.

    Pass ``ltp_cache`` to inject a fake cache into the on_ticks
    closure (the real code does ``from backend.cache import get_cache``
    inside _build_ticker — we patch that import site).
    """
    fake_kc = MagicMock()
    fake_kc.KiteTicker.return_value = fake_kt

    # The closure captures _ltp_cache at _build_ticker call time via
    # ``from backend.cache import get_cache; _ltp_cache = get_cache()``.
    # Patch the module attribute so the lazy import returns our fake.
    patch_target = (
        "backend.algo.broker.ws_multiplexer.KiteWsMultiplexer"
        "._build_ticker"
    )
    with patch.dict(sys.modules, {"kiteconnect": fake_kc}):
        if ltp_cache is not None:
            with patch(
                "backend.cache.get_cache",
                return_value=ltp_cache,
            ):
                kt = mux._build_ticker()
        else:
            kt = mux._build_ticker()

    mux._kt = kt
    mux._connected = True


# ---------------------------------------------------------------------------
# 5.1 — Zero-price / malformed tick guard
# ---------------------------------------------------------------------------


class TestTickGuard:
    """A bad packet must not propagate or kill the on_ticks loop."""

    def test_zero_price_tick_dropped_good_tick_processed(
        self, event_loop,
    ):
        """Batch with ltp=0 and a valid packet: valid tick delivered,
        zero-price silently dropped, no exception raised."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)
        _wire(mux, fake_kt)

        q = mux.subscribe(_SID_1, [101], {101: "RELIANCE"})

        raw_ticks = [
            # zero-price — must be dropped before Tick construction.
            {"instrument_token": 101, "last_price": 0.0},
            # valid
            {
                "instrument_token": 101,
                "last_price": 2500.0,
                "last_traded_quantity": 10,
            },
        ]

        fake_kt.fire_ticks(raw_ticks)
        loop.run_until_complete(asyncio.sleep(0))

        assert q.qsize() == 1
        tick = q.get_nowait()
        assert tick.ltp == pytest.approx(2500.0)

    def test_malformed_ltp_does_not_raise(self, event_loop):
        """A tick with a non-numeric ltp string is caught and logged;
        the loop continues to deliver subsequent valid ticks."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)
        _wire(mux, fake_kt)

        q = mux.subscribe(_SID_1, [101], {101: "INFY"})

        raw_ticks = [
            # malformed ltp — float("boom") raises ValueError
            {"instrument_token": 101, "last_price": "boom"},
            # valid follows
            {
                "instrument_token": 101,
                "last_price": 1800.0,
                "last_traded_quantity": 5,
            },
        ]

        # Must not raise.
        fake_kt.fire_ticks(raw_ticks)
        loop.run_until_complete(asyncio.sleep(0))

        assert q.qsize() == 1
        assert q.get_nowait().ltp == pytest.approx(1800.0)

    def test_none_instrument_token_silently_skipped(self, event_loop):
        """Packets missing instrument_token are skipped, queue clean."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)
        _wire(mux, fake_kt)

        q = mux.subscribe(_SID_1, [101], {101: "TCS"})

        raw_ticks = [
            {"last_price": 3000.0},  # no token key
            {"instrument_token": 101, "last_price": 3000.0},  # valid
        ]

        fake_kt.fire_ticks(raw_ticks)
        loop.run_until_complete(asyncio.sleep(0))

        assert q.qsize() == 1


# ---------------------------------------------------------------------------
# 5.2 — Gap-fill task tracking / single-flight
# ---------------------------------------------------------------------------


class TestGapFillTaskTracking:
    """_gap_fill_task is tracked; scheduling twice cancels the prior;
    close() cancels it."""

    def test_second_schedule_cancels_first(self, event_loop):
        """Calling _schedule_gap_fill_sync twice cancels the first
        in-flight task before creating a second one."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)
        _wire(mux, fake_kt)

        gate = asyncio.Event()

        async def _slow_gap_fill():
            await gate.wait()

        async def run():
            with patch.object(
                mux, "_run_gap_fill", side_effect=_slow_gap_fill,
            ):
                mux._schedule_gap_fill_sync()
                await asyncio.sleep(0)
                task_1 = mux._gap_fill_task

                mux._schedule_gap_fill_sync()
                await asyncio.sleep(0)
                task_2 = mux._gap_fill_task

            assert task_1 is not None
            assert task_2 is not None
            assert task_1 is not task_2
            assert task_1.cancelled()

            gate.set()
            if task_2 and not task_2.done():
                task_2.cancel()
                try:
                    await task_2
                except (asyncio.CancelledError, Exception):
                    pass

        loop.run_until_complete(run())

    def test_close_cancels_gap_fill_task(self, event_loop):
        """close() must cancel a running gap-fill task."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)
        _wire(mux, fake_kt)

        gate = asyncio.Event()

        async def _slow_gap_fill():
            await gate.wait()

        async def run():
            with patch.object(
                mux, "_run_gap_fill", side_effect=_slow_gap_fill,
            ):
                mux._schedule_gap_fill_sync()
                await asyncio.sleep(0)
                task = mux._gap_fill_task

            assert task is not None
            assert not task.done()

            with patch.object(mux, "_flush_backpressure_residual"):
                with patch.object(mux, "_disconnect_kt"):
                    await mux.close()

            assert task.cancelled()

        loop.run_until_complete(run())

    def test_gap_fill_replays_use_enqueue_tick(self, event_loop):
        """Gap-fill ticks are routed through _enqueue_tick so that
        backpressure accounting applies identically to live ticks."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)
        _wire(mux, fake_kt)

        from backend.algo.stream.types import Tick

        fake_tick = Tick(
            ticker="RELIANCE",
            ts_ns=1,
            ltp=2500.0,
            volume=10,
        )

        q = mux.subscribe(_SID_1, [101], {101: "RELIANCE"})
        mux._last_tick_ns[101] = (
            int(time.time() * 1e9) - 120_000_000_000
        )

        enqueue_calls: list = []

        original_enqueue = mux._enqueue_tick

        def _capture(q_arg, tick_arg, sid_arg, tok_arg):
            enqueue_calls.append((sid_arg, tok_arg))
            original_enqueue(q_arg, tick_arg, sid_arg, tok_arg)

        async def _fake_gap_fill():
            subs = mux._token_subs.get(101, set())
            for sid in subs:
                qq = mux._queues.get(sid)
                if qq:
                    mux._enqueue_tick(qq, fake_tick, sid, 101)

        async def run():
            with patch.object(
                mux, "_run_gap_fill", side_effect=_fake_gap_fill,
            ):
                with patch.object(
                    mux, "_enqueue_tick", side_effect=_capture,
                ):
                    mux._schedule_gap_fill_sync()
                    await asyncio.sleep(0)
                    task = mux._gap_fill_task
                    if task and not task.done():
                        await asyncio.wait_for(task, timeout=1.0)

            assert len(enqueue_calls) >= 1, (
                "gap-fill did not call _enqueue_tick"
            )

        loop.run_until_complete(run())


# ---------------------------------------------------------------------------
# 5.3 — Batched LTP writes + backpressure dict pruning
# ---------------------------------------------------------------------------


class _FakePipeline:
    """Tracks pipeline.set() calls and execute() calls."""

    def __init__(self):
        self.set_args: list[tuple] = []
        self.execute_count = 0

    def set(self, key, val, ex=None):
        self.set_args.append((key, val, ex))

    def execute(self):
        self.execute_count += 1


class _FakeCache:
    """get_cache()-compatible fake that records pipeline use."""

    def __init__(self):
        self.pipeline_inst = _FakePipeline()
        self.direct_set_count = 0

    def pipeline(self):
        return self.pipeline_inst

    def set(self, key, val, ttl=None):  # old-style per-tick call
        self.direct_set_count += 1


class TestBatchedLtpWrites:
    """on_ticks must issue ONE pipeline.execute() per batch."""

    def test_single_pipeline_per_batch(self, event_loop):
        """3 valid ticks in one on_ticks batch → pipeline.execute()
        called exactly once, not per-tick; legacy cache.set() not
        called at all."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)

        fake_cache = _FakeCache()
        _wire(mux, fake_kt, ltp_cache=fake_cache)

        mux.subscribe(
            _SID_1,
            [101, 102, 103],
            {101: "RELIANCE", 102: "INFY", 103: "TCS"},
        )

        raw_ticks = [
            {"instrument_token": 101, "last_price": 2500.0},
            {"instrument_token": 102, "last_price": 1800.0},
            {"instrument_token": 103, "last_price": 3200.0},
        ]

        fake_kt.fire_ticks(raw_ticks)
        loop.run_until_complete(asyncio.sleep(0))

        pipe = fake_cache.pipeline_inst
        assert pipe.execute_count == 1, (
            f"Expected 1 pipeline.execute(), got {pipe.execute_count}"
        )
        assert len(pipe.set_args) == 3, (
            f"Expected 3 pipeline.set() calls, got {len(pipe.set_args)}"
        )
        assert fake_cache.direct_set_count == 0, (
            "per-tick cache.set() called — batching not implemented"
        )

    def test_ttl_preserved_in_pipeline_sets(self, event_loop):
        """Each pipeline.set() carries ex=60 (60-second TTL)."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)

        fake_cache = _FakeCache()
        _wire(mux, fake_kt, ltp_cache=fake_cache)

        mux.subscribe(_SID_1, [101], {101: "RELIANCE"})

        fake_kt.fire_ticks([
            {"instrument_token": 101, "last_price": 2500.0},
        ])
        loop.run_until_complete(asyncio.sleep(0))

        pipe = fake_cache.pipeline_inst
        assert len(pipe.set_args) == 1
        _key, _val, ttl = pipe.set_args[0]
        assert ttl == 60, f"Expected TTL=60, got {ttl}"


class TestBpDictPruning:
    """unsubscribe() must prune _bp_drops / _bp_last_emit_ns."""

    @pytest.mark.asyncio
    async def test_unsubscribe_prunes_bp_dicts(self, event_loop):
        """After unsubscribe the departed strategy is gone from both
        backpressure tracking dicts."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)
        _wire(mux, fake_kt)

        mux.subscribe(_SID_1, [101], {101: "RELIANCE"})

        # Simulate accumulated backpressure.
        mux._bp_drops[_SID_1] = 5
        mux._bp_last_emit_ns[_SID_1] = int(time.time() * 1e9)

        with patch.object(mux, "_emit_ws_event"):
            await mux.unsubscribe(_SID_1)

        assert _SID_1 not in mux._bp_drops, (
            "_bp_drops not pruned on unsubscribe"
        )
        assert _SID_1 not in mux._bp_last_emit_ns, (
            "_bp_last_emit_ns not pruned on unsubscribe"
        )

    @pytest.mark.asyncio
    async def test_other_strategy_bp_unaffected(self, event_loop):
        """Pruning SID_1 must not remove SID_2's backpressure state."""
        loop = event_loop
        mux, fake_kt = _make_mux(loop)
        _wire(mux, fake_kt)

        mux.subscribe(_SID_1, [101], {101: "RELIANCE"})
        mux.subscribe(_SID_2, [102], {102: "INFY"})

        mux._bp_drops[_SID_1] = 3
        mux._bp_drops[_SID_2] = 7
        mux._bp_last_emit_ns[_SID_2] = int(time.time() * 1e9)

        with patch.object(mux, "_emit_ws_event"):
            await mux.unsubscribe(_SID_1)

        assert _SID_2 in mux._bp_drops
        assert mux._bp_drops[_SID_2] == 7
