"""Pure helper utilities for the AI Stock Analysis Dashboard callbacks.

Contains input-validation constants and functions, market classification,
and currency helpers.  This module has no Dash imports so it can be used
freely from any sub-module without circular-import risk.

Example::

    from dashboard.callbacks.utils import _get_market, _check_input_safety
"""

import logging
import re
import time as _time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Currency cache — avoids opening JSON on every callback invocation
# ---------------------------------------------------------------------------
_CURRENCY_CACHE_DASH: dict = {}  # {ticker: (symbol_str, expiry_monotonic)}
_CURRENCY_TTL = 300  # seconds

# ---------------------------------------------------------------------------
# Input validation constants
# Module-level regex constants are intentionally kept here (not inside a class)
# because they are stateless compiled patterns shared across the module.
# ---------------------------------------------------------------------------

# _EMAIL_RE: compiled pattern for basic email validation
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# _UNSAFE_SQL: compiled pattern for common SQL injection sequences
_UNSAFE_SQL = re.compile(
    r"(--|/\*|\*/|';\s*|\";\s*|union\s+select"
    r"|drop\s+table|or\s+1\s*=\s*1"
    r"|or\s+'1'\s*=\s*'1')",
    re.IGNORECASE,
)

# _UNSAFE_XSS: compiled pattern for XSS-style URI schemes
_UNSAFE_XSS = re.compile(r"(javascript:|vbscript:|data:)", re.IGNORECASE)


def _is_valid_email(value: str) -> bool:
    """Return True if *value* looks like a valid email address.

    Args:
        value: String to check.

    Returns:
        ``True`` when the string matches a basic ``user@domain.tld`` pattern.
    """
    return bool(_EMAIL_RE.match(value))


def _check_input_safety(
    value: str, field: str, max_len: int = 200
) -> Optional[str]:
    """Return an error string if *value* is unsafe, else ``None``.

    Checks performed (in order): max length, HTML characters, null bytes,
    XSS-style URI schemes, and common SQL injection sequences.

    Args:
        value: The user-supplied string to validate.
        field: Human-readable field label used in error messages.
        max_len: Maximum allowed character length (default 200).

    Returns:
        An error message string when a check fails, or ``None`` when the
        value is safe.
    """
    if len(value) > max_len:
        return f"{field} is too long (max {max_len} characters)."
    if "<" in value or ">" in value:
        return f"{field} must not contain HTML characters."
    if "\x00" in value:
        return f"{field} contains invalid characters."
    if _UNSAFE_XSS.search(value):
        return f"{field} contains unsafe content."
    if _UNSAFE_SQL.search(value):
        return f"{field} contains unsafe content."
    return None


def _get_market(ticker: str) -> str:
    """Return ``'india'`` for NSE/BSE tickers (.NS / .BO), ``'us'`` otherwise.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        ``'india'`` or ``'us'``.
    """
    return "india" if ticker.upper().endswith((".NS", ".BO")) else "us"


def _currency_symbol(code: str) -> str:
    """Return the display symbol for a 3-letter ISO currency code.

    Args:
        code: ISO 4217 currency code, e.g. ``"USD"`` or ``"INR"``.

    Returns:
        The currency symbol string, e.g. ``"$"`` or ``"₹"``.
        Falls back to the code itself for unmapped currencies.
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


def _load_currency_from_iceberg(ticker: str) -> str:
    """Read currency symbol from Iceberg company_info for *ticker*.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Currency symbol string such as ``"$"`` or ``"₹"``.
        Falls back to ``"$"`` if no company info is available.
    """
    try:
        from dashboard.callbacks.iceberg import _get_iceberg_repo

        repo = _get_iceberg_repo()
        if repo is not None:
            info = repo.get_latest_company_info(ticker.upper())
            if info is not None:
                return _currency_symbol(info.get("currency", "USD") or "USD")
    except Exception:
        logger.debug(
            "Iceberg company_info unavailable for %r; defaulting to '$'.",
            ticker,
        )
    return "$"


def _get_currency(ticker: str) -> str:
    """Return the currency symbol for *ticker*.

    Cached for ``_CURRENCY_TTL`` seconds.

    Reads from Iceberg company_info on first call (or after TTL expiry);
    subsequent calls within the TTL window return the cached symbol.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Currency symbol string such as ``"$"`` or ``"₹"``.
    """
    now = _time.monotonic()
    entry = _CURRENCY_CACHE_DASH.get(ticker)
    if entry and entry[1] > now:
        return entry[0]
    result = _load_currency_from_iceberg(ticker)
    _CURRENCY_CACHE_DASH[ticker] = (result, now + _CURRENCY_TTL)
    return result
