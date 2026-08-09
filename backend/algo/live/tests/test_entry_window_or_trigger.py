"""Task 1 — OR-trigger all-day entry (remove Gate B / _MIN_EVAL_TIME_IST).

Before this change a flat allowed-ticker's BUY was deferred to
14:20 IST unless yesterday's CLOSED bar ALSO confirmed the entry
(dual-bar confirmation). After this change the 09:30 BUY floor
(``_MIN_BUY_TIME_IST``) is the only wall-clock gate: from 09:30
onward a flat ticker enters on EITHER today's forming-bar signal
being BUY OR yesterday's closed bar being BUY (OR-trigger).

Harness mirrors ``test_balance_cap.py`` (real ``_on_bar_close`` drive,
budget-gate patches) + ``test_mis_e2e_smoke.py`` (datetime freeze via
patching the real ``datetime.datetime`` class — required because
``_on_bar_close`` and ``_eval_entry_on_closed_bar`` both do a
FUNCTION-LOCAL ``from datetime import datetime`` re-import on every
call, which shadows any module-level patch of
``backend.algo.live.runtime.datetime``; only patching the real
``datetime.datetime`` class object reaches that local re-import).

``compute_indicators`` (also function-locally imported in both call
sites, from ``backend.algo.backtest.indicators``) is patched at its
SOURCE module per CLAUDE.md #16 so both the full-history (forming)
eval and the ``history[:-1]`` (closed) eval in
``_eval_entry_on_closed_bar`` can be independently steered to a
chosen RSI(2) value, without needing a real multi-day price series
that happens to produce that exact indicator value.
"""
from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
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

_TICKER = "ORTRIGGER.NS"
_PRICE = Decimal("500.00")
IST = timezone(timedelta(hours=5, minutes=30))

# Seeded "closed" history: 3 prior trading days. "Today" is the
# following Monday — the bar fed into _on_bar_close, which becomes
# a NEW bucket appended onto the seeded history (full history length
# == len(_SEED_DATES) + 1).
_SEED_DATES = [date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]
_TODAY = date(2026, 8, 10)
_TODAY_TS_NS = int(
    datetime(
        _TODAY.year, _TODAY.month, _TODAY.day, tzinfo=timezone.utc,
    ).timestamp()
    * 1_000_000_000
)


def _strategy_payload() -> dict:
    """rsi_2 <= 5 -> set_target_weight(0.2); else hold. Same shape
    as test_live_order_gate.py's payload — set_target_weight sizes
    a fresh BUY off current equity / last_price when flat."""
    return {
        "id": str(uuid4()),
        "name": "entry window OR-trigger test strategy",
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


def _make_bar():
    from backend.algo.stream.types import Bar

    return Bar(
        ticker=_TICKER,
        interval_sec=86400,
        bar_open_ts_ns=_TODAY_TS_NS,
        open=float(_PRICE),
        high=float(_PRICE),
        low=float(_PRICE),
        close=float(_PRICE),
        volume=10000,
        written_at=datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc),
    )


def _make_runtime():
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_strategy_payload())

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        # max_inr=0 -> strategy budget-cap block is a no-op, keeping
        # this test focused on the entry-window gate, not sizing.
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
    kite.place_order = MagicMock(return_value="KITE_ORDER_OR_TRIGGER")
    kite._get_redis.return_value = MagicMock()

    caps = {"live_orders_enabled": True, "allowed_tickers": [_TICKER]}

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ):
        runtime = LiveRuntime(
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
    return runtime, kite


def _seed_bars(runtime) -> None:
    from backend.algo.backtest.types import BarData as _BackBar

    runtime._bars_by_ticker[_TICKER] = [
        _BackBar(
            ticker=_TICKER,
            date=d,
            open=_PRICE,
            high=_PRICE,
            low=_PRICE,
            close=_PRICE,
            volume=10000,
        )
        for d in _SEED_DATES
    ]


def _fake_compute_indicators(rsi2_closed: float, rsi2_forming: float):
    """Steer rsi_2 independently for the two call sites that share
    ``compute_indicators``: the "closed" call (seeded bars only,
    length == len(_SEED_DATES)) and the "forming" call (seeded bars
    + today's appended bar, length == len(_SEED_DATES) + 1)."""

    def _fake(bars):
        last = bars[-1]
        base = {
            "today_ltp": last.close,
            "today_vol": Decimal(last.volume),
        }
        if len(bars) == len(_SEED_DATES):
            return {
                last.date: {**base, "rsi_2": Decimal(str(rsi2_closed))},
            }
        return {
            last.date: {**base, "rsi_2": Decimal(str(rsi2_forming))},
        }

    return _fake


class _FrozenDatetime(datetime):
    """Subclass swapped in for the real ``datetime.datetime`` class
    object so a FUNCTION-LOCAL ``from datetime import datetime``
    (executed fresh on every call, inside ``_on_bar_close``) picks
    up the frozen wall-clock. Everything except ``now()`` is
    inherited unchanged (fromtimestamp, replace, isinstance, ...)."""

    _frozen: "datetime | None" = None

    @classmethod
    def now(cls, tz=None):
        frozen = cls._frozen
        if frozen is None:
            return super().now(tz)
        return frozen.astimezone(tz) if tz is not None else frozen


@contextmanager
def _clock(hhmm: str):
    hh, mm = (int(x) for x in hhmm.split(":"))
    frozen = datetime(
        _TODAY.year, _TODAY.month, _TODAY.day, hh, mm, tzinfo=IST,
    )
    _FrozenDatetime._frozen = frozen
    try:
        with patch("datetime.datetime", _FrozenDatetime):
            yield
    finally:
        _FrozenDatetime._frozen = None


def _budget_gate_patches():
    """No-real-PG stand-ins for the atomic BUY gate + pre_trade_check,
    mirroring test_balance_cap.py's ``_budget_gate_patches()``.
    ``budget_reserve``/``budget_transition`` are already patched by
    the package-level autouse fixture in conftest.py."""
    from backend.algo.paper.types import RiskDecision

    return (
        patch(
            "backend.algo.live.runtime.budget_reserve_if_headroom",
            new=AsyncMock(return_value=uuid4()),
        ),
        patch(
            "backend.algo.live.runtime.budget_load_user",
            new=AsyncMock(),
        ),
        patch(
            "backend.algo.live.runtime.budget_active_for_strategy",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.live.runtime.get_tick_size",
            return_value=Decimal("0.05"),
        ),
        patch(
            "backend.algo.live.runtime.pre_trade_check",
            new=AsyncMock(
                return_value=RiskDecision(outcome="accept"),
            ),
        ),
    )


async def _feed_bar(
    runtime, *, wall_clock: str, rsi2_forming: float, rsi2_closed: float,
) -> int:
    """Patch compute_indicators + freeze the wall clock, then drive
    a real bar-close. Returns the fill count (0 or 1)."""
    fake_ci = _fake_compute_indicators(rsi2_closed, rsi2_forming)
    gates = _budget_gate_patches()
    with patch(
        "backend.algo.backtest.indicators.compute_indicators",
        side_effect=fake_ci,
    ), gates[0], gates[1], gates[2], gates[3], gates[4]:
        with _clock(wall_clock):
            return await runtime._on_bar_close(
                bar=_make_bar(), last_price=_PRICE,
            )


# ── Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intraday_forming_dip_enters_after_0930():
    """Forming bar RSI2<=5 today (oversold NOW), yesterday's closed
    bar was not (60.0). Wall clock 10:15 IST — well past the 09:30
    floor but well before the old 14:20 eval gate. Before this fix,
    a today-forming-only BUY was deferred until 14:20; now it must
    fire immediately."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime)

    n = await _feed_bar(
        runtime,
        wall_clock="10:15",
        rsi2_forming=3.0,
        rsi2_closed=60.0,
    )

    assert n == 1, (
        "forming-bar-only BUY at 10:15 IST must enter immediately "
        "under the OR-trigger, not defer to 14:20"
    )
    kite.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_yesterday_oversold_but_bounced_still_enters():
    """Yesterday's CLOSED bar was oversold (RSI2<=5 -> BUY) but
    today's forming bar has already bounced back above 5 (no BUY on
    full history). Wall clock 11:00 IST. The OR-trigger's carried
    closed-bar leg must still fire the entry."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime)

    n = await _feed_bar(
        runtime,
        wall_clock="11:00",
        rsi2_forming=42.0,
        rsi2_closed=4.0,
    )

    assert n == 1, (
        "yesterday's closed-bar BUY signal must still enter via the "
        "OR-trigger even though today's forming bar no longer "
        "confirms it"
    )
    kite.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_buy_before_0930_deferred():
    """Both legs say BUY (forming AND closed both oversold), but
    wall clock is 09:20 IST — before the 09:30 floor. Gate A (the
    BUY floor) defers regardless of the OR-trigger; this floor is
    unchanged by this task."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime)

    n = await _feed_bar(
        runtime,
        wall_clock="09:20",
        rsi2_forming=3.0,
        rsi2_closed=3.0,
    )

    assert n == 0, (
        "a BUY before the 09:30 floor must still be deferred, "
        "regardless of the OR-trigger"
    )
    kite.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_closed_only_buy_before_floor_deferred():
    """Fix-loop round 2 — CRITICAL regression guard. Yesterday's
    closed bar was oversold (RSI2<=5 -> BUY) but today's forming bar
    is NOT a BUY (bounced), same shape as
    test_yesterday_oversold_but_bounced_still_enters -- except wall
    clock is 09:16 IST, INSIDE the 09:00-09:29 observation window
    that must place zero orders.

    Before the round-2 fix, the 09:30 floor check ran BEFORE the
    OR-trigger resolved `signal`, and only ever looked at the
    forming-bar signal (`forming_is_buy`). Since forming_is_buy was
    False here, the floor check was skipped entirely -- then
    `signal = closed_entry` overwrote `signal` with a BUY and NO
    wall-clock check ever ran again, so this scenario placed a live
    order at 09:16 IST. The floor must gate a resolved BUY from
    EITHER leg, applied once, after the OR-trigger resolves."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime)

    n = await _feed_bar(
        runtime,
        wall_clock="09:16",
        rsi2_forming=42.0,
        rsi2_closed=4.0,
    )

    assert n == 0, (
        "a BUY resolved via the CLOSED-bar leg before 09:30 IST "
        "must still be deferred by the BUY floor -- the floor must "
        "gate BOTH legs of the OR-trigger, not just the forming-bar "
        "leg"
    )
    kite.place_order.assert_not_called()
