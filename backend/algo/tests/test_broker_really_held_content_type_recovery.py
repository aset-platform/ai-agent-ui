"""_broker_really_held() must recover from Kite's Content-Type-
mismatch DataException instead of returning None (UNKNOWN -> the
caller's fail-safe path skips ALL GTT cleanup). Found 2026-07-03:
this bug was firing on every kc.positions()/kc.holdings() call,
meaning this load-bearing safety set was permanently UNKNOWN and
close-time/hydration GTT cleanup was silently never running.
"""
from __future__ import annotations

import importlib
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from kiteconnect.exceptions import DataException

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires the backend Docker stack (pyarrow + py>=3.10)",
)


def _content_type_exception(body: str) -> DataException:
    return DataException(
        f"Unknown Content-Type (text/plain; charset=utf-8) with "
        f"response: (b'{body}')",
    )


def _make_runtime():
    from backend.algo.broker.kite_client import KiteClient
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.risk.per_trade.trailing_trigger_pct = 5.0
    strategy.risk.per_trade.trailing_atr_multiplier = 1.5
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    strategy.schedule = MagicMock()
    strategy.schedule.interval = "1d"
    strategy.product = "CNC"

    caps_repo = MagicMock()
    caps_repo.get = MagicMock(
        return_value={"live_orders_enabled": True},
    )
    kill_switch_repo = MagicMock()
    kill_switch_repo.is_active = MagicMock(return_value=False)

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
        "live_orders_enabled": True, "allowed_tickers": ["MMTC.NS"],
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


class TestBrokerReallyHeldContentTypeRecovery:
    def test_recovers_held_set_despite_content_type_mismatch(self):
        rt = _make_runtime()
        rt._kite._kc.positions.side_effect = _content_type_exception(
            '{"status":"success","data":{"net":[{"tradingsymbol":'
            '"MMTC","quantity":42}]}}',
        )
        rt._kite._kc.holdings.return_value = []

        held = rt._broker_really_held()

        assert held == {"MMTC.NS"}

    def test_still_returns_none_on_genuine_failure(self):
        """Fail-safe contract preserved: a real, non-recoverable
        error must still return None (UNKNOWN), never a
        partially-wrong set."""
        rt = _make_runtime()
        rt._kite._kc.positions.side_effect = RuntimeError("kite down")
        rt._kite._kc.holdings.return_value = []

        held = rt._broker_really_held()

        assert held is None
