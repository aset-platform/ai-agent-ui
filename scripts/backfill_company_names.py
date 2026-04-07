"""Backfill empty company_name in Iceberg company_info.

Reads names from PG stock_master and patches Iceberg entries
where company_name is empty/null. One-time script — run after
pipeline seed + fundamentals.

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    PYTHONPATH=.:backend python scripts/backfill_company_names.py
"""

import asyncio
import logging
import sys

_logger = logging.getLogger(__name__)


async def _load_master_names() -> dict[str, str]:
    """Load yf_ticker → name map from stock_master."""
    from backend.db.engine import get_session_factory
    from backend.db.models.stock_master import (
        StockMaster,
    )
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(
                StockMaster.yf_ticker,
                StockMaster.name,
            )
        )
        return {
            r[0]: r[1]
            for r in result.all()
            if r[0] and r[1]
        }


def main() -> None:
    """Backfill empty company names."""
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s %(message)s"
        ),
    )

    from stocks.repository import StockRepository

    repo = StockRepository()

    # Load stock_master names
    import concurrent.futures

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(1) as p:
            master = p.submit(
                asyncio.run, _load_master_names(),
            ).result()
    else:
        master = asyncio.run(_load_master_names())

    _logger.info(
        "Loaded %d names from stock_master",
        len(master),
    )

    # Get all registry tickers
    registry = repo.get_all_registry()
    tickers = list(registry.keys())
    _logger.info("Registry has %d tickers", len(tickers))

    # Check each ticker's company_info
    patched = 0
    skipped = 0
    no_master = 0

    for ticker in tickers:
        info = repo.get_latest_company_info_if_fresh(
            ticker,
            __import__("datetime").date.today(),
        )

        if info and info.get("company_name"):
            skipped += 1
            continue

        # Name missing — check stock_master
        master_name = master.get(ticker)
        if not master_name:
            # Try canonical fallback
            canonical = (
                ticker.replace(".NS", "")
                .replace(".BO", "")
            )
            master_name = master.get(f"{canonical}.NS")

        if not master_name:
            no_master += 1
            _logger.debug(
                "No master name for %s", ticker,
            )
            continue

        # Patch: insert a new company_info entry with
        # the stock_master name
        import yfinance as yf

        try:
            yf_info = yf.Ticker(ticker).info
        except Exception:
            yf_info = {}

        yf_info["longName"] = (
            yf_info.get("longName") or master_name
        )
        yf_info["shortName"] = (
            yf_info.get("shortName") or master_name
        )

        try:
            repo.insert_company_info(ticker, yf_info)
            patched += 1
            _logger.info(
                "Patched %s → %s",
                ticker, master_name,
            )
        except Exception:
            _logger.warning(
                "Failed to patch %s", ticker,
                exc_info=True,
            )

    _logger.info(
        "Done: patched=%d skipped=%d "
        "no_master=%d total=%d",
        patched, skipped, no_master, len(tickers),
    )


if __name__ == "__main__":
    main()
