"""Stock registry read/write helpers.

Provides functions for loading and saving the ``stock_registry.json`` file
and for looking up / updating ticker metadata entries.

All accesses to path constants go through ``tools._stock_shared`` module
attributes so that monkeypatching works in tests.

Functions
---------
- :func:`_load_registry`
- :func:`_save_registry`
- :func:`_check_existing_data`
- :func:`_update_registry`
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

# Module-level logger — kept here as a module-level constant (not a mutable global).
_logger = logging.getLogger(__name__)


def _load_registry() -> dict:
    """Load the stock registry JSON file from disk.

    Returns:
        Dict mapping ticker symbols to their metadata records.
    """
    import tools._stock_shared as _ss

    if not _ss._REGISTRY_PATH.exists():
        return {}
    try:
        with open(_ss._REGISTRY_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        _logger.warning("Failed to load stock registry: %s", e)
        return {}


def _save_registry(registry: dict) -> None:
    """Persist the stock registry dict to disk as JSON.

    Args:
        registry: Dict mapping ticker symbols to metadata records.
    """
    import tools._stock_shared as _ss

    _ss._DATA_METADATA.mkdir(parents=True, exist_ok=True)
    with open(_ss._REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2, default=str)
    _logger.debug("Registry saved with %d entries", len(registry))


def _check_existing_data(ticker: str) -> Optional[dict]:
    """Look up a ticker in the stock registry.

    Args:
        ticker: The stock ticker symbol (already uppercased).

    Returns:
        The registry entry dict if the ticker exists, or ``None``.
    """
    return _load_registry().get(ticker)


def _update_registry(ticker: str, df: pd.DataFrame, file_path: Path) -> None:
    """Update the stock registry with metadata for a ticker.

    Also performs an Iceberg dual-write to the ``stocks.registry`` table.

    Args:
        ticker: The stock ticker symbol (already uppercased).
        df: The full OHLCV DataFrame.
        file_path: Absolute path to the saved parquet file.
    """
    from tools._stock_shared import _get_repo

    registry = _load_registry()
    registry[ticker] = {
        "ticker": ticker,
        "last_fetch_date": str(date.today()),
        "total_rows": len(df),
        "date_range": {
            "start": str(df.index.min().date()),
            "end": str(df.index.max().date()),
        },
        "file_path": str(file_path),
    }
    _save_registry(registry)
    try:
        repo = _get_repo()
        if repo is not None:
            market = "india" if ticker.upper().endswith((".NS", ".BO")) else "us"
            repo.upsert_registry(
                ticker=ticker,
                last_fetch_date=date.today(),
                total_rows=len(df),
                date_range_start=df.index.min().date(),
                date_range_end=df.index.max().date(),
                market=market,
            )
    except Exception as _e:
        _logger.warning("Iceberg registry upsert failed for %s: %s", ticker, _e)