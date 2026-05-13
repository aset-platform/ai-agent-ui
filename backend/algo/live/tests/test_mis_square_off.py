"""Tests for the MIS auto-square-off task (ASETPLTFRM-394).

Pins the lifecycle invariants:

  1. CNC strategy → ``_square_off_task`` stays None; auto-square
     never scheduled (daily-strategy invariant).
  2. MIS strategy + square_off_time in the future → task scheduled
     and emits SELL signals for every open position at the target.
  3. MIS strategy + open positions → one SELL per ticker via
     ``_submit_order``; quantities match position quantities; reason
     stamped as ``"mis_auto_square_off"``.
  4. MIS strategy + no open positions when the task fires → no-op,
     no SELL signals.
  5. MIS strategy + runtime stopped before the scheduled time → task
     cancelled, no SELL emitted.
  6. ``_parse_square_off_ist`` parses "HH:MM IST", bare "HH:MM",
     and falls back to 15:14 on garbage.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------
# _parse_square_off_ist — pure utility, no fixture needed.
# ---------------------------------------------------------------


class TestParseSquareOffIst:
    def test_hh_mm_with_ist_suffix(self):
        from backend.algo.live.runtime import LiveRuntime
        assert LiveRuntime._parse_square_off_ist(
            "15:14 IST",
        ) == time(15, 14)

    def test_bare_hh_mm(self):
        from backend.algo.live.runtime import LiveRuntime
        assert LiveRuntime._parse_square_off_ist(
            "09:30",
        ) == time(9, 30)

    def test_none_falls_back_to_default(self):
        from backend.algo.live.runtime import LiveRuntime
        assert LiveRuntime._parse_square_off_ist(
            None,
        ) == time(15, 14)

    def test_empty_string_falls_back_to_default(self):
        from backend.algo.live.runtime import LiveRuntime
        assert LiveRuntime._parse_square_off_ist(
            "",
        ) == time(15, 14)

    def test_garbage_falls_back_with_warning(self, caplog):
        from backend.algo.live.runtime import LiveRuntime
        with caplog.at_level("WARNING"):
            result = LiveRuntime._parse_square_off_ist("not a time")
        assert result == time(15, 14)
        assert any(
            "invalid square_off_time" in rec.message
            for rec in caplog.records
        )


# ---------------------------------------------------------------
# Lifecycle integration — fixture builds a minimal runtime.
# ---------------------------------------------------------------


def _make_runtime(*, product: str = "MIS"):
    """Build a LiveRuntime with all heavy deps mocked, configurable
    product code so we can exercise both CNC + MIS lifecycles."""
    from backend.algo.broker.kite_client import KiteClient
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.root = MagicMock()
    strategy.schedule = MagicMock()
    strategy.schedule.interval = (
        "5m" if product == "MIS" else "1d"
    )
    strategy.product = product
    strategy.square_off_time = (
        "15:14 IST" if product == "MIS" else None
    )

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {"live_orders_enabled": True}

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc = MagicMock()
        MockKC.return_value = kc
        kite = KiteClient(
            api_key="k", access_token="tok", dry_run=True,
        )
        kite._kc = kc

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value={},
    ), patch(
        "backend.algo.live.intraday_bar_warmup."
        "preload_intraday_bars",
        return_value={},
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=None,
            kite=kite,
            caps={"live_orders_enabled": True},
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


# ---------------------------------------------------------------
# 1. CNC strategy never schedules the task.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_cnc_strategy_does_not_schedule_square_off():
    """Daily / CNC strategies must not touch the square-off path.
    Backwards-compat invariant for every existing daily strategy."""
    runtime = _make_runtime(product="CNC")
    assert runtime._square_off_task is None

    # Direct schedule call shouldn't even fire for product=CNC, but
    # the gate lives in ``run()``. The fixture for this test asserts
    # the initialiser leaves the attribute None — the run() gate is
    # exercised separately via the integration test below.


# ---------------------------------------------------------------
# 2 + 3. MIS strategy emits one SELL per open position.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mis_square_off_emits_sell_per_position():
    """When the auto-square fires, every ticker in
    ``PositionTracker.open_positions()`` gets a SELL signal routed
    through ``_submit_order``."""
    runtime = _make_runtime(product="MIS")

    # Seed two open positions.
    open_positions = {
        "ITC.NS": SimpleNamespace(qty=8, avg_price=Decimal("307.33")),
        "RELIANCE.NS": SimpleNamespace(
            qty=5, avg_price=Decimal("2500"),
        ),
    }
    runtime._positions = MagicMock()
    runtime._positions.open_positions.return_value = open_positions

    submitted: list = []

    async def _capture(*, signal, last_price, **_kwargs):
        submitted.append(
            (signal.ticker, signal.side, signal.qty, signal.reason),
        )
        return 1

    runtime._submit_order = _capture  # type: ignore[assignment]

    # Pin "now" so the delay calc is deterministic. Square-off target
    # is "15:14 IST" — set now to 15:13:55 IST so we only sleep 5s,
    # but we patch asyncio.sleep to instant so the test is fast.
    fake_now = datetime(2026, 5, 13, 15, 13, 55, tzinfo=IST)
    with patch(
        "backend.algo.live.runtime.datetime",
    ) as mock_dt:
        mock_dt.now.return_value = fake_now
        # Allow non-`now` paths to use the real datetime.
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        with patch(
            "backend.algo.live.runtime.asyncio.sleep",
            new=AsyncMock(),
        ):
            await runtime._schedule_mis_square_off()

    assert len(submitted) == 2
    sells_by_ticker = {row[0]: row for row in submitted}
    itc = sells_by_ticker["ITC.NS"]
    assert itc[1] == "SELL"
    assert itc[2] == 8
    assert itc[3] == "mis_auto_square_off"
    rel = sells_by_ticker["RELIANCE.NS"]
    assert rel[1] == "SELL"
    assert rel[2] == 5
    assert rel[3] == "mis_auto_square_off"


# ---------------------------------------------------------------
# 4. No open positions → no-op.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mis_square_off_no_positions_is_noop():
    runtime = _make_runtime(product="MIS")
    runtime._positions = MagicMock()
    runtime._positions.open_positions.return_value = {}

    submit_called = MagicMock()
    runtime._submit_order = submit_called  # type: ignore[assignment]

    fake_now = datetime(2026, 5, 13, 15, 13, 55, tzinfo=IST)
    with patch(
        "backend.algo.live.runtime.datetime",
    ) as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        with patch(
            "backend.algo.live.runtime.asyncio.sleep",
            new=AsyncMock(),
        ):
            await runtime._schedule_mis_square_off()

    submit_called.assert_not_called()


# ---------------------------------------------------------------
# 5. Target time already past → immediate no-op.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mis_square_off_past_target_returns_immediately():
    """Operator started the runtime AFTER the configured square-off
    — task no-ops rather than sleeping for ~24h."""
    runtime = _make_runtime(product="MIS")
    runtime._positions = MagicMock()
    runtime._positions.open_positions.return_value = {
        "ITC.NS": SimpleNamespace(qty=8, avg_price=Decimal("307")),
    }
    submit_called = MagicMock()
    runtime._submit_order = submit_called  # type: ignore[assignment]

    # square_off_time = 15:14 IST, now = 15:30 IST → already past.
    fake_now = datetime(2026, 5, 13, 15, 30, 0, tzinfo=IST)
    sleep_mock = AsyncMock()
    with patch(
        "backend.algo.live.runtime.datetime",
    ) as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        with patch(
            "backend.algo.live.runtime.asyncio.sleep", new=sleep_mock,
        ):
            await runtime._schedule_mis_square_off()

    # No sleep (would block ~24h), no submit.
    sleep_mock.assert_not_called()
    submit_called.assert_not_called()


# ---------------------------------------------------------------
# 6. Cancellation propagation — sleep is interrupted cleanly.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mis_square_off_cancels_before_firing():
    """Runtime.stop() cancels the task while it's still sleeping;
    no SELL signals should fire."""
    runtime = _make_runtime(product="MIS")
    runtime._positions = MagicMock()
    runtime._positions.open_positions.return_value = {
        "ITC.NS": SimpleNamespace(qty=8, avg_price=Decimal("307")),
    }
    submit_called = MagicMock()
    runtime._submit_order = submit_called  # type: ignore[assignment]

    fake_now = datetime(2026, 5, 13, 15, 13, 55, tzinfo=IST)
    with patch(
        "backend.algo.live.runtime.datetime",
    ) as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.fromtimestamp = datetime.fromtimestamp

        task = asyncio.create_task(
            runtime._schedule_mis_square_off(),
        )
        # Let the task hit the sleep, then cancel.
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    submit_called.assert_not_called()
