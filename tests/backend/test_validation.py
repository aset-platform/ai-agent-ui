"""Tests for backend.validation module."""

import sys
from pathlib import Path

# Ensure backend/ is on sys.path for direct imports
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "backend")
)

from validation import (  # noqa: E402
    _MAX_CHAT_MESSAGE,
    _MAX_SEARCH_QUERY,
    _MAX_TICKERS_PER_BATCH,
    validate_search_query,
    validate_ticker,
    validate_ticker_batch,
)


# ── validate_ticker ──────────────────────────────────────


class TestValidateTicker:
    """Verify ticker symbol validation."""

    def test_valid_us_ticker(self):
        """Standard US ticker passes."""
        assert validate_ticker("AAPL") is None

    def test_valid_indian_ticker(self):
        """NSE ticker with dot passes."""
        assert validate_ticker("RELIANCE.NS") is None

    def test_valid_bse_ticker(self):
        """BSE ticker with dot passes."""
        assert validate_ticker("TCS.BO") is None

    def test_valid_index(self):
        """Index with caret passes."""
        assert validate_ticker("^GSPC") is None

    def test_valid_with_hyphen(self):
        """Ticker with hyphen passes (e.g. BRK-B)."""
        assert validate_ticker("BRK-B") is None

    def test_empty_string_fails(self):
        """Empty ticker is rejected."""
        err = validate_ticker("")
        assert err is not None
        assert "required" in err.lower()

    def test_whitespace_only_fails(self):
        """Whitespace-only ticker is rejected."""
        err = validate_ticker("   ")
        assert err is not None

    def test_too_long_fails(self):
        """Ticker longer than 15 chars is rejected."""
        err = validate_ticker("A" * 16)
        assert err is not None
        assert "Invalid" in err

    def test_special_chars_rejected(self):
        """Tickers with SQL/XSS chars are rejected."""
        for bad in ["AAPL;DROP", "'; --", "<script>"]:
            err = validate_ticker(bad)
            assert err is not None, f"{bad!r} should fail"

    def test_spaces_rejected(self):
        """Ticker with internal spaces is rejected."""
        err = validate_ticker("AA PL")
        assert err is not None


# ── validate_search_query ────────────────────────────────


class TestValidateSearchQuery:
    """Verify search query validation."""

    def test_valid_query(self):
        """Normal query passes."""
        assert validate_search_query("AAPL earnings") is None

    def test_empty_query_fails(self):
        """Empty query is rejected."""
        err = validate_search_query("")
        assert err is not None
        assert "required" in err.lower()

    def test_too_long_query_fails(self):
        """Query over max length is rejected."""
        err = validate_search_query("x" * (_MAX_SEARCH_QUERY + 1))
        assert err is not None
        assert "too long" in err.lower()

    def test_max_length_query_passes(self):
        """Query at exactly max length passes."""
        assert validate_search_query(
            "x" * _MAX_SEARCH_QUERY
        ) is None


# ── validate_ticker_batch ────────────────────────────────


class TestValidateTickerBatch:
    """Verify batch ticker validation."""

    def test_valid_batch(self):
        """Small comma-separated list passes."""
        assert validate_ticker_batch("AAPL,TSLA,MSFT") is None

    def test_empty_batch_fails(self):
        """Empty string is rejected."""
        err = validate_ticker_batch("")
        assert err is not None

    def test_too_many_tickers_fails(self):
        """Batch over limit is rejected."""
        big = ",".join(
            f"T{i}" for i in range(_MAX_TICKERS_PER_BATCH + 1)
        )
        err = validate_ticker_batch(big)
        assert err is not None
        assert "Too many" in err

    def test_invalid_ticker_in_batch_fails(self):
        """One invalid ticker rejects whole batch."""
        err = validate_ticker_batch("AAPL,<bad>,MSFT")
        assert err is not None
        assert "Invalid" in err

    def test_exact_limit_passes(self):
        """Batch at exactly max count passes."""
        exact = ",".join(
            f"T{i}" for i in range(_MAX_TICKERS_PER_BATCH)
        )
        assert validate_ticker_batch(exact) is None
