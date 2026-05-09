"""_derive_our_ticker — Kite tradingsymbol → our internal ticker."""
from __future__ import annotations

from backend.algo.instruments.loader import _derive_our_ticker


def test_nse_equity_gets_ns_suffix():
    assert _derive_our_ticker({
        "segment": "NSE", "exchange": "NSE",
        "tradingsymbol": "RELIANCE",
    }) == "RELIANCE.NS"


def test_bse_equity_gets_bo_suffix():
    assert _derive_our_ticker({
        "segment": "BSE", "exchange": "BSE",
        "tradingsymbol": "RELIANCE",
    }) == "RELIANCE.BO"


def test_nfo_returns_none():
    """NSE futures + options use a different symbol shape; skip."""
    assert _derive_our_ticker({
        "segment": "NFO-FUT", "exchange": "NFO",
        "tradingsymbol": "RELIANCE25FEBFUT",
    }) is None


def test_mcx_returns_none():
    assert _derive_our_ticker({
        "segment": "MCX-FUT", "exchange": "MCX",
        "tradingsymbol": "GOLDM25FEB",
    }) is None


def test_currency_returns_none():
    assert _derive_our_ticker({
        "segment": "CDS-FUT", "exchange": "CDS",
        "tradingsymbol": "USDINR25FEB",
    }) is None


def test_missing_segment_returns_none():
    assert _derive_our_ticker({
        "exchange": "NSE", "tradingsymbol": "X",
    }) is None


def test_missing_tradingsymbol_returns_none():
    assert _derive_our_ticker({
        "segment": "NSE", "exchange": "NSE",
    }) is None


def test_indices_with_segment_indices_skipped():
    """NSE indices like NIFTY 50 use segment='INDICES' — skip."""
    assert _derive_our_ticker({
        "segment": "INDICES", "exchange": "NSE",
        "tradingsymbol": "NIFTY 50",
    }) is None
