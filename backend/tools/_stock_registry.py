"""Stock registry read/write helpers backed by Iceberg.

Provides functions for looking up and updating ticker metadata in the
``stocks.registry`` Iceberg table — the single source of truth.

All accesses go through ``tools._stock_shared._require_repo()``
so that monkeypatching works in tests.

Functions
---------
- :func:`_load_registry`
- :func:`_check_existing_data`
- :func:`_update_registry`
"""

import logging
from datetime import date
from pathlib import Path

import pandas as pd

# Module-level logger (not a mutable global).
_logger = logging.getLogger(__name__)


def _load_registry() -> dict:
    """Load the full stock registry from Iceberg.

    Returns:
        Dict mapping ticker symbols to their metadata records.
    """
    from tools._stock_shared import _require_repo

    try:
        return _require_repo().get_all_registry()
    except Exception as e:
        _logger.warning("Failed to load stock registry from Iceberg: %s", e)
        return {}


def _check_existing_data(ticker: str) -> dict:
    """Look up a ticker in the Iceberg stock registry.

    Args:
        ticker: The stock ticker symbol (already uppercased).

    Returns:
        The registry entry dict if the ticker exists, or ``None``.
    """
    from tools._stock_shared import _require_repo

    try:
        return _require_repo().check_existing_data(ticker)
    except Exception as e:
        _logger.warning("Failed to check existing data for %s: %s", ticker, e)
        return None


def _update_registry(ticker: str, df: pd.DataFrame, file_path: Path) -> None:
    """Update the Iceberg stock registry with metadata for a ticker.

    Args:
        ticker: The stock ticker symbol (already uppercased).
        df: The full OHLCV DataFrame.
        file_path: Absolute path to the saved parquet file (unused but kept
            for call-site compatibility).
    """
    from tools._stock_shared import _require_repo

    from market_utils import detect_market

    # Preserve existing market if present — don't
    # overwrite "india" with "us".
    existing_reg = _check_existing_data(ticker)
    existing_mkt = None
    if existing_reg:
        existing_mkt = existing_reg.get("market")

    market = detect_market(ticker, existing_mkt)
    _require_repo().upsert_registry(
        ticker=ticker,
        last_fetch_date=date.today(),
        total_rows=len(df),
        date_range_start=df.index.min().date(),
        date_range_end=df.index.max().date(),
        market=market,
    )
