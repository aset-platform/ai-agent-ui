"""Auto-fill company_info gaps from stock_master.

Runs after bulk OHLCV download to ensure every ticker
has complete company_info in Iceberg.  Patches empty
name, sector, industry, market_cap, currency using
stock_master as the source of truth, overlaid with any
available yfinance data.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from datetime import date
from functools import partial

import yfinance as yf

from backend.db.engine import get_session_factory
from backend.db.models.stock_master import StockMaster
from sqlalchemy import select

_logger = logging.getLogger(__name__)

# Fields to check and patch from stock_master
_PATCH_MAP = {
    # iceberg_key: (stock_master_attr, yfinance_info_key)
    "company_name": ("name", "longName"),
    "sector": ("sector", "sector"),
    "industry": ("industry", "industry"),
    "market_cap": ("market_cap", "marketCap"),
    "currency": ("currency", "currency"),
}


async def _load_master_map() -> dict[str, dict]:
    """Load yf_ticker → stock_master fields."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(
                StockMaster.yf_ticker,
                StockMaster.name,
                StockMaster.sector,
                StockMaster.industry,
                StockMaster.market_cap,
                StockMaster.currency,
            )
        )
        out: dict[str, dict] = {}
        for row in result.all():
            if not row[0]:
                continue
            out[row[0]] = {
                "name": row[1],
                "sector": row[2],
                "industry": row[3],
                "market_cap": row[4],
                "currency": row[5],
            }
        return out


def _run_async(coro):
    """Run async code from sync context safely."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(1) as p:
            return p.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _is_empty(val) -> bool:
    """True if value is None, empty, or NaN."""
    if val is None:
        return True
    if isinstance(val, str) and not val.strip():
        return True
    if isinstance(val, float):
        return val != val  # NaN check
    return False


def fill_company_info_gaps() -> dict:
    """Patch empty company_info fields from stock_master.

    For each registry ticker with gaps in company_info:
    1. Fetch yfinance .info (to get any available data)
    2. Overlay stock_master values for anything still empty
    3. Write patched entry to Iceberg

    Returns:
        Summary dict: {patched, skipped, no_master, total}
    """
    from backend.tools._stock_shared import _require_repo

    repo = _require_repo()
    master = _run_async(_load_master_map())
    _logger.info(
        "fill_gaps: loaded %d stock_master entries",
        len(master),
    )

    registry = repo.get_all_registry()
    tickers = list(registry.keys())
    _logger.info(
        "fill_gaps: checking %d registry tickers",
        len(tickers),
    )

    today = date.today()
    patched = 0
    skipped = 0
    no_master = 0

    for ticker in tickers:
        info = repo.get_latest_company_info_if_fresh(
            ticker, today,
        )

        # Check if any fields have gaps
        has_gap = False
        if info is None:
            has_gap = True
        else:
            for ice_key in _PATCH_MAP:
                if _is_empty(info.get(ice_key)):
                    has_gap = True
                    break

        if not has_gap:
            skipped += 1
            continue

        # Get stock_master data for this ticker
        sm = master.get(ticker)
        if not sm:
            no_master += 1
            continue

        # Fetch yfinance .info (best effort)
        try:
            yf_info = yf.Ticker(ticker).info or {}
        except Exception:
            yf_info = {}

        # Patch: yfinance first, stock_master as fallback
        for ice_key, (sm_attr, yf_key) in (
            _PATCH_MAP.items()
        ):
            yf_val = yf_info.get(yf_key)
            sm_val = sm.get(sm_attr)

            if _is_empty(yf_val) and not _is_empty(sm_val):
                yf_info[yf_key] = sm_val

        # Ensure longName/shortName are set
        if _is_empty(yf_info.get("longName")):
            yf_info["longName"] = sm.get("name", "")
        if _is_empty(yf_info.get("shortName")):
            yf_info["shortName"] = sm.get("name", "")

        try:
            repo.insert_company_info(ticker, yf_info)
            patched += 1
            if patched % 10 == 0:
                _logger.info(
                    "fill_gaps: patched %d so far",
                    patched,
                )
        except Exception:
            _logger.warning(
                "fill_gaps: failed to patch %s",
                ticker,
                exc_info=True,
            )

    summary = {
        "patched": patched,
        "skipped": skipped,
        "no_master": no_master,
        "total": len(tickers),
    }
    _logger.info(
        "fill_gaps done: patched=%d skipped=%d "
        "no_master=%d total=%d",
        patched, skipped, no_master, len(tickers),
    )
    return summary
