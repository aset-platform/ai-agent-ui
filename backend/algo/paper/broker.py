"""Paper broker — fills at the current tick's LTP, not next-bar
open like SimBroker. Stamps IndianFeeModel rates_version per
spec § 6.2.

A real Kite broker would place the order via
KiteAdapter.place_order(); v1 paper has no live order leg, so
fills are immediate and synthetic. Slice 8b's reconciliation
loop tests this with a fake-broker fixture.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.types import Fill
from backend.algo.fees import IndianFeeModel, Trade
from backend.algo.paper.types import Signal

_logger = logging.getLogger(__name__)


class PaperBroker:
    """Synchronous, pure-Python at-tick broker."""

    def __init__(self, *, fee_as_of: date) -> None:
        self._fees = IndianFeeModel(as_of=fee_as_of)

    def execute(
        self,
        *,
        signal: Signal,
        last_price: Decimal,
        fill_date: date,
    ) -> Fill:
        """Fill the signal immediately at ``last_price``."""
        breakdown = self._fees.compute(
            Trade(
                symbol=signal.ticker,
                exchange="NSE",
                side=signal.side,
                product="DELIVERY",
                qty=signal.qty,
                price=last_price,
            ),
        )
        return Fill(
            intent_id=uuid4(),
            ticker=signal.ticker,
            side=signal.side,
            qty=signal.qty,
            fill_price=last_price,
            fill_date=fill_date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
            exit_reason=signal.reason or "signal",
        )
