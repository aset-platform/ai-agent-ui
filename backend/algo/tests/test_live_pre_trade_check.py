"""Tests for pre_trade_check — all 9 cap layers.

Each of the 9 caps MUST have a breach test (reject) and a
pass test (accept / scale).  That is 18 cases minimum.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.live.safety import (
    LiveRejectReason,
    pre_trade_check,
)
from backend.algo.paper.types import AccountState, RejectReason, Signal


def _signal(
    ticker="RELIANCE.NS",
    side="BUY",
    qty=10,
):
    return Signal(
        strategy_id=uuid4(),
        user_id=uuid4(),
        ticker=ticker,
        side=side,
        qty=qty,
        emitted_at_ns=0,
    )


def _account(
    kill_switch=False,
    daily_loss=Decimal("0"),
    open_positions=None,
    equity=Decimal("100000"),
):
    return AccountState(
        user_id=uuid4(),
        day_date=__import__("datetime").date.today(),
        initial_capital_inr=equity,
        current_equity_inr=equity,
        daily_realised_pnl_inr=-daily_loss,
        daily_unrealised_pnl_inr=Decimal("0"),
        open_positions=open_positions or {},
        open_position_count=len(open_positions or {}),
        kill_switch_active=kill_switch,
    )


def _caps(
    max_inr=Decimal("100000"),
    max_orders=50,
    allowed_tickers=None,
):
    if allowed_tickers is None:
        allowed_tickers = ["RELIANCE.NS", "TCS.NS"]
    return {
        "max_inr": max_inr,
        "max_orders_per_day": max_orders,
        "allowed_tickers": allowed_tickers,
    }


def _day_state(cum_inr=Decimal("0"), orders=0):
    return {
        "cumulative_inr_today": cum_inr,
        "orders_count_today": orders,
    }


def _risk(
    max_qty=0,
    max_loss_pct=0,
    max_open=0,
    max_conc=0,
    max_exp=0,
):
    return {
        "per_trade": {"max_qty": max_qty},
        "daily": {
            "max_loss_pct": max_loss_pct,
            "max_open_positions": max_open,
        },
        "portfolio": {
            "max_concentration_pct": max_conc,
            "max_exposure_pct": max_exp,
        },
    }


# ----------------------------------------------------------------
# Cap 1: Kill switch
# ----------------------------------------------------------------

class TestCap1KillSwitch:
    def test_kill_switch_armed_rejects(self):
        d = pre_trade_check(
            signal=_signal(),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(kill_switch=True),
            strategy_risk=_risk(),
            last_price=Decimal("2000"),
        )
        assert d.outcome == "reject"
        assert d.reason == RejectReason.KILL_SWITCH

    def test_kill_switch_disarmed_passes_this_cap(self):
        d = pre_trade_check(
            signal=_signal(),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(kill_switch=False),
            strategy_risk=_risk(),
            last_price=Decimal("2000"),
        )
        # Not kill-switch rejected; may pass or fail other caps
        assert d.reason != RejectReason.KILL_SWITCH


# ----------------------------------------------------------------
# Cap 2: Allowed tickers
# ----------------------------------------------------------------

class TestCap2AllowedTickers:
    def test_ticker_not_in_allow_list_rejects(self):
        d = pre_trade_check(
            signal=_signal(ticker="WIPRO.NS"),
            caps=_caps(allowed_tickers=["RELIANCE.NS"]),
            day_state=_day_state(),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("500"),
        )
        assert d.outcome == "reject"
        assert d.reason.value == LiveRejectReason.LIVE_TICKER_NOT_ALLOWED

    def test_empty_allow_list_rejects_everything(self):
        d = pre_trade_check(
            signal=_signal(ticker="RELIANCE.NS"),
            caps=_caps(allowed_tickers=[]),
            day_state=_day_state(),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("2000"),
        )
        assert d.outcome == "reject"
        assert d.reason.value == LiveRejectReason.LIVE_TICKER_NOT_ALLOWED

    def test_ticker_in_allow_list_passes_this_cap(self):
        d = pre_trade_check(
            signal=_signal(ticker="RELIANCE.NS"),
            caps=_caps(allowed_tickers=["RELIANCE.NS"]),
            day_state=_day_state(),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("2000"),
        )
        assert d.reason is None or (
            d.reason.value != LiveRejectReason.LIVE_TICKER_NOT_ALLOWED
        )


# ----------------------------------------------------------------
# Cap 3: max_orders_per_day
# ----------------------------------------------------------------

class TestCap3MaxOrdersPerDay:
    def test_at_limit_rejects(self):
        d = pre_trade_check(
            signal=_signal(),
            caps=_caps(max_orders=5),
            day_state=_day_state(orders=5),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("100"),
        )
        assert d.outcome == "reject"
        assert (
            d.reason.value == LiveRejectReason.LIVE_ORDERS_PER_DAY_CAP
        )

    def test_below_limit_passes_this_cap(self):
        d = pre_trade_check(
            signal=_signal(),
            caps=_caps(max_orders=10),
            day_state=_day_state(orders=3),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("100"),
        )
        assert d.reason is None or (
            d.reason.value != LiveRejectReason.LIVE_ORDERS_PER_DAY_CAP
        )

    def test_zero_cap_means_unlimited(self):
        # max_orders=0 means no cap (same as PaperRuntime semantics)
        d = pre_trade_check(
            signal=_signal(),
            caps=_caps(max_orders=0),
            day_state=_day_state(orders=9999),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("100"),
        )
        assert d.reason is None or (
            d.reason.value != LiveRejectReason.LIVE_ORDERS_PER_DAY_CAP
        )


# ----------------------------------------------------------------
# Cap 4: max_inr
# ----------------------------------------------------------------

class TestCap4MaxInr:
    def test_order_exceeds_remaining_inr_rejects(self):
        # max_inr=10000, already used 9500, order=1000 > headroom=500
        d = pre_trade_check(
            signal=_signal(qty=10),  # 10 * 200 = 2000
            caps=_caps(max_inr=Decimal("10000")),
            day_state=_day_state(cum_inr=Decimal("9500")),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("200"),
        )
        assert d.outcome == "reject"
        assert d.reason.value == LiveRejectReason.LIVE_INR_CAP

    def test_order_within_headroom_passes(self):
        # max_inr=10000, used=0, order=2000 < 10000
        d = pre_trade_check(
            signal=_signal(qty=10),
            caps=_caps(max_inr=Decimal("10000")),
            day_state=_day_state(cum_inr=Decimal("0")),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("200"),
        )
        assert d.reason is None or (
            d.reason.value != LiveRejectReason.LIVE_INR_CAP
        )

    def test_zero_cap_means_unlimited_inr(self):
        d = pre_trade_check(
            signal=_signal(qty=1000),
            caps=_caps(max_inr=Decimal("0")),
            day_state=_day_state(cum_inr=Decimal("0")),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("50000"),
        )
        assert d.reason is None or (
            d.reason.value != LiveRejectReason.LIVE_INR_CAP
        )


# ----------------------------------------------------------------
# Cap 5: per-trade max_qty
# ----------------------------------------------------------------

class TestCap5MaxQty:
    def test_qty_over_cap_rejects(self):
        d = pre_trade_check(
            signal=_signal(qty=101),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(),
            strategy_risk=_risk(max_qty=100),
            last_price=Decimal("100"),
        )
        assert d.outcome == "reject"
        assert d.reason == RejectReason.MAX_QTY

    def test_qty_at_cap_passes(self):
        d = pre_trade_check(
            signal=_signal(qty=100),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(),
            strategy_risk=_risk(max_qty=100),
            last_price=Decimal("100"),
        )
        assert d.reason != RejectReason.MAX_QTY


# ----------------------------------------------------------------
# Cap 6: daily max_loss_pct
# ----------------------------------------------------------------

class TestCap6DailyLossCap:
    def test_at_daily_loss_cap_rejects(self):
        # 5% of 100000 = 5000 loss cap; daily_loss=5000
        d = pre_trade_check(
            signal=_signal(),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(
                equity=Decimal("100000"),
                daily_loss=Decimal("5000"),
            ),
            strategy_risk=_risk(max_loss_pct=5),
            last_price=Decimal("100"),
        )
        assert d.outcome == "reject"
        assert d.reason == RejectReason.DAILY_LOSS_CAP

    def test_below_daily_loss_cap_passes(self):
        d = pre_trade_check(
            signal=_signal(),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(
                equity=Decimal("100000"),
                daily_loss=Decimal("100"),
            ),
            strategy_risk=_risk(max_loss_pct=5),
            last_price=Decimal("100"),
        )
        assert d.reason != RejectReason.DAILY_LOSS_CAP


# ----------------------------------------------------------------
# Cap 7: daily max_open_positions
# ----------------------------------------------------------------

class TestCap7MaxOpenPositions:
    def test_at_max_open_positions_rejects_new_buy(self):
        positions = {
            "RELIANCE.NS": 10,
            "TCS.NS": 5,
        }
        d = pre_trade_check(
            # Signal for a NEW ticker (INFY.NS not in positions)
            signal=_signal(ticker="INFY.NS"),
            caps=_caps(allowed_tickers=["INFY.NS", "RELIANCE.NS"]),
            day_state=_day_state(),
            account=_account(open_positions=positions),
            strategy_risk=_risk(max_open=2),
            last_price=Decimal("1500"),
        )
        assert d.outcome == "reject"
        assert d.reason == RejectReason.MAX_OPEN_POSITIONS

    def test_sell_not_blocked_by_open_position_cap(self):
        positions = {"RELIANCE.NS": 10, "TCS.NS": 5}
        d = pre_trade_check(
            signal=_signal(side="SELL"),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(open_positions=positions),
            strategy_risk=_risk(max_open=1),
            last_price=Decimal("2000"),
        )
        # SELL always skips portfolio caps → accept
        assert d.outcome == "accept"


# ----------------------------------------------------------------
# Cap 8: portfolio max_concentration_pct
# ----------------------------------------------------------------

class TestCap8ConcentrationCap:
    def test_concentration_breach_rejects(self):
        # 10 * 2000 = 20000 notional; equity=50000 → 40% > 30%
        d = pre_trade_check(
            signal=_signal(qty=10),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(equity=Decimal("50000")),
            strategy_risk=_risk(max_conc=30),
            last_price=Decimal("2000"),
        )
        assert d.outcome == "reject"
        assert d.reason == RejectReason.POSITION_CAP

    def test_within_concentration_passes(self):
        # 1 * 100 = 100 notional; equity=100000 → 0.1% < 30%
        d = pre_trade_check(
            signal=_signal(qty=1),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(equity=Decimal("100000")),
            strategy_risk=_risk(max_conc=30),
            last_price=Decimal("100"),
        )
        assert d.reason != RejectReason.POSITION_CAP


# ----------------------------------------------------------------
# Cap 9: portfolio max_exposure_pct (may scale)
# ----------------------------------------------------------------

class TestCap9ExposureCap:
    def test_exposure_breach_scales_down(self):
        # Equity=10000, cap=50% → cap_inr=5000.
        # Existing exposure=4000, requesting 2000 → over by 1000.
        # headroom=1000, last_price=100 → scaled_qty=10.
        d = pre_trade_check(
            signal=_signal(qty=20),  # 20 * 100 = 2000
            caps=_caps(),
            day_state=_day_state(),
            account=_account(
                equity=Decimal("10000"),
                open_positions={"TCS.NS": 40},  # 40*100=4000
            ),
            strategy_risk=_risk(max_exp=50),
            last_price=Decimal("100"),
        )
        assert d.outcome == "scale"
        assert d.adjusted_qty == 10

    def test_no_headroom_at_all_rejects(self):
        # Already at cap; no room for any new order
        d = pre_trade_check(
            signal=_signal(qty=1),
            caps=_caps(),
            day_state=_day_state(),
            account=_account(
                equity=Decimal("10000"),
                open_positions={"TCS.NS": 50},  # 50*100=5000=50%
            ),
            strategy_risk=_risk(max_exp=50),
            last_price=Decimal("100"),
        )
        assert d.outcome == "reject"
        assert d.reason == RejectReason.EXPOSURE_CAP


# ----------------------------------------------------------------
# Short-circuit order: allowed_tickers BEFORE inr cap
# ----------------------------------------------------------------

class TestShortCircuitOrdering:
    def test_unknown_ticker_rejected_before_inr_cap(self):
        """Cap 2 (allowed_tickers) fires before Cap 4 (max_inr)."""
        d = pre_trade_check(
            signal=_signal(ticker="UNKNOWN.NS"),
            caps=_caps(
                allowed_tickers=["RELIANCE.NS"],
                max_inr=Decimal("1"),  # would also reject
            ),
            day_state=_day_state(),
            account=_account(),
            strategy_risk=_risk(),
            last_price=Decimal("2000"),
        )
        assert d.reason.value == LiveRejectReason.LIVE_TICKER_NOT_ALLOWED

    def test_kill_switch_before_ticker_check(self):
        """Cap 1 (kill switch) fires before Cap 2 (allowed_tickers)."""
        d = pre_trade_check(
            signal=_signal(ticker="UNKNOWN.NS"),
            caps=_caps(allowed_tickers=[]),
            day_state=_day_state(),
            account=_account(kill_switch=True),
            strategy_risk=_risk(),
            last_price=Decimal("2000"),
        )
        assert d.reason == RejectReason.KILL_SWITCH


# ----------------------------------------------------------------
# Full-pass: all caps pass → accept
# ----------------------------------------------------------------

class TestFullPass:
    def test_all_caps_pass_returns_accept(self):
        d = pre_trade_check(
            signal=_signal(ticker="RELIANCE.NS", qty=5),
            caps=_caps(
                max_inr=Decimal("1000000"),
                max_orders=50,
                allowed_tickers=["RELIANCE.NS"],
            ),
            day_state=_day_state(
                cum_inr=Decimal("0"), orders=0,
            ),
            account=_account(
                kill_switch=False,
                equity=Decimal("1000000"),
            ),
            strategy_risk=_risk(
                max_qty=100,
                max_loss_pct=10,
                max_open=20,
                max_conc=80,
                max_exp=90,
            ),
            last_price=Decimal("2000"),
        )
        assert d.outcome == "accept"
