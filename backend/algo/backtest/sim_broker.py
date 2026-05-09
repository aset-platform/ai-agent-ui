"""Fee-aware backtest broker. Fills BUY/SELL intents at the
NEXT bar's open price (T+1), never at the same bar's close —
this is the single most important look-ahead guard in the
engine.

Per epic spec § 6: every fill stamps the IndianFeeModel
``fee_rates_version`` so re-runs after a YAML rate change
don't silently drift.
"""
from __future__ import annotations

import logging
from datetime import date

from backend.algo.backtest.types import BarData, Fill, OrderIntent
from backend.algo.fees import IndianFeeModel, Trade

_logger = logging.getLogger(__name__)


class NoBarAvailableError(KeyError):
    """The intent's ticker has no bars in the loaded window."""


class SimBroker:
    """Stateless executor of OrderIntents against a pre-loaded
    bar dict. Construct once per backtest run.
    """

    def __init__(
        self,
        *,
        bars: dict[str, list[BarData]],
        fee_as_of: date,
    ) -> None:
        self._bars = bars
        self._fees = IndianFeeModel(as_of=fee_as_of)
        # Pre-compute date -> index lookup per ticker for O(1)
        # T+1 resolution.
        self._index: dict[str, dict[date, int]] = {
            t: {b.date: i for i, b in enumerate(blist)}
            for t, blist in bars.items()
        }

    def execute(self, intent: OrderIntent) -> Fill | None:
        """Return a Fill at T+1 open, or None if no next bar exists.

        Raises NoBarAvailableError if the ticker isn't in the
        loaded window (a real-world ingestion gap; runner should
        log + skip).
        """
        if intent.ticker not in self._bars:
            raise NoBarAvailableError(intent.ticker)

        idx = self._index[intent.ticker].get(intent.intent_emitted_at)
        if idx is None:
            # Intent emitted on a non-trading day for this ticker —
            # walk forward to the first bar at-or-after.
            future = [
                i for d, i in self._index[intent.ticker].items()
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
        # Compute fees on the executed leg.
        product = "DELIVERY"  # v1 only
        exchange = "NSE"      # v1 only
        breakdown = self._fees.compute(
            Trade(
                symbol=intent.ticker,
                exchange=exchange,
                side=intent.side,
                product=product,
                qty=intent.qty,
                price=next_bar.open,
            ),
        )
        return Fill(
            intent_id=intent.intent_id,
            ticker=intent.ticker,
            side=intent.side,
            qty=intent.qty,
            fill_price=next_bar.open,
            fill_date=next_bar.date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
        )
