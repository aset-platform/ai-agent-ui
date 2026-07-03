"""Regression test — the allow-list gate in
``LiveRuntime._on_bar_close`` must pick up mid-run edits to
``allowed_tickers``, not just what was loaded at startup.

Found 2026-07-03: user added KIRLOSENG.NS to allowed_tickers via
PUT /algo/live/caps/{strategy_id} while a LiveRuntime was already
running. The gate kept rejecting it with signal_rejected
reason=ticker_not_allowed. Root cause: the gate read
``self._caps["allowed_tickers"]`` -- a snapshot frozen at
``LiveRuntime.__init__`` and never refreshed -- instead of
``current_caps``, a fresh per-signal PG read that the SAME function
already performs (and already correctly used) for max_inr /
max_orders_per_day, just later in the function, after the allow-list
gate had already returned.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import date, time as _time, timedelta, timezone, datetime
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
    reason="Requires the backend Docker stack (pyarrow + py>=3.10)",
)


def _series(ticker: str, end: date, n: int):
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


def _rejections(runtime, reason: str) -> list[dict]:
    """``algo.events`` rows store the payload as a JSON string under
    ``payload_json``, not a nested ``payload`` dict."""
    out = []
    for e in runtime._events:
        if e.get("type") != "signal_rejected":
            continue
        payload = json.loads(e.get("payload_json") or "{}")
        if payload.get("reason") == reason:
            out.append(payload)
    return out


def _minute_bar(ticker: str, date_obj: date, *, close: float, volume: int):
    ts = datetime(
        date_obj.year, date_obj.month, date_obj.day,
        3, 45, tzinfo=timezone.utc,
    )
    ts_ns = int(ts.timestamp() * 1_000_000_000)
    return SimpleNamespace(
        ticker=ticker, bar_open_ts_ns=ts_ns,
        open=close, high=close, low=close, close=close, volume=volume,
    )


def _make_runtime(*, startup_allowed_tickers, caps_repo_get_return):
    """Build a LiveRuntime whose startup caps.allowed_tickers is
    ``startup_allowed_tickers``, and whose caps_repo.get() (the
    per-signal fresh read) returns ``caps_repo_get_return`` --
    simulating a caps row that was edited AFTER runtime startup."""
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.risk.per_trade.cooldown_after_failed_exit_days = None
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    strategy.schedule = MagicMock()
    strategy.schedule.interval = "1d"
    strategy.product = "CNC"
    strategy.entry_cutoff_time = None

    caps_repo = AsyncMock()
    caps_repo.get.return_value = caps_repo_get_return

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        kite = KiteClient(api_key="k", access_token="tok", dry_run=True)
        kite._kc = kc_instance

    caps: dict = {
        "live_orders_enabled": True,
        "allowed_tickers": startup_allowed_tickers,
    }
    today = date.today()
    preload_payload = {
        t: _series(t, today - timedelta(days=1), 250)
        for t in startup_allowed_tickers
    }

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value=preload_payload,
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


class TestAllowListPicksUpMidRunEdits:
    @pytest.mark.asyncio
    async def test_ticker_added_mid_run_is_no_longer_rejected(self):
        """KIRLOSENG.NS was NOT in the startup allow-list (runtime
        started before the user added it), but the caps row now
        (per the fresh PG read) includes it -- the gate must allow
        it through, not reject with ticker_not_allowed."""
        runtime = _make_runtime(
            startup_allowed_tickers=["ITC.NS"],
            caps_repo_get_return={
                "live_orders_enabled": True,
                "allowed_tickers": ["ITC.NS", "KIRLOSENG.NS"],
            },
        )
        today = date.today()
        bar = _minute_bar(
            "KIRLOSENG.NS", today, close=550, volume=100,
        )
        lazy_payload = {
            "KIRLOSENG.NS": _series(
                "KIRLOSENG.NS", today - timedelta(days=1), 250,
            ),
        }
        submit_spy = AsyncMock(return_value=1)

        with patch(
            "backend.algo.live.daily_bar_warmup.preload_daily_bars",
            return_value=lazy_payload,
        ), patch(
            "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
            _time(0, 0),
        ), patch.object(
            runtime._evaluator, "eval_node",
            return_value={"type": "buy", "qty": {"shares": 1}},
        ), patch.object(runtime, "_submit_order", submit_spy):
            await runtime._on_bar_close(
                bar=bar, last_price=Decimal("550"),
            )

        rejections = _rejections(runtime, "ticker_not_allowed")
        assert rejections == [], (
            f"KIRLOSENG.NS was in the fresh (mid-run-edited) "
            f"allow-list but was still rejected: {rejections}"
        )

    @pytest.mark.asyncio
    async def test_ticker_removed_mid_run_is_now_rejected(self):
        """Inverse case: a ticker WAS in the startup allow-list but
        has since been removed (fresh caps read no longer has it) --
        the gate must now reject it, proving the check really uses
        the fresh read and not a permissive union of both."""
        runtime = _make_runtime(
            startup_allowed_tickers=["ITC.NS", "KIRLOSENG.NS"],
            caps_repo_get_return={
                "live_orders_enabled": True,
                "allowed_tickers": ["ITC.NS"],
            },
        )
        today = date.today()
        bar = _minute_bar(
            "KIRLOSENG.NS", today, close=550, volume=100,
        )
        submit_spy = AsyncMock(return_value=1)

        with patch(
            "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
            _time(0, 0),
        ), patch.object(
            runtime._evaluator, "eval_node",
            return_value={"type": "buy", "qty": {"shares": 1}},
        ), patch.object(runtime, "_submit_order", submit_spy):
            await runtime._on_bar_close(
                bar=bar, last_price=Decimal("550"),
            )

        rejections = _rejections(runtime, "ticker_not_allowed")
        assert len(rejections) == 1
        submit_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_caps_repo_failure_falls_back_to_startup_caps(self):
        """If the fresh PG read fails/returns nothing, the gate must
        fail safe by falling back to the startup snapshot -- never
        crash, never silently allow everything."""
        runtime = _make_runtime(
            startup_allowed_tickers=["ITC.NS"],
            caps_repo_get_return=None,
        )
        today = date.today()
        bar = _minute_bar(
            "KIRLOSENG.NS", today, close=550, volume=100,
        )
        lazy_payload = {
            "KIRLOSENG.NS": _series(
                "KIRLOSENG.NS", today - timedelta(days=1), 250,
            ),
        }
        submit_spy = AsyncMock(return_value=1)

        with patch(
            "backend.algo.live.daily_bar_warmup.preload_daily_bars",
            return_value=lazy_payload,
        ), patch(
            "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
            _time(0, 0),
        ), patch.object(
            runtime._evaluator, "eval_node",
            return_value={"type": "buy", "qty": {"shares": 1}},
        ), patch.object(runtime, "_submit_order", submit_spy):
            await runtime._on_bar_close(
                bar=bar, last_price=Decimal("550"),
            )

        rejections = _rejections(runtime, "ticker_not_allowed")
        assert len(rejections) == 1
        submit_spy.assert_not_called()
