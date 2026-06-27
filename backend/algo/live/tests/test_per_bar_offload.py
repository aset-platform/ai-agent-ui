"""Task 7.1 — verify per-bar Iceberg reads run off the event loop.

Three assertions per the brief:
1. OFFLOAD  — the three ``_ensure_*`` calls (wrapped in
   ``_per_bar_sync_reads``) execute on a WORKER thread, not the
   asyncio event-loop thread.
2. NO DIRECT LOOP CALL — the recorded ident != the loop ident (same
   captured-ident check).
3. CORRECTNESS — each ``_ensure_*`` method is called exactly once for
   the bar; a normal bar still completes without error.

Runtime construction mirrors ``test_stop_loss_live_integration.py``
and ``test_balance_cap.py`` (construct with all I/O deps mocked, seed
``_bars_by_ticker``, drive ``await runtime._on_bar_close(…)``).
"""
from __future__ import annotations

import importlib.util
import sys
import threading
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

_TICKER = "TESTOFFLOAD.NS"
_PRICE = Decimal("500.00")


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "offload test strategy",
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


# ── autouse: open the eval-time gate ─────────────────────────────────────────


@pytest.fixture(autouse=True)
def _force_eval_gate_open(monkeypatch):
    import datetime as _dt

    from backend.algo.live import runtime as _runtime_mod

    monkeypatch.setattr(
        _runtime_mod,
        "_MIN_EVAL_TIME_IST",
        _dt.time(0, 0),
    )


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_per_bar_reads_run_on_worker_thread():
    """OFFLOAD + NO DIRECT LOOP CALL.

    Capture the thread-ident inside each ``_ensure_*`` call; assert
    it differs from the event-loop thread ident — proving the offload
    to ``asyncio.to_thread`` is in effect.
    """
    loop_ident = threading.get_ident()
    recorded_idents: list[int] = []

    runtime = _make_runtime()
    _seed_bars(runtime)

    orig_factor = runtime._ensure_factor_cache
    orig_regime = runtime._ensure_regime_cache
    orig_overlay = runtime._ensure_daily_overlay_cache

    def _spy_factor(ticker, bar_date_obj):
        recorded_idents.append(threading.get_ident())
        orig_factor(ticker, bar_date_obj)

    def _spy_regime(bar_date_obj):
        recorded_idents.append(threading.get_ident())
        orig_regime(bar_date_obj)

    def _spy_overlay(ticker, bar_date_obj):
        recorded_idents.append(threading.get_ident())
        orig_overlay(ticker, bar_date_obj)

    runtime._ensure_factor_cache = _spy_factor
    runtime._ensure_regime_cache = _spy_regime
    runtime._ensure_daily_overlay_cache = _spy_overlay

    await runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    # At least the three _ensure_* calls must have been recorded.
    assert len(recorded_idents) >= 3, (
        f"Expected >=3 recorded thread idents, got {len(recorded_idents)}"
    )
    for ident in recorded_idents:
        assert ident != loop_ident, (
            "Per-bar reads must NOT run on the event-loop thread "
            f"(loop_ident={loop_ident}, recorded={ident})"
        )


@pytest.mark.asyncio
async def test_per_bar_reads_called_once_each():
    """CORRECTNESS — each _ensure_* is invoked exactly once per bar."""
    runtime = _make_runtime()
    _seed_bars(runtime)

    call_counts: dict[str, int] = {
        "factor": 0,
        "regime": 0,
        "overlay": 0,
    }

    orig_factor = runtime._ensure_factor_cache
    orig_regime = runtime._ensure_regime_cache
    orig_overlay = runtime._ensure_daily_overlay_cache

    def _count_factor(ticker, bar_date_obj):
        call_counts["factor"] += 1
        orig_factor(ticker, bar_date_obj)

    def _count_regime(bar_date_obj):
        call_counts["regime"] += 1
        orig_regime(bar_date_obj)

    def _count_overlay(ticker, bar_date_obj):
        call_counts["overlay"] += 1
        orig_overlay(ticker, bar_date_obj)

    runtime._ensure_factor_cache = _count_factor
    runtime._ensure_regime_cache = _count_regime
    runtime._ensure_daily_overlay_cache = _count_overlay

    await runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    assert call_counts["factor"] == 1, (
        f"_ensure_factor_cache expected 1 call, got {call_counts['factor']}"
    )
    assert call_counts["regime"] == 1, (
        f"_ensure_regime_cache expected 1 call, got {call_counts['regime']}"
    )
    assert call_counts["overlay"] == 1, (
        f"_ensure_daily_overlay_cache expected 1 call, "
        f"got {call_counts['overlay']}"
    )


@pytest.mark.asyncio
async def test_bar_completes_without_error():
    """CORRECTNESS — a normal bar through _on_bar_close does not raise."""
    runtime = _make_runtime()
    _seed_bars(runtime)

    # Should return an int (0 or 1), not raise.
    result = await runtime._on_bar_close(
        bar=_make_bar(), last_price=_PRICE
    )
    assert isinstance(result, int), (
        f"Expected int return from _on_bar_close, got {type(result)}"
    )
