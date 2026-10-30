"""Wiring proof — LiveRuntime must populate
``nifty_distance_from_sma200_pct`` in the assembled
``EvalContext.features`` (2026-08-10 regime feature, Task 1).

Per the algo.md wiring rule, a catalog feature never populated by
a runtime always hits ``signal_rejected reason=missing_feature``
silently. We spy on ``assemble_per_bar_features`` (imported into
``backend.algo.live.runtime``'s namespace — patched there per
CLAUDE.md rule 16) to capture BOTH the kwarg passed in and the
resulting dict, for BOTH call sites: ``_on_bar_close`` (primary
per-bar path) and ``_eval_entry_on_closed_bar`` (the daily
OR-trigger "yesterday's close" leg) — mirrors the two
``assemble_per_bar_features`` invocations in ``live/runtime.py``.

Runtime construction mirrors ``test_per_bar_offload.py``.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timezone
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
    reason=(
        "Requires pyarrow + Python ≥3.10 "
        "(run inside Docker backend container)"
    ),
)

_TICKER = "TESTDISTSMA.NS"
_PRICE = Decimal("500.00")
_NEW_KEY = "nifty_distance_from_sma200_pct"


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "dist-sma200 wiring test strategy",
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
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 1}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


def _make_bar():
    from backend.algo.stream.types import Bar

    return Bar(
        ticker=_TICKER,
        interval_sec=86400,
        bar_open_ts_ns=1_000_000_000,
        open=float(_PRICE),
        high=float(_PRICE),
        low=float(_PRICE),
        close=float(_PRICE),
        volume=1000,
        written_at=datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc),
    )


def _make_runtime():
    from unittest.mock import AsyncMock

    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_strategy_payload())

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("10000000"),
        "max_orders_per_day": 100,
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
    kite.place_order = MagicMock(return_value="KITE_ORDER_TEST")

    caps = {"live_orders_enabled": True, "allowed_tickers": [_TICKER]}

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=date(2026, 6, 24),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


def _seed_bars(runtime) -> None:
    from backend.algo.backtest.types import BarData as _BackBar

    runtime._bars_by_ticker[_TICKER] = [
        _BackBar(
            ticker=_TICKER,
            date=date(2026, 6, d),
            open=_PRICE,
            high=_PRICE,
            low=_PRICE,
            close=_PRICE,
            volume=1000,
        )
        for d in range(1, 21)
    ]


def test_market_dist_sma200_cache_populated_at_init():
    """``LiveRuntime.__init__`` computes ``self._market_dist_sma200``
    alongside ``_market_regime`` / ``_market_trend`` — the same
    try/except-guarded regime-cache block. Absent this, the
    per-bar lookup below has nothing to feed the assembler."""
    runtime = _make_runtime()
    assert hasattr(runtime, "_market_dist_sma200")
    assert isinstance(runtime._market_dist_sma200, dict)


@pytest.mark.asyncio
async def test_on_bar_close_assembles_the_new_feature_key():
    """Spy on ``assemble_per_bar_features`` at its LiveRuntime
    call site: the returned ``EvalContext.features`` dict (what
    the AST evaluator actually reads) must contain
    ``nifty_distance_from_sma200_pct`` — proving this runtime path
    never hits ``missing_feature`` for it."""
    from backend.algo.features.per_bar import (
        assemble_per_bar_features as _real_assemble,
    )

    runtime = _make_runtime()
    _seed_bars(runtime)

    captured: dict = {}

    def _spy(**kwargs):
        result = _real_assemble(**kwargs)
        captured["kwargs"] = kwargs
        captured["features"] = result
        return result

    with patch(
        "backend.algo.live.runtime.assemble_per_bar_features",
        side_effect=_spy,
    ):
        await runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    assert captured, "assemble_per_bar_features was never called"
    assert "market_dist_sma200" in captured["kwargs"], (
        "LiveRuntime's assemble_per_bar_features call site did not "
        "pass market_dist_sma200 — the entry-path wiring site is "
        "missing it"
    )
    assert _NEW_KEY in captured["features"], (
        f"{_NEW_KEY} absent from the assembled EvalContext.features "
        f"— a strategy referencing it would hit missing_feature "
        f"forever on the live entry path"
    )


def test_eval_entry_on_closed_bar_assembles_the_new_feature_key():
    """Second wiring site: ``_eval_entry_on_closed_bar`` — the
    daily OR-trigger's "yesterday's close was oversold" leg. Its
    own ``assemble_per_bar_features`` call must also carry
    ``market_dist_sma200`` through to the assembled features."""
    from backend.algo.backtest.types import BarData as _BackBar
    from backend.algo.features.per_bar import (
        assemble_per_bar_features as _real_assemble,
    )
    from backend.algo.stream.types import Bar

    runtime = _make_runtime()

    history = [
        _BackBar(
            ticker=_TICKER,
            date=date(2026, 6, d),
            open=_PRICE,
            high=_PRICE,
            low=_PRICE,
            close=_PRICE,
            volume=1000,
        )
        for d in (23, 24)
    ]
    bar = Bar(
        ticker=_TICKER,
        interval_sec=86400,
        bar_open_ts_ns=1_000_000_000,
        open=float(_PRICE),
        high=float(_PRICE),
        low=float(_PRICE),
        close=float(_PRICE),
        volume=1000,
        written_at=datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc),
    )

    captured: dict = {}

    def _spy(**kwargs):
        result = _real_assemble(**kwargs)
        captured["kwargs"] = kwargs
        captured["features"] = result
        return result

    with patch(
        "backend.algo.live.runtime.assemble_per_bar_features",
        side_effect=_spy,
    ):
        runtime._eval_entry_on_closed_bar(
            history=history, bar=bar, last_price=_PRICE,
        )

    assert captured, "assemble_per_bar_features was never called"
    assert "market_dist_sma200" in captured["kwargs"]
    assert _NEW_KEY in captured["features"], (
        f"{_NEW_KEY} absent from _eval_entry_on_closed_bar's "
        f"assembled EvalContext.features"
    )
