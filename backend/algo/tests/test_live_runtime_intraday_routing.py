"""Integration tests for the intraday bar-routing path in
``LiveRuntime._on_bar_close`` (ASETPLTFRM-393).

Pins the bucketing invariants for 5-min and 1-min strategies:

  1. ``__init__`` calls the intraday warmup, not the daily one.
  2. Consecutive 1-min ticks within the same 5-min window UPDATE
     the running bar in place — no new bar appended.
  3. The first 1-min tick that crosses a 5-min boundary APPENDS a
     fresh bar; the previous bar is preserved as a closed bar.
  4. 1-min cadence appends a new bar every tick.
  5. ``bar_open_ts_ns`` on each bar matches the bucket-start
     boundary (bar_open // interval_ns × interval_ns).
  6. Daily strategies are byte-for-byte unaffected — covered by
     the existing ``test_live_runtime_daily_bar_warmup.py``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.broker.kite_client import KiteClient


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------


def _make_intraday_runtime(
    *,
    interval: str,
    allowed_tickers: list[str] | None = None,
    preload_payload: dict | None = None,
) -> Any:
    """Build a LiveRuntime with the intraday warmup stubbed.

    Mirrors the daily-warmup test fixture shape but patches
    ``preload_intraday_bars`` and pins ``strategy.schedule.interval``
    to the requested intraday cadence (``5m`` / ``1m`` / ``15m``).
    """
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    strategy.schedule = MagicMock()
    strategy.schedule.interval = interval
    strategy.product = "CNC"  # cadence and product are orthogonal

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {"live_orders_enabled": True}

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        kite = KiteClient(
            api_key="k", access_token="tok", dry_run=True,
        )
        kite._kc = kc_instance

    caps: dict = {"live_orders_enabled": True}
    if allowed_tickers is not None:
        caps["allowed_tickers"] = allowed_tickers

    # Stub out BOTH warmups so the daily path can never be reached
    # accidentally and the intraday path returns our payload.
    with (
        patch(
            "backend.algo.live.intraday_bar_warmup."
            "preload_intraday_bars",
            return_value=(preload_payload or {}),
        ),
        patch(
            "backend.algo.live.daily_bar_warmup.preload_daily_bars",
            side_effect=AssertionError(
                "Intraday runtime should not call daily warmup",
            ),
        ),
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=None,
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


def _minute_bar_at(
    ticker: str,
    hour_ist: int,
    minute_ist: int,
    *,
    open_: float = 100.0,
    high: float = 100.0,
    low: float = 100.0,
    close: float = 100.0,
    volume: int = 100,
):
    """Minute-bar stub anchored at a specific IST wall-clock.

    The runtime reads ``bar_open_ts_ns`` to compute the bucket key,
    so the test must produce realistic nanosecond timestamps. We
    pin a fixed UTC date (2026-05-13) so tests don't drift with
    wall-clock.
    """
    # 09:15 IST = 03:45 UTC on the test date.
    ist_offset_min = 5 * 60 + 30
    utc_min_from_midnight = (
        hour_ist * 60 + minute_ist - ist_offset_min
    )
    base_utc = datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc)
    ts = base_utc.replace(
        hour=utc_min_from_midnight // 60,
        minute=utc_min_from_midnight % 60,
    )
    ts_ns = int(ts.timestamp() * 1_000_000_000)
    return SimpleNamespace(
        ticker=ticker,
        bar_open_ts_ns=ts_ns,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


async def _drive(runtime, bar) -> int:
    """Drive a minute bar through ``_on_bar_close`` with the eval
    gate held open and the evaluator forced to HOLD (we're
    exercising bucketing, not signal flow)."""
    from datetime import time
    with patch(
        "backend.algo.live.runtime._MIN_EVAL_TIME_IST", time(0, 0),
    ):
        with patch.object(
            runtime._evaluator, "eval_node",
            return_value={"type": "hold"},
        ):
            return await runtime._on_bar_close(
                bar=bar, last_price=Decimal(str(bar.close)),
            )


# ---------------------------------------------------------------
# 1. Intraday __init__ calls intraday warmup, not daily.
# ---------------------------------------------------------------


async def test_intraday_init_calls_intraday_warmup() -> None:
    """Daily warmup is the patched AssertionError-raiser inside the
    fixture; if the runtime routed there we'd never construct."""
    runtime = _make_intraday_runtime(
        interval="5m",
        allowed_tickers=["ITC.NS"],
        preload_payload={"ITC.NS": []},
    )
    # Construction succeeded → daily path was never reached.
    assert runtime._strategy.schedule.interval == "5m"
    assert "ITC.NS" in runtime._bars_by_ticker


# ---------------------------------------------------------------
# 2. Bucketing: 1-min ticks in same 5-min window UPDATE, don't append.
# ---------------------------------------------------------------


async def test_5m_ticks_in_same_window_update_in_place() -> None:
    """09:15, 09:16, 09:17, 09:18, 09:19 all sit in the same 5-min
    window [09:15, 09:20). The runtime must keep a SINGLE bar in
    history for that window and update it 5 times."""
    runtime = _make_intraday_runtime(
        interval="5m",
        allowed_tickers=["ITC.NS"],
        preload_payload={"ITC.NS": []},
    )
    initial_len = len(runtime._bars_by_ticker["ITC.NS"])

    for i, minute in enumerate([15, 16, 17, 18, 19]):
        bar = _minute_bar_at(
            "ITC.NS", 9, minute,
            open_=100 + i, high=101 + i, low=99 + i,
            close=100 + i, volume=50,
        )
        await _drive(runtime, bar)

    # Only ONE bar added; later minutes updated it in place.
    series = runtime._bars_by_ticker["ITC.NS"]
    assert len(series) == initial_len + 1
    bar = series[-1]
    # Volume accumulated across all 5 minutes.
    assert bar.volume == 50 * 5
    # Bucket key = 09:15 IST → 03:45 UTC of test date.
    expected_open_utc = datetime(
        2026, 5, 13, 3, 45, tzinfo=timezone.utc,
    )
    assert bar.bar_open_ts_ns == int(
        expected_open_utc.timestamp() * 1_000_000_000,
    )


# ---------------------------------------------------------------
# 3. Crossing a 5-min boundary APPENDS a new bar.
# ---------------------------------------------------------------


async def test_5m_boundary_crossing_appends_new_bar() -> None:
    """09:19 closes the [09:15, 09:20) bucket; 09:20 opens the
    next one. The runtime must append a fresh BarData and leave
    the previous bar closed at its final close price."""
    runtime = _make_intraday_runtime(
        interval="5m",
        allowed_tickers=["ITC.NS"],
        preload_payload={"ITC.NS": []},
    )
    initial_len = len(runtime._bars_by_ticker["ITC.NS"])

    # Fill the first bucket with 09:15 only.
    await _drive(runtime, _minute_bar_at(
        "ITC.NS", 9, 15, close=100, volume=100,
    ))
    # Cross into the second bucket.
    await _drive(runtime, _minute_bar_at(
        "ITC.NS", 9, 20, close=102, volume=200,
    ))

    series = runtime._bars_by_ticker["ITC.NS"]
    # Two buckets — initial + 2 new.
    assert len(series) == initial_len + 2

    first_bucket_open_utc = datetime(
        2026, 5, 13, 3, 45, tzinfo=timezone.utc,
    )
    second_bucket_open_utc = datetime(
        2026, 5, 13, 3, 50, tzinfo=timezone.utc,
    )
    assert series[-2].bar_open_ts_ns == int(
        first_bucket_open_utc.timestamp() * 1_000_000_000,
    )
    assert series[-1].bar_open_ts_ns == int(
        second_bucket_open_utc.timestamp() * 1_000_000_000,
    )
    # First bucket frozen at its 09:15 close; second carries 09:20.
    assert series[-2].close == Decimal("100")
    assert series[-1].close == Decimal("102")


# ---------------------------------------------------------------
# 4. 1-min cadence: every minute is a new bar.
# ---------------------------------------------------------------


async def test_1m_cadence_appends_on_every_minute() -> None:
    runtime = _make_intraday_runtime(
        interval="1m",
        allowed_tickers=["ITC.NS"],
        preload_payload={"ITC.NS": []},
    )
    initial_len = len(runtime._bars_by_ticker["ITC.NS"])

    for minute in [15, 16, 17, 18]:
        await _drive(runtime, _minute_bar_at(
            "ITC.NS", 9, minute, close=100 + minute,
        ))

    series = runtime._bars_by_ticker["ITC.NS"]
    assert len(series) == initial_len + 4
    # Each bar has its own bucket key spaced by 60s.
    diffs = [
        series[i + 1].bar_open_ts_ns - series[i].bar_open_ts_ns
        for i in range(len(series) - 1)
    ]
    assert all(d == 60 * 1_000_000_000 for d in diffs[-3:])


# ---------------------------------------------------------------
# 5. 15-min cadence buckets to :00 / :15 / :30 / :45.
# ---------------------------------------------------------------


async def test_15m_cadence_buckets_to_quarter_hour() -> None:
    """09:15, 09:20, 09:25 all hit the same [09:15, 09:30) bucket
    for a 15-min strategy. 09:30 opens the next bucket."""
    runtime = _make_intraday_runtime(
        interval="15m",
        allowed_tickers=["ITC.NS"],
        preload_payload={"ITC.NS": []},
    )
    initial_len = len(runtime._bars_by_ticker["ITC.NS"])

    for minute in [15, 20, 25]:
        await _drive(runtime, _minute_bar_at(
            "ITC.NS", 9, minute, close=100 + minute, volume=10,
        ))
    # Crosses into [09:30, 09:45)
    await _drive(runtime, _minute_bar_at(
        "ITC.NS", 9, 30, close=130, volume=5,
    ))

    series = runtime._bars_by_ticker["ITC.NS"]
    # First three ticks coalesce into one bucket; fourth opens
    # the next → +2 bars total.
    assert len(series) == initial_len + 2
    # Volume sum for the first bucket = 10 + 10 + 10.
    assert series[-2].volume == 30
    # Second bucket carries only the single 09:30 tick.
    assert series[-1].volume == 5
