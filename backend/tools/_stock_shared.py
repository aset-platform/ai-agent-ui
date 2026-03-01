"""Shared path constants, Iceberg repo singleton, and small helpers for stock tools.

All sub-modules (``_stock_registry``, ``_stock_fetch``) access these values as
module attributes (``import tools._stock_shared as _ss; _ss._DATA_RAW``) so
that ``monkeypatch.setattr(tools._stock_shared, "_DATA_RAW", ...)`` works in
tests.

Constants
---------
- :data:`_PROJECT_ROOT`
- :data:`_DATA_RAW`
- :data:`_DATA_PROCESSED`
- :data:`_DATA_METADATA`
- :data:`_REGISTRY_PATH`
- :data:`_STOCK_REPO`
- :data:`_STOCK_REPO_INIT_ATTEMPTED`
"""

import logging
import sys
from pathlib import Path
from typing import Optional

# Module-level logger; kept at module scope intentionally for shared utility use.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_PROCESSED = _PROJECT_ROOT / "data" / "processed"
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_REGISTRY_PATH = _DATA_METADATA / "stock_registry.json"

_STOCK_REPO = None
_STOCK_REPO_INIT_ATTEMPTED = False


def _get_repo():
    """Return the :class:`~stocks.repository.StockRepository` singleton.

    Initialised on first call.  Returns ``None`` when PyIceberg is
    unavailable.  After a failure, re-tries on the next call (no permanent
    caching of failures).

    Returns:
        :class:`~stocks.repository.StockRepository` instance or ``None``.
    """
    import tools._stock_shared as _ss

    if _ss._STOCK_REPO is not None:
        return _ss._STOCK_REPO
    if _ss._STOCK_REPO_INIT_ATTEMPTED:
        return None
    _ss._STOCK_REPO_INIT_ATTEMPTED = True
    try:
        _root = str(_ss._PROJECT_ROOT)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from stocks.repository import StockRepository  # noqa: PLC0415
        _ss._STOCK_REPO = StockRepository()
        _logger.debug("StockRepository initialised for dual-write")
    except Exception as _e:
        _logger.warning("StockRepository unavailable (Iceberg write disabled): %s", _e)
        _ss._STOCK_REPO_INIT_ATTEMPTED = False  # allow retry on next call
    return _ss._STOCK_REPO


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


def _load_currency(ticker: str) -> str:
    """Read the ISO currency code for *ticker* from its metadata JSON.

    Args:
        ticker: Stock ticker symbol (already uppercased).

    Returns:
        ISO currency code string.  Falls back to ``"USD"``.
    """
    import json
    import tools._stock_shared as _ss

    meta_path = _ss._DATA_METADATA / f"{ticker}_info.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("currency", "USD") or "USD"
    except Exception:
        return "USD"