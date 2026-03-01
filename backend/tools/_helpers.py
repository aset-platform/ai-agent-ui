"""Shared currency helpers for backend tool modules.

Provides :func:`_currency_symbol` and :func:`_load_currency`, which were
previously duplicated across ``_stock_shared``, ``_analysis_shared``, and
``_forecast_shared``.  Importing from here ensures a single definition and
a module-level TTL cache for ``_load_currency``.

Usage::

    from tools._helpers import _currency_symbol, _load_currency
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

# Module-level logger; kept at module scope as a conventional singleton.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"

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
        "USD": "$", "INR": "₹", "GBP": "£", "EUR": "€",
        "JPY": "¥", "CNY": "¥", "AUD": "A$", "CAD": "CA$",
        "HKD": "HK$", "SGD": "S$",
    }.get((code or "USD").upper(), code or "$")


def _load_currency(ticker: str, metadata_dir: Optional[Path] = None) -> str:
    """Read the ISO currency code for *ticker* from its metadata JSON.

    Results are cached for :data:`_CACHE_TTL_SECONDS` seconds at module level
    to avoid repeated file reads when the same ticker is queried multiple times
    within a single request or agent turn.

    Args:
        ticker: Stock ticker symbol (already uppercased).
        metadata_dir: Override the metadata directory (for testing).
            Defaults to the project-level ``data/metadata/`` directory.

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

    base = metadata_dir if metadata_dir is not None else _DATA_METADATA
    meta_path = base / f"{ticker}_info.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        code = data.get("currency", "USD") or "USD"
    except Exception:
        code = "USD"

    _CURRENCY_CACHE[ticker] = (code, now + _CACHE_TTL_SECONDS)
    return code
