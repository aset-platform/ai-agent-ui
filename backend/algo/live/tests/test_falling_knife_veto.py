"""Task 3 — falling-knife veto on daily-strategy BUY entry.

Before this change a resolved BUY (either OR-trigger leg — Task 1)
that cleared the ``_MIN_BUY_TIME_IST`` floor (Gate A) went straight
to order submission with no check on how the name got to oversold.
After this change a BUY is hard-vetoed when the ticker is in
free-fall: prior-3-CLOSED-day return <= ``_KNIFE_3D_PCT`` (default
-10.0) OR entry-day gap-down (today's forming-bar open vs
yesterday's close) <= ``_KNIFE_GAP_PCT`` (default -4.0). A veto
emits a ``signal_rejected`` event (``reason="falling_knife_veto"``)
carrying both metrics and returns 0 — no order.

Harness mirrors ``test_entry_window_or_trigger.py`` (Task 1's real
``_on_bar_close`` drive, frozen wall-clock via patching the real
``datetime.datetime`` class, ``compute_indicators`` patched at its
SOURCE module per CLAUDE.md #16) with one addition: seeded history
carries REAL close prices (4 closed bars, so ``_in_free_fall`` has
enough data for the 3-day return) and the incoming daily bar's
``open`` is parameterized per test to drive the gap leg.
"""
from __future__ import annotations

import importlib.util
import json
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

_TICKER = "KNIFE.NS"
_PRICE = Decimal("500.00")
IST = timezone(timedelta(hours=5, minutes=30))

# 4 seeded CLOSED trading days -> _in_free_fall has the >=4 closed
# bars it needs for the 3-day-return leg. "Today" (Monday) becomes
# a NEW bucket appended onto this seeded history.
_SEED_DATES = [
    date(2026, 8, 4),
    date(2026, 8, 5),
    date(2026, 8, 6),
    date(2026, 8, 7),
]
_TODAY = date(2026, 8, 10)
_TODAY_TS_NS = int(
    datetime(
        _TODAY.year, _TODAY.month, _TODAY.day, tzinfo=timezone.utc,
    ).timestamp()
    * 1_000_000_000
)


def _strategy_payload() -> dict:
    """rsi_2 <= 5 -> set_target_weight(0.2); else hold. Same shape
    as test_entry_window_or_trigger.py's payload."""
    return {
        "id": str(uuid4()),
        "name": "falling-knife veto test strategy",
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


def _make_bar(open_price: Decimal):
    from backend.algo.stream.types import Bar

    return Bar(
        ticker=_TICKER,
        interval_sec=86400,
        bar_open_ts_ns=_TODAY_TS_NS,
        open=float(open_price),
        high=float(open_price),
        low=float(open_price),
        close=float(open_price),
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
        # this test focused on the veto gate, not sizing.
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
    kite.place_order = MagicMock(return_value="KITE_ORDER_KNIFE")
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


def _seed_bars(runtime, closes: list[float]) -> None:
    from backend.algo.backtest.types import BarData as _BackBar

    assert len(closes) == len(_SEED_DATES)
    runtime._bars_by_ticker[_TICKER] = [
        _BackBar(
            ticker=_TICKER,
            date=d,
            open=Decimal(str(c)),
            high=Decimal(str(c)),
            low=Decimal(str(c)),
            close=Decimal(str(c)),
            volume=10000,
        )
        for d, c in zip(_SEED_DATES, closes)
    ]


def _fake_compute_indicators(rsi2_closed: float, rsi2_forming: float):
    """Steer rsi_2 independently for the "closed" call (seeded bars
    only, length == len(_SEED_DATES)) and the "forming" call (seeded
    bars + today's appended bar, length == len(_SEED_DATES) + 1)."""

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
    up the frozen wall-clock."""

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
    mirroring test_entry_window_or_trigger.py's
    ``_budget_gate_patches()``. ``budget_reserve``/``budget_transition``
    are already patched by the package-level autouse fixture in
    conftest.py."""
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
    runtime,
    *,
    wall_clock: str,
    rsi2_forming: float,
    rsi2_closed: float,
    today_open: Decimal,
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
                bar=_make_bar(today_open), last_price=_PRICE,
            )


def _rejection_events(runtime, reason: str) -> list[dict]:
    out = []
    for row in runtime._events:
        if row["type"] != "signal_rejected":
            continue
        if json.loads(row["payload_json"]).get("reason") == reason:
            out.append(row)
    return out


# ── Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_veto_rejects_free_fall_3d_return():
    """Prior-3-closed-day return -14% (100 -> 86) breaches the
    default -10% threshold. No order; a signal_rejected event
    carries reason=falling_knife_veto and the computed metrics."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime, [100, 96, 90, 86])

    n = await _feed_bar(
        runtime,
        wall_clock="10:30",
        rsi2_forming=3.0,
        rsi2_closed=60.0,
        today_open=Decimal("86"),  # == yesterday's close, no gap
    )

    assert n == 0, "a knife-falling BUY must be vetoed, not filled"
    kite.place_order.assert_not_called()

    rejections = _rejection_events(runtime, "falling_knife_veto")
    assert len(rejections) == 1
    payload = json.loads(rejections[0]["payload_json"])
    assert payload["ticker"] == _TICKER
    assert payload["side"] == "BUY"
    assert payload["ret_3d_pct"] == pytest.approx(-14.0)
    assert payload["gap_pct"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_veto_rejects_gap_down():
    """Flat 3-day closes (0% return) but today's forming-bar open
    gaps -4.5% below yesterday's close, breaching the default -4%
    gap threshold. No order."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime, [100, 100, 100, 100])

    n = await _feed_bar(
        runtime,
        wall_clock="10:30",
        rsi2_forming=3.0,
        rsi2_closed=60.0,
        today_open=Decimal("95.5"),
    )

    assert n == 0, "a gap-down BUY must be vetoed, not filled"
    kite.place_order.assert_not_called()

    rejections = _rejection_events(runtime, "falling_knife_veto")
    assert len(rejections) == 1
    payload = json.loads(rejections[0]["payload_json"])
    assert payload["ret_3d_pct"] == pytest.approx(0.0)
    assert payload["gap_pct"] == pytest.approx(-4.5)


@pytest.mark.asyncio
async def test_normal_dip_passes_veto():
    """A shallow -2% 3-day dip with no gap does NOT breach either
    threshold — the veto must not block a legitimate oversold
    entry."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime, [100, 99, 98, 98])

    n = await _feed_bar(
        runtime,
        wall_clock="10:30",
        rsi2_forming=3.0,
        rsi2_closed=60.0,
        today_open=Decimal("98"),  # == yesterday's close, no gap
    )

    assert n == 1, "a normal shallow dip must not be vetoed"
    kite.place_order.assert_called_once()
    assert _rejection_events(runtime, "falling_knife_veto") == []
