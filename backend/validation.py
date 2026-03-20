"""Shared input-validation helpers for backend tools and endpoints.

Provides reusable validators for ticker symbols, search queries,
and chat messages at the system boundary.  Each function returns
``None`` on success or a short error string on failure.

.. note::
    Uses ``from __future__ import annotations`` for Python 3.9
    compatibility with PEP 604 union syntax.

Example::

    from validation import validate_ticker

    err = validate_ticker(raw)
    if err:
        return f"Error: {err}"
    ticker = raw.upper().strip()
"""

from __future__ import annotations

import re

_TICKER_RE = re.compile(r"^[A-Za-z0-9^.\-]{1,15}$")

_MAX_CHAT_MESSAGE = 10_000
_MAX_SEARCH_QUERY = 500
_MAX_TICKERS_PER_BATCH = 50


def validate_ticker(ticker: str) -> str | None:
    """Return error string if *ticker* is invalid, else ``None``.

    Allows 1\u201315 characters: letters, digits, ``^``, ``.``,
    ``-`` (covers NYSE, NSE, BSE, indices like ``^GSPC``).

    Args:
        ticker: Raw ticker string from user input.

    Returns:
        Error message or ``None`` if valid.
    """
    if not ticker or not ticker.strip():
        return "Ticker symbol is required."
    if not _TICKER_RE.match(ticker.strip()):
        return (
            f"Invalid ticker '{ticker[:20]}'. "
            "Use 1\u201315 alphanumeric characters "
            "(e.g. AAPL, RELIANCE.NS)."
        )
    return None


def validate_search_query(query: str) -> str | None:
    """Return error string if *query* is too long or empty.

    Args:
        query: Raw search query string.

    Returns:
        Error message or ``None`` if valid.
    """
    if not query or not query.strip():
        return "Search query is required."
    if len(query) > _MAX_SEARCH_QUERY:
        return (
            f"Search query too long " f"(max {_MAX_SEARCH_QUERY} characters)."
        )
    return None


def validate_ticker_batch(raw: str) -> str | None:
    """Return error if comma-separated ticker list is too large.

    Args:
        raw: Comma-separated ticker string.

    Returns:
        Error message or ``None`` if valid.
    """
    parts = [t.strip() for t in raw.split(",") if t.strip()]
    if not parts:
        return "At least one ticker is required."
    if len(parts) > _MAX_TICKERS_PER_BATCH:
        return (
            f"Too many tickers ({len(parts)}). "
            f"Maximum is {_MAX_TICKERS_PER_BATCH} per call."
        )
    for t in parts:
        err = validate_ticker(t)
        if err:
            return err
    return None
