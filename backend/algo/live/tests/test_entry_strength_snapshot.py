"""Task 5 — shadow ``entry_strength_snapshot`` event.

When a BUY is about to fire (after the falling-knife veto — Task 3 —
passes, before order submission) the runtime now emits a NON-GATING
``entry_strength_snapshot`` event capturing entry context (trigger
leg, RSI2, SMA distances, knife metrics, universe breadth) for later
Release-2 calibration. The emit is best-effort: any failure is caught
and logged, never blocking or altering the order path.

Harness mirrors ``test_falling_knife_veto.py`` (Task 1's real
``_on_bar_close`` drive, frozen wall-clock via patching the real
``datetime.datetime`` class, ``compute_indicators`` patched at its
SOURCE module per CLAUDE.md #16, 4 seeded closed bars so
``_in_free_fall`` has enough history for the 3-day-return leg).
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

_TICKER = "SNAPSHOT.NS"
_PRICE = Decimal("500.00")
IST = timezone(timedelta(hours=5, minutes=30))

# 4 seeded CLOSED trading days -> _in_free_fall has the >=4 closed
# bars it needs for the 3-day-return leg. "Today" (Monday) becomes a
# NEW bucket appended onto this seeded history.
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
    as test_falling_knife_veto.py's payload."""
    return {
        "id": str(uuid4()),
        "name": "entry strength snapshot test strategy",
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


def _make_bar(open_price: Decimal = _PRICE):
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
        # this test focused on the snapshot emit, not sizing.
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
    kite.place_order = MagicMock(return_value="KITE_ORDER_SNAPSHOT")
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
    # Universe breadth (Task 4) is a pure count over this dict —
    # seed a small, deterministic cache so the payload's
    # breadth_oversold/breadth_total are non-trivial.
    runtime._last_rsi2 = {"A.NS": 3.0, "B.NS": 4.9, "C.NS": 55.0}
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
            "distance_from_sma50": Decimal("-0.02"),
            "distance_from_sma200": Decimal("-0.05"),
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
    mirroring test_falling_knife_veto.py's ``_budget_gate_patches()``.
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
    runtime,
    *,
    wall_clock: str,
    rsi2_forming: float,
    rsi2_closed: float,
    today_open: Decimal = _PRICE,
    extra_patches: tuple = (),
) -> int:
    """Patch compute_indicators + freeze the wall clock, then drive
    a real bar-close. Returns the fill count (0 or 1)."""
    fake_ci = _fake_compute_indicators(rsi2_closed, rsi2_forming)
    gates = _budget_gate_patches()
    with patch(
        "backend.algo.backtest.indicators.compute_indicators",
        side_effect=fake_ci,
    ), gates[0], gates[1], gates[2], gates[3], gates[4]:
        with _ctx_stack(extra_patches):
            with _clock(wall_clock):
                return await runtime._on_bar_close(
                    bar=_make_bar(today_open), last_price=_PRICE,
                )


@contextmanager
def _ctx_stack(patches: tuple):
    """Enter an arbitrary tuple of context managers together."""
    if not patches:
        yield
        return
    with patches[0]:
        with _ctx_stack(patches[1:]):
            yield


def _snapshot_events(runtime) -> list[dict]:
    return [
        row
        for row in runtime._events
        if row["type"] == "entry_strength_snapshot"
    ]


# ── Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_emitted_and_nonblocking():
    """An intraday-forming BUY (yesterday's closed bar not oversold,
    no knife veto) emits exactly one entry_strength_snapshot event
    with the intraday_forming trigger, populated rsi2_forming, and
    the breadth counts — and the order still submits (snapshot is
    non-gating)."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime, [100, 99, 98, 98])  # shallow dip, no veto

    n = await _feed_bar(
        runtime,
        wall_clock="10:30",
        rsi2_forming=3.0,
        rsi2_closed=60.0,
        today_open=Decimal("98"),  # == yesterday's close, no gap
    )

    assert n == 1, "order still placed — snapshot never blocks"
    kite.place_order.assert_called_once()

    snaps = _snapshot_events(runtime)
    assert len(snaps) == 1
    p = json.loads(snaps[0]["payload_json"])
    assert p["trigger"] == "intraday_forming"
    # Decimal feature values round-trip through
    # ``json.dumps(default=str)`` as strings (CLAUDE.md #17 —
    # sanitise-at-write-boundary convention); float() normalizes
    # for comparison regardless of whether json already coerced
    # to a number.
    assert float(p["rsi2_forming"]) == pytest.approx(3.0)
    assert float(p["dist_sma50"]) == pytest.approx(-0.02)
    assert float(p["dist_sma200"]) == pytest.approx(-0.05)
    assert "breadth_oversold" in p and "breadth_total" in p
    # Seeded cache is {A: 3.0, B: 4.9, C: 55.0} (2 oversold of 3) —
    # plus this bar's own ticker gets folded into ``_last_rsi2`` by
    # the eval that ran just above (rsi2_forming=3.0, oversold),
    # so the breadth read at the snapshot site sees 3 of 4.
    assert (p["breadth_oversold"], p["breadth_total"]) == (3, 4)
    assert "ret_3d_pct" in p and "gap_pct" in p
    assert p["ret_3d_pct"] == pytest.approx(-2.0)
    assert p["gap_pct"] == pytest.approx(0.0)
    assert p["ticker"] == _TICKER


@pytest.mark.asyncio
async def test_snapshot_trigger_both_legs():
    """Both OR-trigger legs resolve BUY -> trigger == "both"."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime, [100, 99, 98, 98])

    n = await _feed_bar(
        runtime,
        wall_clock="10:30",
        rsi2_forming=3.0,
        rsi2_closed=3.0,
        today_open=Decimal("98"),
    )

    assert n == 1
    snaps = _snapshot_events(runtime)
    assert len(snaps) == 1
    p = json.loads(snaps[0]["payload_json"])
    assert p["trigger"] == "both"


@pytest.mark.asyncio
async def test_snapshot_trigger_yesterday_close_leg():
    """Only the carried closed-bar leg resolves BUY (today's forming
    bar has bounced) -> trigger == "yesterday_close"."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime, [100, 99, 98, 98])

    n = await _feed_bar(
        runtime,
        wall_clock="10:30",
        rsi2_forming=42.0,
        rsi2_closed=4.0,
        today_open=Decimal("98"),
    )

    assert n == 1
    snaps = _snapshot_events(runtime)
    assert len(snaps) == 1
    p = json.loads(snaps[0]["payload_json"])
    assert p["trigger"] == "yesterday_close"


@pytest.mark.asyncio
async def test_knife_vetoed_buy_produces_no_snapshot():
    """A knife-vetoed BUY returns before the emit site is reached —
    no entry_strength_snapshot event, only signal_rejected."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime, [100, 96, 90, 86])  # -14% 3d return, breaches

    n = await _feed_bar(
        runtime,
        wall_clock="10:30",
        rsi2_forming=3.0,
        rsi2_closed=60.0,
        today_open=Decimal("86"),  # == yesterday's close, no gap
    )

    assert n == 0, "a knife-falling BUY must be vetoed, not filled"
    kite.place_order.assert_not_called()
    assert _snapshot_events(runtime) == []
    reasons = {
        json.loads(row["payload_json"]).get("reason")
        for row in runtime._events
        if row["type"] == "signal_rejected"
    }
    assert "falling_knife_veto" in reasons


@pytest.mark.asyncio
async def test_snapshot_emit_failure_does_not_block_order():
    """Forcing event_row to raise on the snapshot's own emit must
    not prevent the BUY from being submitted — proves the emit is
    genuinely best-effort/non-blocking, not merely untested."""
    runtime, kite = _make_runtime()
    _seed_bars(runtime, [100, 99, 98, 98])  # shallow dip, no veto

    call_count = {"n": 0}
    from backend.algo.live import runtime as runtime_mod

    real_event_row = runtime_mod.event_row

    def _flaky_event_row(*args, **kwargs):
        call_count["n"] += 1
        if kwargs.get("type_") == "entry_strength_snapshot":
            raise RuntimeError("boom — simulated emit failure")
        return real_event_row(*args, **kwargs)

    n = await _feed_bar(
        runtime,
        wall_clock="10:30",
        rsi2_forming=3.0,
        rsi2_closed=60.0,
        today_open=Decimal("98"),
        extra_patches=(
            patch(
                "backend.algo.live.runtime.event_row",
                side_effect=_flaky_event_row,
            ),
        ),
    )

    assert n == 1, (
        "the BUY must still submit even though the snapshot's own "
        "emit raised — the try/except must swallow it, not the "
        "order path"
    )
    kite.place_order.assert_called_once()
    assert _snapshot_events(runtime) == []
