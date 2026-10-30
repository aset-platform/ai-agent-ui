"""Regression test — GTT limit-price headroom (gtt_limit_headroom_pct)
must pick up mid-run caps edits, mirroring the allowed_tickers fix in
test_live_runtime_allow_list_staleness.py.

Found 2026-07-03 alongside the allow-list bug: self._gtt_limit_headroom_pct
was cached once at LiveRuntime.__init__ and read directly by
on_buy_fill_trailing / _ratchet_all_gtts (both sync, thread-safe-by-
design, no await) -- a mid-run edit via PUT /algo/live/caps/{id} was
invisible until restart, same class of bug as allowed_tickers.

Fix: both call sites now read self._caps.get("gtt_limit_headroom_pct")
directly instead of a frozen scalar, and self._caps itself gets
refreshed at two natural points (_on_bar_close's existing fresh-caps
read, and immediately before each 15-min ratchet tick) so the sync
call sites see an up-to-date value without needing their own I/O.
"""
from __future__ import annotations

import importlib
import sys
from decimal import Decimal
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


def _make_runtime(*, gtt_limit_headroom_pct: float):
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.risk.per_trade.trailing_trigger_pct = 5.0
    strategy.risk.per_trade.trailing_atr_multiplier = 1.5
    strategy.risk.per_trade.stop_loss_pct = 5.0
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
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
            api_key="k", access_token="tok", dry_run=False,
        )
        kite._kc = kc_instance

    caps: dict = {
        "live_orders_enabled": True,
        "allowed_tickers": ["ITC.NS"],
        "gtt_limit_headroom_pct": gtt_limit_headroom_pct,
    }

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value={},
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


class TestGttHeadroomPicksUpMidRunEdits:
    def test_on_buy_fill_trailing_uses_current_self_caps_value(self):
        """self._caps is read directly (not a frozen __init__-time
        scalar) -- mutating it (as _on_bar_close's fresh-caps-read
        now does) must change the very next GTT's limit price."""
        runtime = _make_runtime(gtt_limit_headroom_pct=0.01)

        with patch(
            "backend.algo.live.runtime.get_tick_size",
            return_value=Decimal("0.05"),
        ):
            runtime._kite.place_gtt = MagicMock(return_value=1)
            runtime.on_buy_fill_trailing(
                ticker="ITC.NS", fill_price=300.0, qty=10,
            )
            limit_at_1pct = runtime._kite.place_gtt.call_args.kwargs[
                "limit_price"
            ]

            # Simulate a mid-run caps edit (what the fresh-caps read
            # in _on_bar_close / the ratchet loop does).
            runtime._caps = {
                **runtime._caps,
                "gtt_limit_headroom_pct": 0.05,
            }
            runtime._trailing_managers.pop("ITC.NS", None)
            runtime._kite.place_gtt = MagicMock(return_value=2)
            runtime.on_buy_fill_trailing(
                ticker="ITC.NS", fill_price=300.0, qty=10,
            )
            limit_at_5pct = runtime._kite.place_gtt.call_args.kwargs[
                "limit_price"
            ]

        assert limit_at_1pct != limit_at_5pct, (
            "GTT limit price did not change after a mid-run "
            "gtt_limit_headroom_pct edit -- still reading a frozen "
            "startup value."
        )
        # Bigger headroom -> limit set further below the stop.
        assert limit_at_5pct < limit_at_1pct

    @pytest.mark.asyncio
    async def test_on_bar_close_refreshes_self_caps(self):
        """The existing fresh-caps read in _on_bar_close must also
        update self._caps itself, not just the local current_caps
        used for the allow-list gate -- this is what feeds the sync
        GTT call sites above."""
        from datetime import timezone, datetime as _dt
        from types import SimpleNamespace

        runtime = _make_runtime(gtt_limit_headroom_pct=0.01)
        runtime._caps_repo.get = AsyncMock(return_value={
            "live_orders_enabled": True,
            "allowed_tickers": ["ITC.NS"],
            "gtt_limit_headroom_pct": 0.07,
        })
        runtime._strategy.entry_cutoff_time = None
        runtime._strategy.risk.per_trade.cooldown_after_failed_exit_days = (
            None
        )

        ts = _dt(2026, 7, 3, 3, 45, tzinfo=timezone.utc)
        bar = SimpleNamespace(
            ticker="ITC.NS",
            bar_open_ts_ns=int(ts.timestamp() * 1_000_000_000),
            open=300.0, high=300.0, low=300.0, close=300.0,
            volume=100,
        )

        # The caps-refresh code sits on the signal path (past the
        # entry-cutoff/per-ticker-cap gates), so eval must return a
        # real BUY, not hold, to reach it.
        with patch.object(
            runtime._evaluator, "eval_node",
            return_value={"type": "buy", "qty": {"shares": 1}},
        ), patch.object(
            runtime, "_submit_order", AsyncMock(return_value=0),
        ):
            await runtime._on_bar_close(
                bar=bar, last_price=Decimal("300"),
            )

        assert runtime._caps.get("gtt_limit_headroom_pct") == 0.07
