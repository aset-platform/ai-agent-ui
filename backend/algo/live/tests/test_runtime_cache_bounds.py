"""Tests for per-ticker bar-history cap and closed-entry-cache eviction.

High #15 — two unbounded in-memory caches in LiveRuntime leak memory
across a long live session:
  1. _bars_by_ticker[ticker] grows by one entry per new bucket, never trimmed.
  2. _closed_entry_cache gains one (ticker, date) key per day, never evicted.

These tests verify the bounding logic added in runtime.py:
  - _MAX_BAR_HISTORY cap (in-place slice-delete on new-bucket append only).
  - _evict_stale_closed_entry_cache helper removes old keys.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
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

_TICKER = "TESTCAP.NS"
_PRICE = Decimal("500.00")


# ── Harness helpers ────────────────────────────────────────────────────────────


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "cache-bounds test strategy",
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


def _make_runtime():
    """Construct a LiveRuntime with all I/O deps mocked (no real Kite/PG)."""
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
        "gtt_limit_headroom_pct": Decimal("0.005"),
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    kite = MagicMock()
    kite.dry_run = False
    kite.place_order = MagicMock(return_value="KITE_ORDER_CAP")

    caps = {"live_orders_enabled": True, "allowed_tickers": [_TICKER]}

    with patch(
        "backend.algo.live.position_hydration.hydrate", return_value=[]
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=date(2026, 1, 1),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


def _make_bar(*, ticker: str = _TICKER, ts_ns: int, close: float = 500.0):
    """Return a daily Bar whose bucket is identified by ts_ns."""
    from backend.algo.stream.types import Bar

    return Bar(
        ticker=ticker,
        interval_sec=86400,
        bar_open_ts_ns=ts_ns,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=10_000,
        written_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
    )


def _ts_for_date(d: date) -> int:
    """Return UTC-midnight nanosecond timestamp for ``d``."""
    dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _backbar(ticker: str, d: date):
    """Return a BarData (BackBar) for the given date."""
    from backend.algo.backtest.types import BarData as _BackBar

    p = Decimal("500.00")
    return _BackBar(
        ticker=ticker,
        date=d,
        open=p,
        high=p,
        low=p,
        close=p,
        volume=10_000,
    )


def _bar_close_patches(runtime):
    """Stub all heavy I/O inside _on_bar_close so tests focus on
    cache-bounding logic only. Returns a list of context managers."""
    from backend.algo.live.budget_types import UserBudget

    async def _load_user(uid):
        return UserBudget(
            user_id=uid,
            allocated_inr=Decimal("100000000"),
        )

    async def _active(_uid, _sid):
        return Decimal("0")

    return [
        patch(
            "backend.algo.live.runtime.budget_reserve_if_headroom",
            new=AsyncMock(return_value=uuid4()),
        ),
        patch(
            "backend.algo.live.runtime.budget_load_user",
            new=_load_user,
        ),
        patch(
            "backend.algo.live.runtime.budget_active_for_strategy",
            new=_active,
        ),
        patch(
            "backend.algo.live.runtime.budget_reserve",
            new=AsyncMock(return_value=uuid4()),
        ),
        patch(
            "backend.algo.live.runtime.budget_transition",
            new=AsyncMock(),
        ),
        # No-op the Iceberg / feature reads so asyncio.to_thread returns fast.
        patch.object(runtime, "_per_bar_sync_reads", return_value=None),
    ]


# ── Test 1: Bar-history cap ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bar_history_capped_at_max(monkeypatch):
    """Pre-seed cap-1 bars, drive cap+3 new-bucket bars, assert cap holds.

    After each new-bucket append the list must not exceed cap, and the
    retained bars must be the most-recent ones. List identity must be
    preserved (same object as _bars_by_ticker[ticker]).
    """
    import backend.algo.live.runtime as _rt_mod

    cap = 5
    monkeypatch.setattr(_rt_mod, "_MAX_BAR_HISTORY", cap)
    # No eval-time gate to hold open anymore (Task 1 / ASETPLTFRM-383
    # OR-trigger redesign removed it) -- irrelevant here regardless,
    # since the new-bucket append + trim this test asserts on always
    # runs BEFORE the entry-timing gate block in _on_bar_close.

    runtime = _make_runtime()

    # Pre-seed cap-1 historical closed bars.
    base = date(2026, 1, 1)
    seed_dates = [base + timedelta(days=i) for i in range(cap - 1)]
    runtime._bars_by_ticker[_TICKER] = [
        _backbar(_TICKER, d) for d in seed_dates
    ]
    history_ref = runtime._bars_by_ticker[_TICKER]

    patches = _bar_close_patches(runtime)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        for i in range(cap + 3):
            bar_date = seed_dates[-1] + timedelta(days=i + 1)
            bar = _make_bar(ticker=_TICKER, ts_ns=_ts_for_date(bar_date))
            await runtime._on_bar_close(bar=bar, last_price=_PRICE)

    history = runtime._bars_by_ticker[_TICKER]

    assert len(history) == cap, (
        f"Expected cap={cap} bars; got {len(history)}"
    )
    expected_last = seed_dates[-1] + timedelta(days=cap + 3)
    assert history[-1].date == expected_last, (
        f"Last bar date should be {expected_last}; got {history[-1].date}"
    )
    # In-place trim must keep same list object bound to _bars_by_ticker.
    assert history is history_ref, (
        "_bars_by_ticker[ticker] must remain the same list object after trim"
    )


# ── Test 2: In-place update does not trim or grow ──────────────────────────


@pytest.mark.asyncio
async def test_same_bucket_update_does_not_trim_or_grow(monkeypatch):
    """Multiple ticks within the SAME bucket must not grow or shrink the list.

    The in-place `else` branch (running-bar model_copy) must never touch
    the length; cap logic must be in the new-bucket branch only.
    """
    import backend.algo.live.runtime as _rt_mod

    cap = 3
    monkeypatch.setattr(_rt_mod, "_MAX_BAR_HISTORY", cap)

    runtime = _make_runtime()

    base = date(2026, 2, 1)
    seed_dates = [base + timedelta(days=i) for i in range(cap)]
    runtime._bars_by_ticker[_TICKER] = [
        _backbar(_TICKER, d) for d in seed_dates
    ]

    # Send 3 bars all sharing the SAME bucket date (in-place update).
    same_date = seed_dates[-1]
    ts = _ts_for_date(same_date)

    patches = _bar_close_patches(runtime)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        for _ in range(3):
            bar = _make_bar(ticker=_TICKER, ts_ns=ts)
            await runtime._on_bar_close(bar=bar, last_price=_PRICE)

    history = runtime._bars_by_ticker[_TICKER]
    assert len(history) == cap, (
        f"In-place update must not change len; expected {cap}, got {len(history)}"
    )


# ── Test 3: Closed-entry cache eviction ────────────────────────────────────


def test_evict_stale_closed_entry_cache_removes_old_keys():
    """Old keys (date older than MAX_AGE_DAYS) must be dropped; recent retained.

    Boundary condition: a key at exactly the cutoff date (as_of - MAX_AGE) is
    retained (>= cutoff); a key one day older is evicted.
    """
    from backend.algo.live import runtime as _rt_mod

    runtime = _make_runtime()
    max_age = _rt_mod._CLOSED_ENTRY_CACHE_MAX_AGE_DAYS
    as_of = date(2026, 3, 15)

    old_date = as_of - timedelta(days=max_age + 1)   # evicted
    cutoff_date = as_of - timedelta(days=max_age)     # retained (== cutoff)
    recent_date = as_of - timedelta(days=1)            # retained

    old_key = (_TICKER, old_date)
    cutoff_key = (_TICKER, cutoff_date)
    recent_key = (_TICKER, recent_date)

    runtime._closed_entry_cache = {
        old_key: {"type": "buy"},
        cutoff_key: {"type": "buy"},
        recent_key: {"type": "buy"},
    }

    runtime._evict_stale_closed_entry_cache(as_of=as_of)

    assert old_key not in runtime._closed_entry_cache, (
        f"Key {old_key} (> {max_age} days old) must be evicted"
    )
    assert cutoff_key in runtime._closed_entry_cache, (
        f"Key {cutoff_key} (== cutoff boundary) must be retained"
    )
    assert recent_key in runtime._closed_entry_cache, (
        f"Key {recent_key} (recent) must be retained"
    )


def test_evict_stale_closed_entry_cache_empty_dict_no_crash():
    """Calling eviction on an empty dict must not raise."""
    runtime = _make_runtime()
    assert runtime._closed_entry_cache == {}
    runtime._evict_stale_closed_entry_cache(as_of=date(2026, 3, 1))
    assert runtime._closed_entry_cache == {}
