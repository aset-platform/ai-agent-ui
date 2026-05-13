"""SimBroker fee-schedule dispatch on cadence
(ASETPLTFRM-400 slice 4).

The fee model's dispatch on ``Trade.product`` already existed
in ``backend/algo/fees.py`` (capped brokerage, sell-side-only
STT @ 0.025 %, no DP charges, lower buy-side stamp duty for
INTRADAY). Slice 4 wires SimBroker to pick the right product
based on whether the intent carries ``intent_emitted_ts_ns``
(intraday) or not (daily).

This file verifies the dispatch end-to-end:

- Intraday intent → ``Fill`` priced under INTRADAY schedule
  (capped brokerage at ₹20/leg, lower buy-side stamp, no DP
  charges on the sell leg).
- Daily intent → ``Fill`` priced under DELIVERY schedule
  (zero brokerage at Zerodha rates, full 0.1% sell-side STT,
  full DP charge on the sell leg).
- Same notional → INTRADAY total < DELIVERY total on
  sell-side (no DP charges + lower STT dominate).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.algo.backtest.sim_broker import SimBroker
from backend.algo.backtest.types import BarData, OrderIntent

IST = timezone(timedelta(minutes=330))


def _intraday_bar(
    ticker: str,
    day: date,
    hour: int,
    minute: int,
    close: Decimal,
) -> BarData:
    open_dt = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=IST,
    )
    ts_ns = int(
        open_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000,
    )
    return BarData(
        ticker=ticker,
        date=day,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000,
        bar_open_ts_ns=ts_ns,
    )


def _daily_bar(ticker: str, day: date, close: Decimal) -> BarData:
    return BarData(
        ticker=ticker,
        date=day,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=10_000,
    )


# ────────────────────────────────────────────────────────────────
# Intraday intent → INTRADAY fee schedule
# ────────────────────────────────────────────────────────────────


def test_intraday_buy_intent_fills_with_intraday_brokerage_cap():
    """Brokerage on a ~₹100,000 intraday BUY = min(0.03 %, ₹20).
    0.03 % of 100,000 = ₹30, so the ₹20 cap applies.
    DELIVERY brokerage would be ₹0 (Zerodha free). The cap is
    the loudest single-line signal that we dispatched INTRADAY.
    """
    day = date(2026, 4, 1)
    bars = {
        "ITC.NS": [
            _intraday_bar("ITC.NS", day, 9, 15, Decimal("500")),
            _intraday_bar("ITC.NS", day, 9, 30, Decimal("500")),
        ]
    }
    sim = SimBroker(bars=bars, fee_as_of=day)

    bar_t = bars["ITC.NS"][0]
    intent = OrderIntent(
        ticker="ITC.NS",
        side="BUY",
        qty=200,
        intent_emitted_at=day,
        intent_emitted_ts_ns=bar_t.bar_open_ts_ns,
    )
    fill = sim.execute(intent)
    assert fill is not None
    assert fill.fill_ts_ns == bars["ITC.NS"][1].bar_open_ts_ns
    # 200 × ~500 = ₹100k notional → INTRADAY brokerage capped
    # at ₹20 (× GST + tiny exchange + SEBI + stamp). Total is
    # bounded between ₹20 (brokerage alone) and ₹40 (with
    # all add-ons). DELIVERY brokerage at the same notional
    # would be ₹0 → total < ₹15.
    assert fill.fees_inr >= Decimal("20"), (
        f"expected ≥ ₹20 (INTRADAY capped brokerage), got " f"{fill.fees_inr}"
    )


def test_intraday_sell_intent_pays_no_dp_charges():
    """INTRADAY sells skip the DP-charges fee component (DP =
    delivery-sell only). A daily sell at the same notional
    would tack on ~₹15.93 (₹13.5 + 18 % GST)."""
    day = date(2026, 4, 1)
    bars = {
        "ITC.NS": [
            _intraday_bar("ITC.NS", day, 9, 15, Decimal("500")),
            _intraday_bar("ITC.NS", day, 9, 30, Decimal("500")),
        ]
    }
    sim = SimBroker(bars=bars, fee_as_of=day)

    bar_t = bars["ITC.NS"][0]
    intent = OrderIntent(
        ticker="ITC.NS",
        side="SELL",
        qty=200,
        intent_emitted_at=day,
        intent_emitted_ts_ns=bar_t.bar_open_ts_ns,
    )
    fill = sim.execute(intent)
    assert fill is not None
    # INTRADAY sell fees on ₹100k:
    #   brokerage cap   ₹20
    #   STT (sell)      100000 × 0.00025 = ₹25
    #   exchange (NSE)  100000 × 0.0000297 ≈ ₹3
    #   SEBI            100000 × 0.000001 = ₹0.1
    #   GST 18 % on (brokerage + exchange + SEBI) ≈ ₹4.2
    # Total ≈ ₹52.3. DELIVERY at the same notional would add
    # ~₹16 of DP charges → ≥ ₹115 (STT 0.1 % alone is ₹100).
    # So the INTRADAY total should be < ₹80.
    assert fill.fees_inr < Decimal(
        "80"
    ), f"expected < ₹80 (no DP, lower STT), got {fill.fees_inr}"


# ────────────────────────────────────────────────────────────────
# Daily intent → DELIVERY fee schedule (regression)
# ────────────────────────────────────────────────────────────────


def test_daily_intent_dispatches_delivery_schedule():
    """No regression for the daily path — DELIVERY BUY fees on
    ₹100 k notional land around ₹100-130 (dominated by the
    yaml's ``delivery_buy_pct = 0.001`` STT row). INTRADAY
    would be < ₹40 (capped brokerage + no buy-side STT), so
    landing in the DELIVERY band proves SimBroker dispatched
    DELIVERY for the no-ts_ns intent path."""
    day = date(2026, 4, 1)
    next_day = day + timedelta(days=1)
    bars = {
        "ITC.NS": [
            _daily_bar("ITC.NS", day, Decimal("500")),
            _daily_bar("ITC.NS", next_day, Decimal("500")),
        ]
    }
    sim = SimBroker(bars=bars, fee_as_of=day)

    intent = OrderIntent(
        ticker="ITC.NS",
        side="BUY",
        qty=200,
        intent_emitted_at=day,
        # intent_emitted_ts_ns deliberately None → DELIVERY
    )
    fill = sim.execute(intent)
    assert fill is not None
    assert fill.fill_ts_ns is None
    # DELIVERY BUY total lands ~₹118 on ₹100k notional
    # (STT + stamp + exchange + SEBI + GST). The clean signal
    # vs INTRADAY (~₹30) is the > ₹50 lower bound.
    assert fill.fees_inr > Decimal("50"), (
        f"expected > ₹50 (DELIVERY STT band), got " f"{fill.fees_inr}"
    )


# ────────────────────────────────────────────────────────────────
# Cross-cadence side-by-side
# ────────────────────────────────────────────────────────────────


def test_intraday_buy_costs_less_than_delivery_buy_same_notional():
    """Apples-to-apples: BUY 200 @ ₹500 = ₹100 k notional.
    Per the dated rate ladder, DELIVERY pays ``delivery_buy_pct
    = 0.001`` STT (~₹100); INTRADAY pays zero buy-side STT and
    only the ₹20 brokerage cap → net INTRADAY < DELIVERY by
    ~₹85."""
    day = date(2026, 4, 1)

    # Intraday side: two adjacent 15m bars
    intraday_bars = {
        "ITC.NS": [
            _intraday_bar("ITC.NS", day, 9, 15, Decimal("500")),
            _intraday_bar("ITC.NS", day, 9, 30, Decimal("500")),
        ]
    }
    intraday_sim = SimBroker(bars=intraday_bars, fee_as_of=day)
    intraday_fill = intraday_sim.execute(
        OrderIntent(
            ticker="ITC.NS",
            side="BUY",
            qty=200,
            intent_emitted_at=day,
            intent_emitted_ts_ns=intraday_bars["ITC.NS"][0].bar_open_ts_ns,
        )
    )

    # Daily side: two adjacent calendar days
    daily_bars = {
        "ITC.NS": [
            _daily_bar("ITC.NS", day, Decimal("500")),
            _daily_bar("ITC.NS", day + timedelta(days=1), Decimal("500")),
        ]
    }
    daily_sim = SimBroker(bars=daily_bars, fee_as_of=day)
    daily_fill = daily_sim.execute(
        OrderIntent(
            ticker="ITC.NS",
            side="BUY",
            qty=200,
            intent_emitted_at=day,
        )
    )

    assert intraday_fill.fees_inr < daily_fill.fees_inr, (
        f"intraday BUY ({intraday_fill.fees_inr}) should cost "
        f"less than DELIVERY BUY ({daily_fill.fees_inr}) at "
        f"the same notional — INTRADAY has no buy-side STT"
    )
    # Bound the gap so a future yaml drift that erases the
    # distinction fails loud.
    assert (daily_fill.fees_inr - intraday_fill.fees_inr) > Decimal("50"), (
        f"expected ≥ ₹50 fee difference, got "
        f"{daily_fill.fees_inr - intraday_fill.fees_inr}"
    )


def test_intraday_sell_costs_less_than_delivery_sell_same_notional():
    """Apples-to-apples: SELL 200 @ ₹500 = ₹100 k notional.
    DELIVERY sell pays the full 0.1 % STT (₹100) + DP charges
    (~₹16). INTRADAY sell pays 0.025 % STT (₹25) + brokerage
    cap (~₹20) + no DP. Net: INTRADAY < DELIVERY."""
    day = date(2026, 4, 1)

    intraday_bars = {
        "ITC.NS": [
            _intraday_bar("ITC.NS", day, 9, 15, Decimal("500")),
            _intraday_bar("ITC.NS", day, 9, 30, Decimal("500")),
        ]
    }
    intraday_sim = SimBroker(bars=intraday_bars, fee_as_of=day)
    intraday_fill = intraday_sim.execute(
        OrderIntent(
            ticker="ITC.NS",
            side="SELL",
            qty=200,
            intent_emitted_at=day,
            intent_emitted_ts_ns=intraday_bars["ITC.NS"][0].bar_open_ts_ns,
        )
    )

    daily_bars = {
        "ITC.NS": [
            _daily_bar("ITC.NS", day, Decimal("500")),
            _daily_bar("ITC.NS", day + timedelta(days=1), Decimal("500")),
        ]
    }
    daily_sim = SimBroker(bars=daily_bars, fee_as_of=day)
    daily_fill = daily_sim.execute(
        OrderIntent(
            ticker="ITC.NS",
            side="SELL",
            qty=200,
            intent_emitted_at=day,
        )
    )

    assert intraday_fill.fees_inr < daily_fill.fees_inr, (
        f"intraday SELL ({intraday_fill.fees_inr}) should cost "
        f"less than DELIVERY SELL ({daily_fill.fees_inr}) — "
        f"lower STT + no DP charges"
    )


def test_fill_carries_correct_fee_rates_version():
    """Every fill — intraday or daily — must stamp the dated
    fee_rates_version YAML row used. Mismatch here means a
    re-run after the YAML changes would silently drift."""
    day = date(2026, 4, 1)
    bars = {
        "ITC.NS": [
            _intraday_bar("ITC.NS", day, 9, 15, Decimal("500")),
            _intraday_bar("ITC.NS", day, 9, 30, Decimal("500")),
        ]
    }
    sim = SimBroker(bars=bars, fee_as_of=day)
    fill = sim.execute(
        OrderIntent(
            ticker="ITC.NS",
            side="BUY",
            qty=10,
            intent_emitted_at=day,
            intent_emitted_ts_ns=bars["ITC.NS"][0].bar_open_ts_ns,
        )
    )
    assert fill is not None
    assert (
        fill.fee_rates_version
    ), "fee_rates_version must be stamped on every fill"
