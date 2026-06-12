"""Backfill NSE ETF ticker_type in public.stock_registry.

One-off idempotent script: reclassifies India rows in
``public.stock_registry`` whose ``ticker_type`` is not already
``'etf'`` but match the NSE ETF rules (BEES/ETF suffix or curated
set).

Usage::

    docker compose exec backend \\
        python scripts/backfill_nse_etf_ticker_type.py

Safe to re-run — rows already marked ``'etf'`` are excluded by
the WHERE clause, so no double-updates occur.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import text

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
_logger = logging.getLogger("backfill_nse_etf")


async def _backfill() -> None:
    from backend.db.engine import get_session_factory
    from backend.tools._stock_registry import _is_nse_etf

    sf = get_session_factory()

    # 1. Fetch India rows not already classified as etf.
    async with sf() as session:
        res = await session.execute(
            text(
                "SELECT ticker FROM public.stock_registry "
                "WHERE market = 'india' "
                "  AND ticker_type <> 'etf'"
            )
        )
        candidates = [row[0] for row in res.fetchall()]

    _logger.info(
        "Fetched %d India non-etf rows to inspect",
        len(candidates),
    )

    # 2. Apply _is_nse_etf to find rows to reclassify.
    to_reclassify: list[str] = []
    for ticker in candidates:
        clean = (
            ticker.replace(".NS", "")
            .replace(".BO", "")
        )
        if _is_nse_etf(clean):
            to_reclassify.append(ticker)

    if not to_reclassify:
        _logger.info(
            "No rows need reclassification — "
            "already up to date."
        )
        return

    # 3. Bulk UPDATE.
    async with sf() as session:
        await session.execute(
            text(
                "UPDATE public.stock_registry "
                "SET ticker_type = 'etf' "
                "WHERE ticker = ANY(:tickers)"
            ),
            {"tickers": to_reclassify},
        )
        await session.commit()

    _logger.info(
        "Reclassified %d ticker(s) to 'etf': %s",
        len(to_reclassify),
        sorted(to_reclassify),
    )


def main() -> None:
    """Entry point — run the async backfill."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(1) as p:
            p.submit(
                asyncio.run, _backfill(),
            ).result()
    else:
        asyncio.run(_backfill())


if __name__ == "__main__":
    sys.exit(main() or 0)
