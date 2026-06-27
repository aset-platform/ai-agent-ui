"""Regression tests for two bugs introduced by set_target_weight trim SELLs.

Bug 1 — position_hydration.py:
  When an overnight holding is partially sold intraday (e.g. held 16,
  sold 2 → net qty=-2 in positions()['net']), the hydration code was
  checking `qty == 0` to skip; qty=-2 passed that check and was added
  to `already_loaded`, which then blocked the holdings()  entry (qty=14)
  from being loaded. Result: position tracker shows 0 shares instead of
  14 after a restart.

Bug 2 — _on_sell_fill_trailing:
  A `set_target_weight` SELL is a rebalancing trim, not a position close.
  The method was unconditionally cancelling the GTT and clearing all
  trailing state, leaving the remaining shares unprotected.
"""
from __future__ import annotations

import importlib
import sys
from decimal import Decimal
from unittest.mock import MagicMock, call, patch
from uuid import uuid4

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires the backend Docker stack (pyarrow + py>=3.10)",
)


# ------------------------------------------------------------------ #
# Bug 1: hydration skips net-sell overnight positions                 #
# ------------------------------------------------------------------ #

class TestHydrationNetSellPosition:
    """positions()['net'] qty<0 must NOT block the holdings entry."""

    def test_negative_net_position_does_not_block_holding(self):
        from backend.algo.live.position_hydration import hydrate
        from backend.algo.backtest.positions import PositionTracker
        from backend.algo.live.position_hydration import apply_hydrated_positions

        kite = MagicMock()
        # Kite positions()['net']: sold 2 today from overnight holding
        kite._kc.positions.return_value = {
            "net": [
                {
                    "tradingsymbol": "KTKBANK",
                    "quantity": -2,        # net SELL today
                    "product": "CNC",
                    "average_price": 267.0,
                },
            ]
        }
        # Kite holdings(): 14 shares remaining after 2 sold today
        kite._kc.holdings.return_value = [
            {
                "tradingsymbol": "KTKBANK",
                "quantity": 14,
                "t1_quantity": 0,
                "product": "CNC",
                "average_price": 260.0,
            },
        ]

        strategy = MagicMock()
        strategy.id = uuid4()

        hydrated = hydrate(
            kite,
            strategy,
            user_id=uuid4(),
            allowed_tickers=["KTKBANK.NS"],
            events_reader=lambda uid, sym: None,
        )

        # Should have exactly 1 position: the 14-share holdings entry.
        # The -2 net-sell must be skipped without blocking the holding.
        assert len(hydrated) == 1, (
            f"Expected 1 hydrated position (the 14-share holding), "
            f"got {len(hydrated)}: {hydrated}"
        )
        assert hydrated[0].symbol == "KTKBANK.NS"
        assert hydrated[0].qty == 14
        assert hydrated[0].source == "holdings"

    def test_zero_qty_net_position_still_skipped(self):
        """Original behaviour: qty==0 in positions()['net'] is skipped."""
        from backend.algo.live.position_hydration import hydrate

        kite = MagicMock()
        kite._kc.positions.return_value = {
            "net": [
                {
                    "tradingsymbol": "KTKBANK",
                    "quantity": 0,
                    "product": "CNC",
                    "average_price": 267.0,
                },
            ]
        }
        kite._kc.holdings.return_value = [
            {
                "tradingsymbol": "KTKBANK",
                "quantity": 16,
                "t1_quantity": 0,
                "product": "CNC",
                "average_price": 260.0,
            },
        ]

        strategy = MagicMock()
        strategy.id = uuid4()

        hydrated = hydrate(
            kite,
            strategy,
            user_id=uuid4(),
            allowed_tickers=["KTKBANK.NS"],
            events_reader=lambda uid, sym: None,
        )
        # qty=0 in positions → skipped; holding loaded
        assert len(hydrated) == 1
        assert hydrated[0].qty == 16

    def test_positive_net_position_blocks_holding(self):
        """Today's CNC BUY in positions()['net'] must block the holdings entry."""
        from backend.algo.live.position_hydration import hydrate

        kite = MagicMock()
        kite._kc.positions.return_value = {
            "net": [
                {
                    "tradingsymbol": "HSCL",
                    "quantity": 20,        # bought 20 today
                    "product": "CNC",
                    "average_price": 630.0,
                },
            ]
        }
        kite._kc.holdings.return_value = [
            {
                "tradingsymbol": "HSCL",
                "quantity": 20,            # same — T+1 not yet settled
                "t1_quantity": 0,
                "product": "CNC",
                "average_price": 630.0,
            },
        ]

        strategy = MagicMock()
        strategy.id = uuid4()

        hydrated = hydrate(
            kite,
            strategy,
            user_id=uuid4(),
            allowed_tickers=["HSCL.NS"],
            events_reader=lambda uid, sym: None,
        )
        # The positions() entry loads qty=20; holdings() must be skipped
        # (already_loaded guard). No double-count.
        assert len(hydrated) == 1
        assert hydrated[0].qty == 20
        assert hydrated[0].source == "positions"


# ------------------------------------------------------------------ #
# Bug 2: _on_sell_fill_trailing treats trim SELLs as full closes      #
# ------------------------------------------------------------------ #

def _make_runtime(trailing_enabled: bool):
    from backend.algo.broker.kite_client import KiteClient
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.risk.per_trade.trailing_trigger_pct = 5.0 if trailing_enabled else None
    strategy.risk.per_trade.trailing_atr_multiplier = 1.5 if trailing_enabled else None
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    strategy.schedule = MagicMock()
    strategy.schedule.interval = "1d"
    strategy.product = "CNC"

    caps_repo = MagicMock()
    caps_repo.get = MagicMock(return_value={"live_orders_enabled": True})
    kill_switch_repo = MagicMock()
    kill_switch_repo.is_active = MagicMock(return_value=False)

    with patch("backend.algo.broker.kite_client.KiteConnect") as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        kite = KiteClient(api_key="k", access_token="tok", dry_run=True)
        kite._kc = kc_instance

    caps: dict = {"live_orders_enabled": True, "allowed_tickers": ["KTKBANK.NS"]}
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


class TestTrimSellTrailing:
    def test_set_target_weight_trim_preserves_trailing_state(self):
        """set_target_weight SELL must NOT clear GTT or trailing manager."""
        rt = _make_runtime(trailing_enabled=True)

        ticker = "KTKBANK.NS"
        gtt_id = 324861555
        mgr = MagicMock()
        rt._trailing_managers[ticker] = mgr
        rt._gtt_ids[ticker] = gtt_id
        rt._ws_hwm[ticker] = Decimal("270")
        rt._ticker_locked.add(ticker)

        rt._on_sell_fill_trailing(ticker, reason="set_target_weight")

        # Trailing state must be preserved for remaining shares
        assert ticker in rt._trailing_managers
        assert rt._gtt_ids.get(ticker) == gtt_id
        assert ticker in rt._ticker_locked

    def test_exit_reason_clears_trailing_state(self):
        """SELL with reason='exit' is a full close → clear GTT + trailing."""
        rt = _make_runtime(trailing_enabled=True)

        ticker = "KTKBANK.NS"
        gtt_id = 324861555
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = gtt_id
        rt._ws_hwm[ticker] = Decimal("270")
        rt._ticker_locked.add(ticker)

        rt._kite.delete_gtt = MagicMock()
        rt._on_sell_fill_trailing(ticker, reason="exit")

        assert ticker not in rt._trailing_managers
        assert ticker not in rt._gtt_ids
        assert ticker not in rt._ticker_locked
        rt._kite.delete_gtt.assert_called_once_with(gtt_id)

    def test_no_reason_clears_trailing_state(self):
        """SELL from GTT fire has no matched in-flight entry → reason=None → full clear."""
        rt = _make_runtime(trailing_enabled=True)

        ticker = "KTKBANK.NS"
        gtt_id = 324861555
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = gtt_id
        rt._ticker_locked.add(ticker)

        rt._kite.delete_gtt = MagicMock()
        rt._on_sell_fill_trailing(ticker, reason=None)

        assert ticker not in rt._trailing_managers
        assert ticker not in rt._ticker_locked
        rt._kite.delete_gtt.assert_called_once_with(gtt_id)

    def test_trailing_disabled_is_noop(self):
        """When trailing is disabled, _on_sell_fill_trailing is a no-op."""
        rt = _make_runtime(trailing_enabled=False)
        ticker = "KTKBANK.NS"
        rt._ticker_locked.add(ticker)

        # Should not raise or modify state in unexpected ways
        rt._on_sell_fill_trailing(ticker, reason="set_target_weight")
        rt._on_sell_fill_trailing(ticker, reason="exit")
        # trailing disabled → lock NOT released (no-op)
        assert ticker in rt._ticker_locked
