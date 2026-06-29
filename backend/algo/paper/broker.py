"""Paper broker — fills at the current tick's LTP, not next-bar
open like SimBroker. Stamps IndianFeeModel rates_version per
spec § 6.2.

A real Kite broker would place the order via
KiteAdapter.place_order(); v1 paper has no live order leg, so
fills are immediate and synthetic. Slice 8b's reconciliation
loop tests this with a fake-broker fixture.

Slippage
--------
``ALGO_PAPER_SLIPPAGE_BPS`` (int, default 0) applies a directional
price penalty to improve promotion-gate realism:
- BUY:  fill_price = last_price * (1 + bps/10_000)  — buyer pays up.
- SELL: fill_price = last_price * (1 - bps/10_000)  — seller receives
        less.
Fees are computed on the unslipped base price (``trigger_price`` when
set, else ``last_price``).
"""
from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.types import Fill
from backend.algo.fees import IndianFeeModel, Trade
from backend.algo.paper.types import Signal

_logger = logging.getLogger(__name__)

_PAISE = Decimal("0.01")


def _slipped_price(last_price: Decimal, side: str) -> Decimal:
    """Return fill_price after applying directional slippage.

    Reads ``ALGO_PAPER_SLIPPAGE_BPS`` at call time so tests can
    override via ``monkeypatch.setenv`` without patching the module.
    """
    try:
        bps = int(os.getenv("ALGO_PAPER_SLIPPAGE_BPS", "0"))
    except (TypeError, ValueError):
        bps = 0
    if bps <= 0:
        return last_price
    factor = Decimal(bps) / Decimal(10_000)
    if side == "BUY":
        return (last_price * (Decimal("1") + factor)).quantize(_PAISE)
    return (last_price * (Decimal("1") - factor)).quantize(_PAISE)


class PaperBroker:
    """Synchronous, pure-Python at-tick broker."""

    def __init__(
        self, *, fee_as_of: date, product: str = "DELIVERY"
    ) -> None:
        self._fees = IndianFeeModel(as_of=fee_as_of)
        self._product = product

    def execute(
        self,
        *,
        signal: Signal,
        last_price: Decimal,
        fill_date: date,
        trigger_price: Decimal | None = None,
    ) -> Fill:
        """Fill the signal with directional slippage.

        When ``trigger_price`` is set (stop/trailing exit, modelling the
        live GTT), the fill is based on the trigger; otherwise on
        ``last_price``. Fees are computed on the UNSLIPPED base price;
        ``fill_price`` includes the slippage penalty.
        """
        base = trigger_price if trigger_price is not None else last_price
        breakdown = self._fees.compute(
            Trade(
                symbol=signal.ticker,
                exchange="NSE",
                side=signal.side,
                product=self._product,
                qty=signal.qty,
                price=base,
            ),
        )
        fill_price = _slipped_price(base, signal.side)
        return Fill(
            intent_id=uuid4(),
            ticker=signal.ticker,
            side=signal.side,
            qty=signal.qty,
            fill_price=fill_price,
            fill_date=fill_date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
            exit_reason=signal.reason or "signal",
            trigger_price=trigger_price,
        )
