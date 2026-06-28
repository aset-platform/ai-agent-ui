"""Fee-aware backtest broker. Fills BUY/SELL intents at the
NEXT bar's open price (T+1), never at the same bar's close —
this is the single most important look-ahead guard in the
engine.

Per epic spec § 6: every fill stamps the IndianFeeModel
``fee_rates_version`` so re-runs after a YAML rate change
don't silently drift.

REGIME-7: ADTV-scaled slippage is applied to the next-bar open
BEFORE fee computation, so the booked fees reflect the actual
fill price (not the mid). When ``adtv_lookup`` is empty the
slippage falls back to the 5bps minimum on every leg.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal

from backend.algo.backtest.types import BarData, Fill, OrderIntent
from backend.algo.fees import IndianFeeModel, Trade

_logger = logging.getLogger(__name__)


# REGIME-7 — slippage model constants. Per research §10:
# ``max(5, 50 * order_value / ADTV) bps``. Below the floor every
# trade still pays at least 5bps; above the floor slippage scales
# linearly with order participation in the 60d ADTV.
SLIPPAGE_MIN_BPS = Decimal("5")
SLIPPAGE_IMPACT_BPS = Decimal("50")


def estimate_slippage_bps(
    order_value_inr: Decimal,
    ticker_adtv_inr: Decimal,
) -> Decimal:
    """Compute slippage in bps for a single executed leg.

    ``ticker_adtv_inr`` is the 60d average daily traded value in
    rupees. Missing / zero / NaN ADTV degrades safely to the 5bps
    minimum so we never raise on ingestion gaps.
    """
    if ticker_adtv_inr.is_nan() or ticker_adtv_inr <= 0:
        return SLIPPAGE_MIN_BPS
    impact = SLIPPAGE_IMPACT_BPS * (order_value_inr / ticker_adtv_inr)
    return max(SLIPPAGE_MIN_BPS, impact)


def _flat_slip(trigger: Decimal, side: str) -> Decimal:
    """Directional flat-bps slippage on a trigger price (live GTT)."""
    try:
        bps = int(os.getenv("ALGO_PAPER_SLIPPAGE_BPS", "0"))
    except (TypeError, ValueError):
        bps = 0
    if bps <= 0:
        return trigger
    factor = Decimal(bps) / Decimal("10000")
    if side == "BUY":
        return trigger * (Decimal("1") + factor)
    return trigger * (Decimal("1") - factor)


class NoBarAvailableError(KeyError):
    """The intent's ticker has no bars in the loaded window."""


class SimBroker:
    """Stateless executor of OrderIntents against a pre-loaded
    bar dict. Construct once per backtest run.

    ``adtv_lookup`` (REGIME-7, optional): ticker → 60d ADTV in INR.
    Missing tickers fall back to the 5bps slippage minimum.
    """

    def __init__(
        self,
        *,
        bars: dict[str, list[BarData]],
        fee_as_of: date,
        adtv_lookup: dict[str, Decimal] | None = None,
    ) -> None:
        self._bars = bars
        self._fees = IndianFeeModel(as_of=fee_as_of)
        self._adtv: dict[str, Decimal] = adtv_lookup or {}
        # Pre-compute date -> index lookup per ticker for O(1)
        # T+1 resolution on daily strategies. For intraday strategies
        # (multiple bars per date) this only records the LAST bar's
        # index per date, so intraday paths key off ``_ts_index``
        # below instead.
        self._index: dict[str, dict[date, int]] = {
            t: {b.date: i for i, b in enumerate(blist)}
            for t, blist in bars.items()
        }
        # ASETPLTFRM-400 slice 3 — bar_open_ts_ns → index for
        # intraday strategies. Empty on daily runs (where every
        # bar's ``bar_open_ts_ns`` is None).
        self._ts_index: dict[str, dict[int, int]] = {
            t: {
                b.bar_open_ts_ns: i
                for i, b in enumerate(blist)
                if b.bar_open_ts_ns is not None
            }
            for t, blist in bars.items()
        }

    def execute(self, intent: OrderIntent) -> Fill | None:
        """Return a Fill at T+1 open, or None if no next bar exists.

        Raises NoBarAvailableError if the ticker isn't in the
        loaded window (a real-world ingestion gap; runner should
        log + skip).

        REGIME-7: applies ADTV-scaled slippage to the next-bar
        open BEFORE fees are computed.
        """
        if intent.ticker not in self._bars:
            raise NoBarAvailableError(intent.ticker)

        if intent.trigger_price is not None:
            return self._execute_trigger_fill(intent)

        # ASETPLTFRM-400 slice 3 — when the intent carries
        # ``intent_emitted_ts_ns`` (intraday cadence), resolve T+1
        # to the next intraday BAR open rather than the next
        # calendar day's open. Daily intents keep the original
        # date-keyed path below.
        if intent.intent_emitted_ts_ns is not None:
            ts_idx = self._ts_index.get(intent.ticker, {}).get(
                intent.intent_emitted_ts_ns,
            )
            if ts_idx is None:
                future = [
                    i
                    for ns, i in self._ts_index.get(
                        intent.ticker,
                        {},
                    ).items()
                    if ns > intent.intent_emitted_ts_ns
                ]
                if not future:
                    return None
                next_idx = min(future)
            else:
                next_idx = ts_idx + 1
                if next_idx >= len(self._bars[intent.ticker]):
                    return None
        else:
            idx = self._index[intent.ticker].get(
                intent.intent_emitted_at,
            )
            if idx is None:
                # Intent emitted on a non-trading day for this
                # ticker — walk forward to the first bar
                # at-or-after.
                future = [
                    i
                    for d, i in self._index[intent.ticker].items()
                    if d > intent.intent_emitted_at
                ]
                if not future:
                    return None
                next_idx = min(future)
            else:
                next_idx = idx + 1
                if next_idx >= len(self._bars[intent.ticker]):
                    return None

        next_bar = self._bars[intent.ticker][next_idx]

        # REGIME-7 slippage — adjust the open price by bps before
        # fees so the booked fees reflect the actual fill price.
        adtv = self._adtv.get(intent.ticker, Decimal("NaN"))
        order_value = Decimal(intent.qty) * next_bar.open
        bps = estimate_slippage_bps(order_value, adtv)
        slip_factor = bps / Decimal("10000")
        if intent.side == "BUY":
            fill_price = next_bar.open * (Decimal("1") + slip_factor)
        else:
            fill_price = next_bar.open * (Decimal("1") - slip_factor)

        # Compute fees on the executed leg (uses fill_price, not
        # the bar open).
        # ASETPLTFRM-400 slice 4 — fee dispatch on cadence.
        # Intraday intents (those carrying ``intent_emitted_ts_ns``)
        # use the INTRADAY schedule: capped brokerage (₹20 / leg
        # max), sell-side-only STT @ 0.025 %, sell-side-only no
        # DP charges. Daily strategies keep the DELIVERY schedule.
        # An explicit ``intent.product`` (set by two-clock exits)
        # overrides the bar-grain inference so a CNC daily strategy's
        # intraday-detected exit still bills DELIVERY fees.
        product = intent.product or (
            "INTRADAY"
            if intent.intent_emitted_ts_ns is not None
            else "DELIVERY"
        )
        exchange = "NSE"  # v1 only
        breakdown = self._fees.compute(
            Trade(
                symbol=intent.ticker,
                exchange=exchange,
                side=intent.side,
                product=product,
                qty=intent.qty,
                price=fill_price,
            ),
        )
        return Fill(
            intent_id=intent.intent_id,
            ticker=intent.ticker,
            side=intent.side,
            qty=intent.qty,
            fill_price=fill_price,
            fill_date=next_bar.date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
            exit_reason=intent.exit_reason,
            fill_ts_ns=next_bar.bar_open_ts_ns,
        )

    def _execute_trigger_fill(
        self, intent: OrderIntent
    ) -> Fill | None:
        """Fill a stop/trailing exit on its own bar at the trigger.

        Resolves the CURRENT (emitted) bar rather than T+1.
        Fees are computed on the unslipped trigger price to mirror
        the live GTT execution model.
        """
        if intent.intent_emitted_ts_ns is not None:
            idx = self._ts_index.get(intent.ticker, {}).get(
                intent.intent_emitted_ts_ns
            )
        else:
            idx = self._index.get(intent.ticker, {}).get(
                intent.intent_emitted_at
            )
        if idx is None:
            return None
        bar = self._bars[intent.ticker][idx]
        trigger = intent.trigger_price  # type: ignore[assignment]
        fill_price = _flat_slip(trigger, intent.side)
        # Explicit ``intent.product`` (two-clock exits) overrides the
        # bar-grain inference — a CNC strategy's intraday trailing
        # exit must still bill DELIVERY fees.
        product = intent.product or (
            "INTRADAY"
            if intent.intent_emitted_ts_ns is not None
            else "DELIVERY"
        )
        breakdown = self._fees.compute(
            Trade(
                symbol=intent.ticker,
                exchange="NSE",
                side=intent.side,
                product=product,
                qty=intent.qty,
                price=trigger,  # fees on unslipped trigger
            ),
        )
        return Fill(
            intent_id=intent.intent_id,
            ticker=intent.ticker,
            side=intent.side,
            qty=intent.qty,
            fill_price=fill_price,
            fill_date=bar.date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
            exit_reason=intent.exit_reason,
            fill_ts_ns=bar.bar_open_ts_ns,
            trigger_price=trigger,
        )
