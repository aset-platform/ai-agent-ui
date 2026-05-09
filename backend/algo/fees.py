# backend/algo/fees.py
"""Indian equity fee model — Slice 1 of the Algo Trading epic.

Reads dated YAML fee rates and computes a per-trade
``FeeBreakdown``. Used by SimBroker (backtest + paper),
the Settings preview widget, and (in v2) the live order
ledger. Without this, backtests lie — see spec § 6.

References:
- Zerodha brokerage calculator: zerodha.com/brokerage-calculator
- Statutory fees (STT/CTT, GST, SEBI) updated annually.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

_RATES_PATH = Path(__file__).resolve().parent / "fee_rates.yaml"

Side = Literal["BUY", "SELL"]
Product = Literal["DELIVERY", "INTRADAY"]
Exchange = Literal["NSE", "BSE"]


class Trade(BaseModel):
    """A single leg of a trade — one row in the fill ledger."""

    symbol: str = Field(min_length=1, max_length=64)
    exchange: Exchange
    side: Side
    product: Product
    qty: int = Field(ge=0)
    price: Decimal = Field(ge=Decimal("0"))

    @field_validator("price")
    @classmethod
    def _validate_price(cls, v: Decimal) -> Decimal:
        # Pydantic v2 already rejects negative via ge=0; this is
        # belt-and-braces against subclass overrides.
        if v < 0:
            raise ValueError("price must be non-negative")
        return v


class FeeBreakdown(BaseModel):
    """Per-leg fee components in INR, rounded to 2dp.

    All Decimal to avoid float drift in repeated backtests.
    ``rates_version`` is the ``effective_from`` of the rate
    row used — pinned on every ``order_filled`` event so a
    re-run after a rate change won't silently drift.
    """

    brokerage_inr: Decimal
    stt_inr: Decimal
    exchange_txn_inr: Decimal
    sebi_inr: Decimal
    stamp_duty_inr: Decimal
    gst_inr: Decimal
    dp_charges_inr: Decimal
    total_inr: Decimal
    rates_version: str


def _quantize(v: Decimal) -> Decimal:
    """Round half-up to 2dp INR — Zerodha calculator convention."""
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _load_rates_for(as_of: date) -> dict:
    """Pick the YAML row whose ``effective_from`` covers *as_of*."""
    raw = yaml.safe_load(_RATES_PATH.read_text(encoding="utf-8"))
    for row in raw:
        eff_from = date.fromisoformat(str(row["effective_from"]))
        eff_to = row.get("effective_to")
        if eff_to is None:
            if as_of >= eff_from:
                return row
        else:
            eff_to_d = date.fromisoformat(str(eff_to))
            if eff_from <= as_of <= eff_to_d:
                return row
    raise ValueError(f"No fee rates configured for {as_of.isoformat()}")


class IndianFeeModel:
    """Stateless fee calculator pinned to a specific YAML row.

    Construct with the date the trade settled on. All compute
    calls use the same row — no per-trade YAML reload.
    """

    def __init__(self, as_of: date):
        self._row = _load_rates_for(as_of)
        self.rates_version: str = str(self._row["effective_from"])

    def compute(self, trade: Trade) -> FeeBreakdown:
        rates = self._row
        notional = Decimal(trade.qty) * trade.price

        brokerage = self._brokerage(trade, rates, notional)
        stt = self._stt(trade, rates, notional)
        exch = self._exchange(trade, rates, notional)
        sebi = notional * Decimal(str(rates["sebi"]["pct"]))
        stamp = self._stamp(trade, rates, notional)
        gst = (brokerage + exch + sebi) * Decimal(str(rates["gst"]["pct"]))
        dp = self._dp(trade, rates)

        brokerage = _quantize(brokerage)
        stt = _quantize(stt)
        exch = _quantize(exch)
        sebi = _quantize(sebi)
        stamp = _quantize(stamp)
        gst = _quantize(gst)
        dp = _quantize(dp)
        total = _quantize(brokerage + stt + exch + sebi + stamp + gst + dp)

        return FeeBreakdown(
            brokerage_inr=brokerage,
            stt_inr=stt,
            exchange_txn_inr=exch,
            sebi_inr=sebi,
            stamp_duty_inr=stamp,
            gst_inr=gst,
            dp_charges_inr=dp,
            total_inr=total,
            rates_version=self.rates_version,
        )

    @staticmethod
    def _brokerage(t: Trade, rates: dict, notional: Decimal) -> Decimal:
        if t.product == "DELIVERY":
            return notional * Decimal(str(rates["brokerage"]["delivery_pct"]))
        # INTRADAY — pct or cap, whichever lower
        pct = notional * Decimal(str(rates["brokerage"]["intraday_pct"]))
        cap = Decimal(str(rates["brokerage"]["intraday_cap_inr"]))
        return min(pct, cap)

    @staticmethod
    def _stt(t: Trade, rates: dict, notional: Decimal) -> Decimal:
        stt = rates["stt"]
        if t.product == "DELIVERY":
            if t.side == "BUY":
                return notional * Decimal(str(stt["delivery_buy_pct"]))
            return notional * Decimal(str(stt["delivery_sell_pct"]))
        # INTRADAY — only sell leg pays STT
        if t.side == "SELL":
            return notional * Decimal(str(stt["intraday_sell_pct"]))
        return Decimal("0")

    @staticmethod
    def _exchange(t: Trade, rates: dict, notional: Decimal) -> Decimal:
        ex = rates["exchange_txn"]
        if t.exchange == "NSE":
            return notional * Decimal(str(ex["nse_pct"]))
        return notional * Decimal(str(ex["bse_pct"]))

    @staticmethod
    def _stamp(t: Trade, rates: dict, notional: Decimal) -> Decimal:
        # Stamp duty is buy-side only.
        if t.side != "BUY":
            return Decimal("0")
        sd = rates["stamp_duty"]
        if t.product == "DELIVERY":
            return notional * Decimal(str(sd["delivery_buy_pct"]))
        return notional * Decimal(str(sd["intraday_buy_pct"]))

    @staticmethod
    def _dp(t: Trade, rates: dict) -> Decimal:
        # DP charges = sell-side delivery only, flat per ISIN per day.
        if t.side != "SELL" or t.product != "DELIVERY":
            return Decimal("0")
        dp = rates["dp_charges"]
        base = Decimal(str(dp["delivery_sell_inr"]))
        gst_rate = Decimal(str(dp["delivery_sell_gst_pct"]))
        return base * (Decimal("1") + gst_rate)
