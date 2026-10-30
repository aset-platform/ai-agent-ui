"""Task 6 — safety-invariant regression for the entry-window redesign.

Tasks 1-5 rewrote the ENTRY path (all-day OR-trigger, the 09:30 BUY
floor, the falling-knife veto, the shadow entry_strength_snapshot,
and the universe-oversold breadth helper). None of that touches the
SAFETY-exit path (stop-loss / STOP_HIT / GTT-triggered exits), which
in ``_on_bar_close`` is evaluated and returned BEFORE the entry block
is ever reached. This module is a standalone regression guard proving
that invariant still holds after Tasks 1-5 landed:

1. ``test_stop_hit_fires_pre_0930_after_entry_changes`` — a
   stop-loss/STOP_HIT SELL at 09:05 IST (well before the new 09:30
   BUY/SELL floors) still reaches Kite. Mirrors the harness in
   ``test_stop_loss_live_integration.py`` (unconditional-BUY strategy
   + a pre-seeded losing position so the stop-loss monitor
   short-circuits AST eval on the very first bar) with the wall clock
   additionally frozen pre-floor, the same technique
   ``test_sell_observation_floor.py::test_safety_sell_fires_before_0930``
   already uses to prove safety exits are never gated.

2. ``test_gtt_exit_releases_budget_reservation`` — a GTT-triggered
   exit (Piece A: ``_ratchet_all_gtts`` polling ``kite.get_gtts()``)
   still calls ``_release_budget_reservation_for_gtt_exit`` (via
   ``budget_reserve`` + ``budget_transition``). This is a verbatim
   replication of the existing assertion in
   ``backend/algo/tests/test_gtt_exit_budget_release.py::``
   ``TestPieceAReleasesBudgetOnRatchetPoll::``
   ``test_gtt_triggered_exit_releases_budget`` — the plan's "reuse
   the existing assertion" instruction, kept here as an independent
   guard so a future entry-path change that regresses this is caught
   by the same suite that pins the stop-loss invariant above. (Note:
   the plan names ``test_gtt_trailing_integration.py`` as the source
   file, but the budget-reservation assertion actually lives in
   ``backend/algo/tests/test_gtt_exit_budget_release.py`` — this file
   has no such assertion. Mirrored from the correct source instead of
   inventing new plumbing.)
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# Heavy backend deps (pyarrow + PEP 604 union syntax) only resolve
# inside the Docker backend container — gate the suite identically
# to the other live-runtime integration tests in this package.
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

_TICKER = "SAFETYREG.NS"
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
    (executed fresh on every call, inside ``_on_bar_close``) picks up
    the frozen wall-clock. Mirrors
    ``test_sell_observation_floor.py``/``test_entry_window_or_trigger
    .py``."""

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


# ── (a) stop-loss SELL still fires pre-09:30 ──────────────────────


def _stop_hit_strategy_payload() -> dict:
    """Unconditional BUY so AST eval, if it ran, would emit a
    competing BUY on the trigger bar — that lets us assert the
    stop-loss path short-circuits AST eval (mirrors
    ``test_stop_loss_live_integration.py::_strategy_payload``)."""
    return {
        "id": str(uuid4()),
        "name": "entry-change safety regression: stop-loss",
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


def _make_stop_hit_runtime():
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_stop_hit_strategy_payload())

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
    kite.place_order = MagicMock(
        return_value="KITE_ORDER_SAFETY_REGRESSION",
    )

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
    return runtime, kite


def _seed_losing_position(runtime, *, qty: int) -> None:
    from backend.algo.backtest.types import Fill

    fill = Fill(
        intent_id=uuid4(),
        ticker=_TICKER,
        side="BUY",
        qty=qty,
        fill_price=Decimal("100"),
        fill_date=_TODAY - timedelta(days=1),
        fees_inr=Decimal("0"),
        fee_rates_version="test",
    )
    runtime._positions.apply_fill(fill)


def _seed_bar_history(runtime) -> None:
    from backend.algo.backtest.types import BarData as _BackBar

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


@pytest.mark.asyncio
async def test_stop_hit_fires_pre_0930_after_entry_changes():
    """A stop-loss/STOP_HIT SELL at 09:05 IST — well inside the new
    09:30 BUY/SELL observation window added by Tasks 1-2 — must still
    submit a SELL. Safety exits are evaluated ABOVE the entry block
    in ``_on_bar_close`` and are NEVER gated by ``_MIN_BUY_TIME_IST``/
    ``_MIN_SELL_TIME_IST``; this pins that invariant against the
    Tasks 1-5 entry-path rewrite (OR-trigger, falling-knife veto,
    shadow snapshot, breadth helper)."""
    runtime, kite = _make_stop_hit_runtime()
    _seed_losing_position(runtime, qty=10)
    _seed_bar_history(runtime)

    close_price = 94.0  # -6% — trips the 5% stop_loss_pct.
    from backend.algo.stream.types import Bar

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

    with _clock("09:05"):
        n = await runtime._on_bar_close(
            bar=bar, last_price=Decimal(str(close_price)),
        )

    assert n == 1, (
        "a stop-loss safety SELL at 09:05 IST must fire unaffected "
        "by the entry-window redesign — STOP_HIT is never gated"
    )
    kite.place_order.assert_called_once()
    kwargs = kite.place_order.call_args.kwargs
    assert kwargs["transaction_type"] == "SELL"
    assert kwargs["quantity"] == 10


# ── (b) GTT-triggered exit still releases the budget reservation ──
#
# Verbatim replication of the setup + assertions from
# ``backend/algo/tests/test_gtt_exit_budget_release.py::``
# ``TestPieceAReleasesBudgetOnRatchetPoll::``
# ``test_gtt_triggered_exit_releases_budget`` — kept as an
# independent regression guard in this Task-6 module (see module
# docstring for why the plan's named source file doesn't actually
# contain this assertion).


def _make_gtt_exit_runtime():
    from backend.algo.broker.kite_client import KiteClient
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.risk.per_trade.trailing_trigger_pct = 5.0
    strategy.risk.per_trade.trailing_atr_multiplier = 1.5
    strategy.risk.per_trade.stop_loss_pct = 5.0
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    strategy.schedule = MagicMock()
    strategy.schedule.interval = "1d"
    strategy.product = "CNC"

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {"live_orders_enabled": True}
    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as mock_kc:
        kc_instance = MagicMock()
        mock_kc.return_value = kc_instance
        kite = KiteClient(
            api_key="k", access_token="tok", dry_run=False,
        )
        kite._kc = kc_instance

    caps: dict = {
        "live_orders_enabled": True,
        "allowed_tickers": ["HSCL.NS"],
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


@pytest.mark.asyncio
async def test_gtt_exit_releases_budget_reservation():
    """``_ratchet_all_gtts`` (Piece A poll) is sync and runs via
    ``asyncio.to_thread`` in production, dispatching the budget
    release via ``run_coroutine_threadsafe`` onto ``self._loop``. Set
    ``self._loop`` to the test's own running loop and drive
    ``_ratchet_all_gtts`` from a real worker thread
    (``asyncio.to_thread``) to exercise this exactly as production
    does — calling it directly on the loop thread hits the
    deadlock-avoidance skip branch instead."""
    from backend.algo.backtest.types import Fill as _Fill

    runtime = _make_gtt_exit_runtime()
    runtime._loop = asyncio.get_running_loop()

    # A real open position, as if opened by an earlier BUY fill --
    # _ratchet_all_gtts falls back to the position tracker's qty
    # when Kite's GTT order definition doesn't expose it.
    runtime._positions.apply_fill(_Fill(
        intent_id=uuid4(), ticker="HSCL.NS", side="BUY", qty=4,
        fill_price=Decimal("642.6"), fill_date=date.today(),
        fees_inr=Decimal("0"), fee_rates_version="test",
    ))

    gtt_id = 555
    runtime._trailing_managers["HSCL.NS"] = MagicMock(
        current_stop=610.0,
        state=MagicMock(phase=MagicMock(value="trail")),
    )
    runtime._gtt_ids["HSCL.NS"] = gtt_id
    runtime._ws_hwm["HSCL.NS"] = 640.0

    # get_gtts() no longer lists our tracked gtt_id -> triggered.
    runtime._kite.get_gtts = MagicMock(return_value=[])

    with patch(
        "backend.algo.live.runtime.budget_reserve",
        new_callable=AsyncMock,
    ) as reserve_mock, patch(
        "backend.algo.live.runtime.budget_transition",
        new_callable=AsyncMock,
    ) as transition_mock:
        reserve_mock.return_value = uuid4()

        await asyncio.to_thread(runtime._ratchet_all_gtts)

    reserve_mock.assert_awaited_once()
    _, reserve_kwargs = reserve_mock.call_args
    assert reserve_kwargs["ticker"] == "HSCL.NS"
    assert reserve_kwargs["side"] == "SELL"
    transition_mock.assert_awaited_once()
