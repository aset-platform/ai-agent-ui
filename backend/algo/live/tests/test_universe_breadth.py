"""Task 4 — per-ticker RSI2 cache + universe-oversold breadth helper.

Feeds Task 5's shadow snapshot: ``self._last_rsi2`` (dict[str, float],
``.NS``-keyed, refreshed each ``_on_bar_close`` eval) and
``_universe_oversold_breadth()`` (pure count over that dict). This
test only exercises the pure counting method, so it builds a minimal
``LiveRuntime`` instance (mirroring
``test_entry_window_or_trigger.py``'s ``_make_runtime()``) and sets
``_last_rsi2`` directly rather than driving a real bar-close eval.
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason=(
        "Requires pyarrow + Python >=3.10 "
        "(run inside Docker backend container)"
    ),
)

_TICKER = "BREADTH.NS"


def _strategy_payload() -> dict:
    """Minimal valid strategy — never evaluated in this test."""
    return {
        "id": str(uuid4()),
        "name": "universe breadth test strategy",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close",
            "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 5},
        "root": {
            "type": "if",
            "cond": {
                "type": "compare",
                "op": "<=",
                "left": {"feature": "rsi_2"},
                "right": {"literal": 5},
            },
            "then": {"type": "set_target_weight", "weight": 0.2},
            "else": {"type": "hold"},
        },
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 1000},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


@pytest.fixture
def runtime():
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy
    from datetime import date

    strategy = parse_strategy(_strategy_payload())

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("0"),
        "max_orders_per_day": 0,
        "allowed_tickers": [_TICKER],
        "cumulative_inr_today": Decimal("0"),
        "orders_count_today": 0,
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    kite = MagicMock()
    kite.dry_run = False
    kite._get_redis.return_value = MagicMock()

    caps = {"live_orders_enabled": True, "allowed_tickers": [_TICKER]}

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ):
        rt = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=date(2026, 8, 4),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return rt


def test_last_rsi2_initialized_empty(runtime):
    assert runtime._last_rsi2 == {}


def test_breadth_counts_oversold(runtime):
    runtime._last_rsi2 = {
        "A.NS": 3.0, "B.NS": 4.9, "C.NS": 55.0, "D.NS": 5.0,
    }
    assert runtime._universe_oversold_breadth() == (3, 4)  # A,B,D


def test_breadth_empty_cache(runtime):
    assert runtime._universe_oversold_breadth() == (0, 0)


def test_breadth_custom_threshold(runtime):
    runtime._last_rsi2 = {"A.NS": 10.0, "B.NS": 20.0, "C.NS": 30.0}
    assert runtime._universe_oversold_breadth(threshold=15.0) == (1, 3)
