"""Tests for LiveRuntime's daily-bar warmup integration
(ASETPLTFRM-383).

Covers:
  1. __init__ preload populates ``_bars_by_ticker`` from
     stocks.ohlcv (via preload_daily_bars) for every ticker in
     caps.allowed_tickers.
  2. _on_bar_close updates today's running bar in place (no
     append, no minute-bar pollution of the daily series).
  3. high/low/close/volume invariants: high broadens up, low
     broadens down, close advances to latest, volume accumulates.
  4. Eval gate: a minute-bar arriving before MIN_EVAL_TIME_IST
     returns 0 (no signal eval) but STILL updates today's bar.
  5. Eval gate: after MIN_EVAL_TIME_IST, eval fires normally
     (verified by reaching the evaluator call site).
  6. Universe drift: ticker not in caps.allowed_tickers lazy-
     preloads on first bar via asyncio.to_thread.
  7. Day rollover: a bar from a NEW date appends a new running
     bar (doesn't mutate yesterday's).
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import date, datetime, time as _time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.broker.kite_client import KiteClient

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires the backend Docker stack (pyarrow + py≥3.10)",
)


# ---------------------------------------------------------------
# Shared fixture: a fully-mocked LiveRuntime with caps.allowed_tickers
# populated, daily-bar preload stubbed to a known 250-bar series.
# ---------------------------------------------------------------


def _series(ticker: str, end: date, n: int):
    """N consecutive ascending bars ending at ``end``."""
    from backend.algo.backtest.types import BarData
    out = []
    for i in range(n):
        d = end - timedelta(days=(n - 1 - i))
        c = Decimal(str(100 + i))
        out.append(BarData(
            ticker=ticker, date=d,
            open=c, high=c + 1, low=c - 1, close=c, volume=1000,
        ))
    return out


def _make_runtime(
    *,
    allowed_tickers: list[str] | None,
    preload_payload: dict | None = None,
) -> "LiveRuntime":
    """Build a LiveRuntime with caps.allowed_tickers set and the
    daily-bar preload monkey-patched to ``preload_payload``."""
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    # ``_on_bar_close`` reads strategy.root.model_dump(by_alias=True)
    # before invoking the evaluator. The spec= guard above blocks
    # attribute access on .root, so wire a plain mock that returns
    # an empty AST node dict (evaluator is patched to "hold" anyway).
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    # ASETPLTFRM-390 — eval-gate carve-out reads strategy.schedule
    # .interval; ASETPLTFRM-389 reads strategy.product. MagicMock
    # (spec=Strategy) doesn't auto-pick-up new optional Pydantic
    # fields, so pin daily / CNC defaults explicitly to mirror every
    # strategy created before this slice.
    strategy.schedule = MagicMock()
    strategy.schedule.interval = "1d"
    strategy.product = "CNC"

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

    # Stub out the daily-bar preload so we don't read real Iceberg.
    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value=(preload_payload or {}),
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


def _minute_bar(
    ticker: str,
    date_obj: date,
    *,
    open_: float = 100.0,
    high: float = 100.0,
    low: float = 100.0,
    close: float = 100.0,
    volume: int = 100,
):
    """Minute-bar stub matching algo.stream.types.Bar's used fields."""
    # bar_open_ts_ns from 09:15 IST = 03:45 UTC of date_obj.
    # Explicit UTC tzinfo so the test isn't sensitive to the
    # container TZ (Asia/Kolkata in dev) — naive datetime would
    # be interpreted as IST and shift the UTC date back by one.
    ts = datetime(
        date_obj.year, date_obj.month, date_obj.day,
        3, 45, tzinfo=timezone.utc,
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


# ---------------------------------------------------------------
# 1. __init__ preload populates _bars_by_ticker for allowed tickers
# ---------------------------------------------------------------


def test_init_preloads_bars_for_allowed_tickers() -> None:
    today = date.today()
    payload = {
        "ITC.NS": _series("ITC.NS", today - timedelta(days=1), 250),
    }
    runtime = _make_runtime(
        allowed_tickers=["ITC.NS"],
        preload_payload=payload,
    )
    assert "ITC.NS" in runtime._bars_by_ticker
    assert len(runtime._bars_by_ticker["ITC.NS"]) == 250


def test_init_skips_preload_when_no_allowed_tickers() -> None:
    runtime = _make_runtime(allowed_tickers=None, preload_payload={})
    assert runtime._bars_by_ticker == {}


# ---------------------------------------------------------------
# 2 + 3. _on_bar_close updates today's running bar in place
# ---------------------------------------------------------------


async def _drive_bar(runtime, bar) -> int:
    """Run _on_bar_close with a permissive eval-time gate (so the
    eval path is reached regardless of wall-clock). Returns the
    int the method returned (0 = gate hit OR no fill, ≥1 = fill)."""
    with patch(
        "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
        _time(0, 0),  # always past — eval gate open
    ):
        # Force evaluator to return HOLD so we don't trigger an
        # actual order submission path (orthogonal to this test).
        with patch.object(
            runtime._evaluator, "eval_node",
            return_value={"type": "hold"},
        ):
            return await runtime._on_bar_close(
                bar=bar,
                last_price=Decimal(str(bar.close)),
            )


@pytest.mark.asyncio
async def test_running_bar_updated_in_place_not_appended() -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    payload = {
        "ITC.NS": _series("ITC.NS", yesterday, 250),
    }
    runtime = _make_runtime(
        allowed_tickers=["ITC.NS"],
        preload_payload=payload,
    )
    initial_len = len(runtime._bars_by_ticker["ITC.NS"])

    bar = _minute_bar(
        "ITC.NS", today,
        open_=300, high=302, low=299, close=301, volume=500,
    )
    await _drive_bar(runtime, bar)

    # First bar of today APPENDS a new running bar → length+1.
    assert len(runtime._bars_by_ticker["ITC.NS"]) == initial_len + 1
    running = runtime._bars_by_ticker["ITC.NS"][-1]
    assert running.date == today
    assert running.close == Decimal("301")
    assert running.high == Decimal("302")
    assert running.low == Decimal("299")
    assert running.volume == 500

    # A second minute bar UPDATES the existing running bar — no
    # new entry. high broadens up, low broadens down, close
    # tracks latest, volume accumulates.
    bar2 = _minute_bar(
        "ITC.NS", today,
        open_=301, high=305, low=296, close=304, volume=200,
    )
    await _drive_bar(runtime, bar2)

    assert len(runtime._bars_by_ticker["ITC.NS"]) == initial_len + 1
    updated = runtime._bars_by_ticker["ITC.NS"][-1]
    assert updated.high == Decimal("305")     # broadened up
    assert updated.low == Decimal("296")      # broadened down
    assert updated.close == Decimal("304")
    assert updated.volume == 700              # 500 + 200


# ---------------------------------------------------------------
# 4. Pre-gate: eval skipped, bar still updated.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_gate_skips_eval_but_updates_bar() -> None:
    today = date.today()
    payload = {
        "ITC.NS": _series(
            "ITC.NS", today - timedelta(days=1), 250,
        ),
    }
    runtime = _make_runtime(
        allowed_tickers=["ITC.NS"], preload_payload=payload,
    )

    bar = _minute_bar(
        "ITC.NS", today,
        open_=300, high=302, low=299, close=301, volume=500,
    )
    # Gate set to 23:59 → effectively closed for the duration.
    with patch(
        "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
        _time(23, 59),
    ):
        eval_spy = MagicMock()
        with patch.object(runtime._evaluator, "eval_node", eval_spy):
            result = await runtime._on_bar_close(
                bar=bar, last_price=Decimal("301"),
            )

    assert result == 0
    eval_spy.assert_not_called()
    # Bar STILL updated despite gate (visible on Live panel).
    running = runtime._bars_by_ticker["ITC.NS"][-1]
    assert running.date == today
    assert running.close == Decimal("301")


# ---------------------------------------------------------------
# 5. Post-gate: evaluator is invoked.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_gate_invokes_evaluator() -> None:
    today = date.today()
    payload = {
        "ITC.NS": _series(
            "ITC.NS", today - timedelta(days=1), 250,
        ),
    }
    runtime = _make_runtime(
        allowed_tickers=["ITC.NS"], preload_payload=payload,
    )
    bar = _minute_bar(
        "ITC.NS", today, close=301, volume=10,
    )

    with patch(
        "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
        _time(0, 0),
    ):
        eval_spy = MagicMock(return_value={"type": "hold"})
        with patch.object(runtime._evaluator, "eval_node", eval_spy):
            await runtime._on_bar_close(
                bar=bar, last_price=Decimal("301"),
            )

    assert eval_spy.call_count == 1


# ---------------------------------------------------------------
# 5b. ASETPLTFRM-390 — eval-gate carve-out for intraday cadence.
# Daily strategies keep the 14:30 IST gate so today's running daily
# candle stabilises before they act. Intraday strategies (5m / 1m)
# need to fire from market open at 09:15 IST; gating them on the
# same daily cutoff would silence the entire morning session.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_intraday_strategy_bypasses_pre_gate_skip() -> None:
    """A 5-min strategy must NOT be silenced by the 14:30 IST gate.

    Pins the carve-out: with the gate fully closed (23:59), the
    daily-cadence path returns 0 (covered by
    ``test_pre_gate_skips_eval_but_updates_bar``); the intraday-
    cadence path must still invoke the evaluator.
    """
    today = date.today()
    payload = {
        "ITC.NS": _series(
            "ITC.NS", today - timedelta(days=1), 250,
        ),
    }
    runtime = _make_runtime(
        allowed_tickers=["ITC.NS"], preload_payload=payload,
    )
    # Mark this runtime's strategy as intraday — the carve-out reads
    # strategy.schedule.interval to decide whether to honour the
    # daily gate.
    runtime._strategy.schedule.interval = "5m"

    bar = _minute_bar(
        "ITC.NS", today, close=301, volume=10,
    )
    # Gate fully closed; the daily path would return 0 here.
    with patch(
        "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
        _time(23, 59),
    ):
        eval_spy = MagicMock(return_value={"type": "hold"})
        with patch.object(runtime._evaluator, "eval_node", eval_spy):
            await runtime._on_bar_close(
                bar=bar, last_price=Decimal("301"),
            )

    # Intraday path bypasses the gate → evaluator fires.
    assert eval_spy.call_count == 1, (
        "Intraday strategy must evaluate even when the 14:30 IST "
        "gate is closed — the gate is daily-cadence specific."
    )


@pytest.mark.asyncio
async def test_daily_strategy_still_gated_after_carve_out() -> None:
    """Backwards-compat invariant: existing daily strategies keep
    being gated by the pre-14:30 cutoff. Pins the unchanged path.
    """
    today = date.today()
    payload = {
        "ITC.NS": _series(
            "ITC.NS", today - timedelta(days=1), 250,
        ),
    }
    runtime = _make_runtime(
        allowed_tickers=["ITC.NS"], preload_payload=payload,
    )
    # Strategy defaults to interval="1d" via _make_runtime fixture.
    assert runtime._strategy.schedule.interval == "1d"

    bar = _minute_bar(
        "ITC.NS", today, close=301, volume=10,
    )
    with patch(
        "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
        _time(23, 59),
    ):
        eval_spy = MagicMock()
        with patch.object(runtime._evaluator, "eval_node", eval_spy):
            result = await runtime._on_bar_close(
                bar=bar, last_price=Decimal("301"),
            )

    assert result == 0
    eval_spy.assert_not_called()


# ---------------------------------------------------------------
# 6. Universe drift: lazy preload for unknown ticker.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_universe_drift_triggers_lazy_preload() -> None:
    today = date.today()
    # Empty preload at __init__ (no allowed_tickers).
    runtime = _make_runtime(
        allowed_tickers=None, preload_payload={},
    )
    assert "TCS.NS" not in runtime._bars_by_ticker

    bar = _minute_bar("TCS.NS", today, close=4000, volume=100)
    lazy_payload = {
        "TCS.NS": _series(
            "TCS.NS", today - timedelta(days=1), 250,
        ),
    }
    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value=lazy_payload,
    ):
        with patch(
            "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
            _time(0, 0),
        ):
            with patch.object(
                runtime._evaluator, "eval_node",
            return_value={"type": "hold"},
            ):
                await runtime._on_bar_close(
                    bar=bar, last_price=Decimal("4000"),
                )

    assert "TCS.NS" in runtime._bars_by_ticker
    # 250 preloaded + 1 running today bar.
    assert len(runtime._bars_by_ticker["TCS.NS"]) == 251
    assert runtime._bars_by_ticker["TCS.NS"][-1].date == today


# ---------------------------------------------------------------
# 7. Day rollover appends a new running bar (rare; runtime
#    usually restarts daily).
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_day_rollover_appends_new_running_bar() -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    payload = {"ITC.NS": _series("ITC.NS", yesterday, 250)}
    runtime = _make_runtime(
        allowed_tickers=["ITC.NS"], preload_payload=payload,
    )
    # Drive a bar for *yesterday* first (simulating a stale clock /
    # replay scenario). It should update the existing yesterday bar
    # in place because yesterday IS the last preloaded bar.
    bar_y = _minute_bar(
        "ITC.NS", yesterday, close=199, volume=10,
    )
    await _drive_bar(runtime, bar_y)
    assert len(runtime._bars_by_ticker["ITC.NS"]) == 250
    assert runtime._bars_by_ticker["ITC.NS"][-1].date == yesterday
    assert runtime._bars_by_ticker["ITC.NS"][-1].close == (
        Decimal("199")
    )

    # Now drive a bar dated TODAY — should APPEND a new running bar
    # without overwriting yesterday's.
    bar_t = _minute_bar(
        "ITC.NS", today, close=201, volume=10,
    )
    await _drive_bar(runtime, bar_t)

    assert len(runtime._bars_by_ticker["ITC.NS"]) == 251
    assert runtime._bars_by_ticker["ITC.NS"][-1].date == today
    assert runtime._bars_by_ticker["ITC.NS"][-2].date == yesterday
    assert runtime._bars_by_ticker["ITC.NS"][-2].close == (
        Decimal("199")
    )  # yesterday untouched
