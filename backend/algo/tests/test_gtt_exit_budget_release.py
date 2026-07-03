"""GTT-triggered exits (both Piece A poll and Piece B postback) must
release the matching BUY's budget reservation, not just apply the
fill to the in-memory position tracker + emit algo.events.

Found 2026-07-03: neither path ever wrote a SELL row to
algo.budget_reservations. sum_open_position_cost (Cap 0 pool-wide
budget headroom) only nets a BUY's cost basis out when a matching
FILLED SELL exists -- so every GTT-closed position stayed "open"
forever in the ledger, permanently understating headroom for ALL the
user's strategies. Confirmed via a live incident: HSCL, SKYGOLD,
SOUTHBANK, ZENTEC (all GTT-closed) inflated open_pos_cost from a real
₹9,399.78 to a ledger-reported ₹17,483.28, causing a BHEL BUY to be
rejected with reason=live_budget_cap despite real headroom existing.
"""
from __future__ import annotations

import importlib
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.broker.kite_client import KiteClient
from backend.algo.live.budget_types import ReservationState

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires the backend Docker stack (pyarrow + py>=3.10)",
)


def _make_runtime():
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
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        kite = KiteClient(
            api_key="k", access_token="tok", dry_run=False,
        )
        kite._kc = kc_instance

    caps: dict = {
        "live_orders_enabled": True, "allowed_tickers": ["HSCL.NS"],
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


class TestReleaseBudgetReservationForGttExit:
    """The core helper, in isolation."""

    @pytest.mark.asyncio
    async def test_creates_and_fills_a_sell_reservation(self):
        runtime = _make_runtime()
        with patch(
            "backend.algo.live.runtime.budget_reserve",
            new_callable=AsyncMock,
        ) as reserve_mock, patch(
            "backend.algo.live.runtime.budget_transition",
            new_callable=AsyncMock,
        ) as transition_mock:
            reserve_mock.return_value = uuid4()

            await runtime._release_budget_reservation_for_gtt_exit(
                ticker="HSCL.NS", qty=4, fill_price=673.55,
            )

        reserve_mock.assert_awaited_once()
        _, reserve_kwargs = reserve_mock.call_args
        assert reserve_kwargs["side"] == "SELL"
        assert reserve_kwargs["ticker"] == "HSCL.NS"
        assert reserve_kwargs["qty"] == 4
        assert reserve_kwargs["reserved_inr"] == Decimal("2694.20")
        assert reserve_kwargs["metadata"]["mode"] == "live"

        transition_mock.assert_awaited_once()
        _, transition_kwargs = transition_mock.call_args
        assert (
            transition_kwargs["reservation_id"]
            == reserve_mock.return_value
        )
        assert transition_kwargs["new_state"] == ReservationState.FILLED
        assert transition_kwargs["filled_qty"] == 4
        assert transition_kwargs["filled_inr"] == Decimal("2694.20")

    @pytest.mark.asyncio
    async def test_reserve_failure_is_swallowed_not_raised(self):
        """Best-effort: a budget-ledger blip must never propagate up
        and break the caller's fill-application flow."""
        runtime = _make_runtime()
        with patch(
            "backend.algo.live.runtime.budget_reserve",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            await runtime._release_budget_reservation_for_gtt_exit(
                ticker="HSCL.NS", qty=4, fill_price=673.55,
            )
        # No exception propagated -- test reaching here is the assertion.

    @pytest.mark.asyncio
    async def test_transition_failure_is_swallowed_not_raised(self):
        runtime = _make_runtime()
        with patch(
            "backend.algo.live.runtime.budget_reserve",
            new_callable=AsyncMock,
            return_value=uuid4(),
        ), patch(
            "backend.algo.live.runtime.budget_transition",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            await runtime._release_budget_reservation_for_gtt_exit(
                ticker="HSCL.NS", qty=4, fill_price=673.55,
            )


class TestPieceBReleasesBudgetOnPostback:
    @pytest.mark.asyncio
    async def test_apply_gtt_triggered_sell_fill_releases_budget(self):
        runtime = _make_runtime()
        with patch(
            "backend.algo.live.runtime.budget_reserve",
            new_callable=AsyncMock,
        ) as reserve_mock, patch(
            "backend.algo.live.runtime.budget_transition",
            new_callable=AsyncMock,
        ) as transition_mock:
            reserve_mock.return_value = uuid4()

            await runtime._apply_gtt_triggered_sell_fill(
                ticker="HSCL.NS", fill_price=673.55, qty=4,
            )

        reserve_mock.assert_awaited_once()
        transition_mock.assert_awaited_once()
        assert runtime._positions.open_positions().get("HSCL.NS") is None


class TestPieceAReleasesBudgetOnRatchetPoll:
    """_ratchet_all_gtts is sync (runs via asyncio.to_thread in
    production) and dispatches the budget release via
    run_coroutine_threadsafe onto self._loop. Set self._loop to the
    test's own running loop and drive _ratchet_all_gtts from a real
    worker thread (asyncio.to_thread) to exercise this exactly as
    production does -- calling it directly on the loop thread would
    hit the deadlock-avoidance skip branch instead.
    """

    @pytest.mark.asyncio
    async def test_gtt_triggered_exit_releases_budget(self):
        import asyncio as _asyncio
        from datetime import date as _date

        from backend.algo.backtest.types import Fill as _Fill

        runtime = _make_runtime()
        runtime._loop = _asyncio.get_running_loop()

        # A real open position, as if opened by an earlier BUY fill --
        # _ratchet_all_gtts falls back to the position tracker's qty
        # when Kite's GTT order definition doesn't expose it.
        runtime._positions.apply_fill(_Fill(
            intent_id=uuid4(), ticker="HSCL.NS", side="BUY", qty=4,
            fill_price=Decimal("642.6"), fill_date=_date.today(),
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

            await _asyncio.to_thread(runtime._ratchet_all_gtts)

        reserve_mock.assert_awaited_once()
        _, reserve_kwargs = reserve_mock.call_args
        assert reserve_kwargs["ticker"] == "HSCL.NS"
        assert reserve_kwargs["side"] == "SELL"
        transition_mock.assert_awaited_once()
