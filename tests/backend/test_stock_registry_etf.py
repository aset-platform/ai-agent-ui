"""Tests for NSE ETF classification in _stock_registry.

Verifies that _is_nse_etf() and _detect_ticker_type() correctly
classify NSE ETFs (via suffix and curated set) without mis-labelling
real equities as ETFs.
"""

from backend.tools._stock_registry import (
    _detect_ticker_type,
    _is_nse_etf,
)


def test_nse_etf_suffix_rules():
    assert _is_nse_etf("GILT5YBEES")  # BEES
    assert _is_nse_etf("NIFTYBEES")
    assert _is_nse_etf("CPSEETF")     # ETF
    assert _is_nse_etf("ALPHAETF")


def test_nse_etf_curated():
    for s in [
        "LIQUID",
        "MONQ50",
        "MOQUALITY",
        "HDFCGOLD",
        "ICICIB22",
        "SETFNIF50",
        "MASPTOP50",
    ]:
        assert _is_nse_etf(s), s


def test_real_stocks_not_etf():
    # contain ETF-ish substrings but are real stocks
    for s in [
        "SKYGOLD",
        "MOIL",
        "BHARATFORG",
        "DALBHARAT",
        "AXISBANK",
        "GROWW",
        "EVINDIA",
        "MOMENTUM",
    ]:
        assert not _is_nse_etf(s), s


def test_detect_ticker_type_nse():
    assert _detect_ticker_type("GILT5YBEES.NS") == "etf"
    assert _detect_ticker_type("MONQ50.NS") == "etf"
    assert _detect_ticker_type("SKYGOLD.NS") == "stock"
    assert _detect_ticker_type("MOIL.NS") == "stock"
    assert _detect_ticker_type("^NSEI") == "index"
