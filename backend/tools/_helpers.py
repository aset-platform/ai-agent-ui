"""Shared currency helpers for backend tool modules.

Provides :func:`_currency_symbol` and :func:`_load_currency`, which were
previously duplicated across ``_stock_shared``, ``_analysis_shared``, and
``_forecast_shared``.  Importing from here ensures a single definition and
a module-level TTL cache for ``_load_currency``.

Usage::

    from tools._helpers import _currency_symbol, _load_currency
"""

import logging
import time

# Module-level logger; kept at module scope as a conventional singleton.
_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level TTL cache for _load_currency (Fix #5)
# Keys: ticker string  Values: (currency_code, expiry_timestamp)
# ---------------------------------------------------------------------------
_CURRENCY_CACHE: dict = {}
_CACHE_TTL_SECONDS: int = 300  # 5 minutes


def _currency_symbol(code: str) -> str:
    """Return the display symbol for a 3-letter ISO currency code.

    Args:
        code: ISO 4217 currency code, e.g. ``"USD"`` or ``"INR"``.

    Returns:
        The currency symbol string, e.g. ``"$"`` or ``"₹"``.
    """
    return {
        "USD": "$",
        "INR": "₹",
        "GBP": "£",
        "EUR": "€",
        "JPY": "¥",
        "CNY": "¥",
        "AUD": "A$",
        "CAD": "CA$",
        "HKD": "HK$",
        "SGD": "S$",
    }.get((code or "USD").upper(), code or "$")


def _load_currency(ticker: str, metadata_dir=None) -> str:
    """Read the ISO currency code for *ticker* from Iceberg company_info.

    Results are cached for :data:`_CACHE_TTL_SECONDS` seconds at module level
    to avoid repeated Iceberg scans when the same ticker is queried multiple
    times within a single request or agent turn.

    Args:
        ticker: Stock ticker symbol (already uppercased).
        metadata_dir: Unused; kept for backward compatibility with existing
            call sites. Ignored.

    Returns:
        ISO currency code string.  Falls back to ``"USD"``.
    """
    now = time.monotonic()
    cached = _CURRENCY_CACHE.get(ticker)
    if cached is not None:
        code, expiry = cached
        if now < expiry:
            return code
        # expired — remove stale entry
        del _CURRENCY_CACHE[ticker]

    code = "USD"
    try:
        from tools._stock_shared import _get_repo

        repo = _get_repo()
        if repo is not None:
            code = repo.get_currency(ticker)
    except Exception as exc:
        _logger.warning("Currency lookup failed for %s: %s", ticker, exc)

    _CURRENCY_CACHE[ticker] = (code, now + _CACHE_TTL_SECONDS)
    return code
