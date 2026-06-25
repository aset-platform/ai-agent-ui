"""Regression test: v5 GTT trailing stop suppresses AST 'exit' signal.

When a TrailingStopManager is active for a ticker, _action_to_signal MUST
return None for 'exit' type actions so the GTT trail handles the close
rather than the strategy's RSI threshold.

Scenario: SHAILY.NS had rsi_2 >= 80, strategy emitted exit, but GTT was
live at phase-15 trailing. The GTT should be the primary exit mechanism.
"""
from __future__ import annotations

import importlib
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch
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

    caps: dict = {"live_orders_enabled": True, "allowed_tickers": ["SHAILY.NS"]}

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


def _open_position(ticker: str, qty: int = 1):
    pos = MagicMock()
    pos.qty = qty
    pos.ticker = ticker
    return pos


class TestTrailingStopSuppressesAstExit:
    def test_exit_suppressed_when_trailing_active(self):
        """_action_to_signal returns None for exit when GTT trailing is live."""
        rt = _make_runtime(trailing_enabled=True)

        ticker = "SHAILY.NS"
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = 324765504

        pos_map = {ticker: _open_position(ticker, qty=1)}
        rt._positions = MagicMock()
        rt._positions.open_positions.return_value = pos_map

        signal = rt._action_to_signal(
            {"type": "exit", "scope": "this_symbol"},
            ticker=ticker,
            bar_date_ns=0,
            last_price=Decimal("2813"),
        )

        assert signal is None, (
            "exit signal must be suppressed when GTT trailing is active"
        )

    def test_exit_fires_when_no_trailing_manager(self):
        """exit signal converts to SELL when trailing is off / no manager."""
        rt = _make_runtime(trailing_enabled=False)

        ticker = "SHAILY.NS"
        pos_map = {ticker: _open_position(ticker, qty=1)}
        rt._positions = MagicMock()
        rt._positions.open_positions.return_value = pos_map

        signal = rt._action_to_signal(
            {"type": "exit", "scope": "this_symbol"},
            ticker=ticker,
            bar_date_ns=0,
            last_price=Decimal("2813"),
        )

        assert signal is not None
        assert signal.side == "SELL"
        assert signal.qty == 1

    def test_exit_fires_when_trailing_enabled_but_no_manager_for_ticker(self):
        """Trailing is on globally, but no manager for THIS ticker → exit fires."""
        rt = _make_runtime(trailing_enabled=True)

        ticker = "SHAILY.NS"
        # Different ticker has a manager; SHAILY does not.
        rt._trailing_managers["OTHER.NS"] = MagicMock()

        pos_map = {ticker: _open_position(ticker, qty=1)}
        rt._positions = MagicMock()
        rt._positions.open_positions.return_value = pos_map

        signal = rt._action_to_signal(
            {"type": "exit", "scope": "this_symbol"},
            ticker=ticker,
            bar_date_ns=0,
            last_price=Decimal("2813"),
        )

        assert signal is not None
        assert signal.side == "SELL"
