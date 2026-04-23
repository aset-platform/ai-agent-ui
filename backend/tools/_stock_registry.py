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

# Sector indices for forecast enrichment.
# Downloaded during bulk-download pipeline runs so that
# relative-strength calculations have index OHLCV available.
SECTOR_INDICES: list[str] = [
    # India sector indices
    "^NSEBANK",
    "^CNXIT",
    "^CNXPHARMA",
    "^CNXFMCG",
    "^CNXAUTO",
    # US sector ETFs (broad sector proxies)
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLY",
]


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


_etf_symbols: set[str] | None = None


def _load_etf_symbols() -> set[str]:
    """Load ETF symbols from stock_master tags.

    Cached after first call to avoid repeated PG
    queries during batch operations.
    """
    global _etf_symbols  # noqa: PLW0603
    if _etf_symbols is not None:
        return _etf_symbols
    try:
        from backend.db.engine import (
            get_session_factory,
        )
        from backend.db.models.stock_master import (
            StockMaster,
        )
        from backend.db.models.stock_tag import (
            StockTag,
        )

        import asyncio
        from sqlalchemy import select

        sf = get_session_factory()

        async def _q():
            async with sf() as s:
                r = await s.execute(
                    select(StockMaster.symbol)
                    .join(StockTag)
                    .where(
                        StockTag.tag == "etf",
                        StockTag.removed_at.is_(
                            None,
                        ),
                    )
                )
                return {
                    row[0] for row in r.fetchall()
                }

        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
            ) as pool:
                syms = pool.submit(
                    asyncio.run, _q(),
                ).result()
        except RuntimeError:
            syms = asyncio.run(_q())
        _etf_symbols = syms
    except Exception:
        _logger.debug(
            "ETF symbol load failed",
            exc_info=True,
        )
        _etf_symbols = set()
    return _etf_symbols


def _detect_ticker_type(ticker: str) -> str:
    """Classify a ticker as stock, index, commodity,
    or etf.

    Index tickers start with ``^``.  Commodity futures
    end with ``=F`` or contain ``.NYB``.  ETFs are
    detected via stock_master tags (cached).
    Everything else is a stock.
    """
    if ticker.startswith("^"):
        return "index"
    if "=F" in ticker or ".NYB" in ticker:
        return "commodity"

    # Check cached ETF symbols from stock_master
    clean = ticker.replace(".NS", "").replace(
        ".BO", "",
    )
    if clean in _load_etf_symbols():
        return "etf"
    return "stock"


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
        ticker_type=_detect_ticker_type(ticker),
    )
