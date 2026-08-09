"""Task 2 — 09:30 floor for signal-based SELLs (safety exits ungated).

Before this change a SIGNAL-based SELL (AST ``sell``/``exit``,
set_target_weight rebalance) could fire at any wall-clock time,
including inside the 09:00-09:29 pre-open / early-session
observation window that ``_MIN_BUY_TIME_IST`` already protects BUYs
from. This task adds a SELL-side sibling floor
(``_MIN_SELL_TIME_IST``, default 09:30) that defers a resolved
SIGNAL SELL until the floor passes. Safety exits (stop-loss,
time-stop, STOP_HIT, GTT-triggered, MIS square-off) are evaluated
EARLIER in ``_on_bar_close`` and return before the new floor is ever
reached — this suite proves both halves: a signal SELL deferred
pre-floor, and a safety-exit SELL firing unaffected pre-floor.

Harness for the signal-SELL tests mirrors
``test_entry_window_or_trigger.py`` (real ``_on_bar_close`` drive +
frozen wall-clock via patching the real ``datetime.datetime`` class
— required because ``_on_bar_close`` does a FUNCTION-LOCAL
``from datetime import datetime`` re-import on every call, which
shadows any module-level patch of
``backend.algo.live.runtime.datetime``). The safety-exit test
mirrors ``test_stop_loss_live_integration.py`` (unconditional-BUY
strategy + a pre-seeded open position that trips the stop-loss
monitor on the very first bar, short-circuiting AST eval entirely).
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

_TICKER = "SELLFLOOR.NS"
_PRICE = Decimal("500.00")
IST = timezone(timedelta(hours=5, minutes=30))
_TODAY = date(2026, 8, 10)
_TODAY_TS_NS = int(
    datetime(
        _TODAY.year, _TODAY.month, _TODAY.day, tzinfo=timezone.utc,
    ).timestamp()
    * 1_000_000_000
)


class _FrozenDatetime(datetime):
    """Subclass swapped in for the real ``datetime.datetime`` class
    object so a FUNCTION-LOCAL ``from datetime import datetime``
    (executed fresh on every call, inside ``_on_bar_close``) picks
    up the frozen wall-clock. Mirrors
    ``test_entry_window_or_trigger.py``."""

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
    """No-real-PG stand-ins, mirroring
    ``test_entry_window_or_trigger.py``'s helper of the same name.
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


def _exit_strategy_payload() -> dict:
    """Unconditional ``exit`` — any bar with an open position emits
    a SIGNAL SELL for the full qty, without needing a real
    indicator/feature pipeline (same rationale as
    ``test_stop_loss_live_integration.py``'s unconditional-BUY
    payload, mirrored for the SELL side)."""
    return {
        "id": str(uuid4()),
        "name": "sell floor test strategy",
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
        "root": {"type": "exit", "scope": "this_symbol"},
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

    strategy = parse_strategy(_exit_strategy_payload())

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
    kite.place_order = MagicMock(return_value="KITE_ORDER_SELL_FLOOR")

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


def _seed_open_position(
    runtime, *, qty: int, avg_price: Decimal,
) -> None:
    """Drop a synthetic BUY Fill straight into the PositionTracker
    so the ``exit`` action resolves a SELL for an already-open
    position — mirrors
    ``test_stop_loss_live_integration.py::_seed_open_position``."""
    from backend.algo.backtest.types import Fill

    fill = Fill(
        intent_id=uuid4(),
        ticker=_TICKER,
        side="BUY",
        qty=qty,
        fill_price=avg_price,
        fill_date=_TODAY - timedelta(days=1),
        fees_inr=Decimal("0"),
        fee_rates_version="test",
    )
    runtime._positions.apply_fill(fill)


def _seed_bars(runtime) -> None:
    from backend.algo.backtest.types import BarData as _BackBar

    runtime._bars_by_ticker[_TICKER] = [
        _BackBar(
            ticker=_TICKER,
            date=_TODAY - timedelta(days=1),
            open=_PRICE,
            high=_PRICE,
            low=_PRICE,
            close=_PRICE,
            volume=10000,
        ),
    ]


async def _feed_sell_bar(runtime, *, wall_clock: str) -> int:
    """Drive one real bar-close under a frozen wall-clock, with the
    PG-backed budget/risk gates stood in so the SELL runs the full
    ``_on_bar_close`` pipeline (not just the stop-loss short-circuit
    used elsewhere in this package)."""
    gates = _budget_gate_patches()
    with gates[0], gates[1], gates[2], gates[3], gates[4]:
        with _clock(wall_clock):
            return await runtime._on_bar_close(
                bar=_make_bar(), last_price=_PRICE,
            )


# ── Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_sell_before_0930_deferred():
    """AST ``exit`` SELL (rebalance/AST/discretionary — never a
    safety exit) on an open position at 09:20 IST — inside the
    pre-floor observation window — must be deferred (n == 0), never
    reaching Kite."""
    runtime, kite = _make_runtime()
    _seed_open_position(runtime, qty=5, avg_price=Decimal("480"))
    _seed_bars(runtime)

    n = await _feed_sell_bar(runtime, wall_clock="09:20")

    assert n == 0, (
        "a signal-based SELL before the 09:30 floor must be deferred"
    )
    kite.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_replay_signal_sell_before_0930_not_gated():
    """Same signal + same pre-floor wall clock (09:20 IST), but
    ``self._is_replay = True`` (mode=dryrun + source=replay
    rehearsal). Gate S must mirror Gate A's ``not self._is_replay``
    exemption — replay wall-clock is meaningless (see
    ``_on_bar_close``'s ``daily_realtime`` computation) — so the
    SELL must proceed unaffected, not be silently dropped."""
    runtime, kite = _make_runtime()
    runtime._is_replay = True
    _seed_open_position(runtime, qty=5, avg_price=Decimal("480"))
    _seed_bars(runtime)

    n = await _feed_sell_bar(runtime, wall_clock="09:20")

    assert n == 1, (
        "a replay-mode signal SELL before 09:30 IST must NOT be "
        "gated — replay wall-clock is meaningless, mirroring Gate "
        "A's not self._is_replay exemption"
    )
    kite.place_order.assert_called_once()
    kwargs = kite.place_order.call_args.kwargs
    assert kwargs["transaction_type"] == "SELL"


@pytest.mark.asyncio
async def test_signal_sell_at_0930_fires():
    """Same signal, wall clock exactly 09:30 IST. The floor check is
    strict (``now_ist < _MIN_SELL_TIME_IST``), so 09:30:00 itself is
    NOT before the floor and must fire normally."""
    runtime, kite = _make_runtime()
    _seed_open_position(runtime, qty=5, avg_price=Decimal("480"))
    _seed_bars(runtime)

    n = await _feed_sell_bar(runtime, wall_clock="09:30")

    assert n == 1, "a signal SELL at exactly 09:30 IST must fire"
    kite.place_order.assert_called_once()
    kwargs = kite.place_order.call_args.kwargs
    assert kwargs["transaction_type"] == "SELL"


@pytest.mark.asyncio
async def test_safety_sell_fires_before_0930():
    """STOP_HIT / stop-loss exits are handled ABOVE the entry block
    in ``_on_bar_close`` and are NEVER gated by the new SELL floor —
    must still fire at 09:10 IST, well before it. Mirrors
    ``test_stop_loss_live_integration.py::
    test_live_stop_loss_calls_kite_place_order`` with the wall-clock
    additionally frozen pre-floor to make the "never gated" property
    explicit."""
    from backend.algo.backtest.types import BarData as _BackBar
    from backend.algo.backtest.types import Fill
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy
    from backend.algo.stream.types import Bar

    strategy_payload = {
        "id": str(uuid4()),
        "name": "sell floor safety test strategy",
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
        # Unconditional BUY so AST eval, if it ran, would emit a
        # competing BUY on the trigger bar — that lets us assert
        # the stop-loss path short-circuits AST eval (same
        # rationale as test_stop_loss_live_integration.py).
        "root": {"type": "buy", "qty": {"shares": 5}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }
    strategy = parse_strategy(strategy_payload)

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
    kite.place_order = MagicMock(return_value="KITE_ORDER_STOP")

    caps = {"live_orders_enabled": True, "allowed_tickers": []}

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

    fill = Fill(
        intent_id=uuid4(),
        ticker=_TICKER,
        side="BUY",
        qty=10,
        fill_price=Decimal("100"),
        fill_date=_TODAY - timedelta(days=1),
        fees_inr=Decimal("0"),
        fee_rates_version="test",
    )
    runtime._positions.apply_fill(fill)
    runtime._bars_by_ticker[_TICKER] = [
        _BackBar(
            ticker=_TICKER,
            date=_TODAY - timedelta(days=1),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1,
            bar_open_ts_ns=0,
        ),
    ]

    close_price = 94.0  # -6% — trips the 5% stop.
    bar = Bar(
        ticker=_TICKER,
        interval_sec=86400,
        bar_open_ts_ns=_TODAY_TS_NS,
        open=close_price,
        high=close_price,
        low=close_price,
        close=close_price,
        volume=1,
        written_at=datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc),
    )

    with _clock("09:10"):
        n = await runtime._on_bar_close(
            bar=bar, last_price=Decimal(str(close_price)),
        )

    assert n == 1, (
        "a stop-loss safety SELL at 09:10 IST — before the 09:30 "
        "SELL floor — must fire unaffected; safety exits are never "
        "gated by _MIN_SELL_TIME_IST"
    )
    kite.place_order.assert_called_once()
    kwargs = kite.place_order.call_args.kwargs
    assert kwargs["transaction_type"] == "SELL"
    assert kwargs["quantity"] == 10
