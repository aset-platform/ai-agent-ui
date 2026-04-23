"""Bulk download OHLCV for all stocks in stock_master via yfinance.

Uses yf.download() batch mode which fetches hundreds of tickers
concurrently — dramatically faster than one-by-one NSE scraping.

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    PYTHONPATH=.:backend python scripts/bulk_download_ohlcv.py

    # Optional: limit batch size
    PYTHONPATH=.:backend python scripts/bulk_download_ohlcv.py --batch 100

    # Specific tickers only
    PYTHONPATH=.:backend python scripts/bulk_download_ohlcv.py --tickers RELIANCE,TCS,INFY
"""

import argparse
import asyncio
import logging
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

_logger = logging.getLogger(__name__)


async def _load_tickers(
    batch: int | None = None,
    tickers_csv: str | None = None,
) -> list[dict]:
    """Load tickers from stock_master."""
    if tickers_csv:
        syms = [
            t.strip().upper()
            for t in tickers_csv.split(",")
            if t.strip()
        ]
        return [
            {"symbol": s, "yf_ticker": f"{s}.NS"}
            for s in syms
        ]

    from backend.db.engine import get_session_factory
    from backend.db.models.stock_master import StockMaster
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(StockMaster)
            .where(StockMaster.is_active.is_(True))
            .order_by(StockMaster.id)
        )
        if batch:
            stmt = stmt.limit(batch)
        result = await session.execute(stmt)
        stocks = list(result.scalars().all())

    return [
        {
            "id": s.id,
            "symbol": s.symbol,
            "yf_ticker": s.yf_ticker,
        }
        for s in stocks
    ]


def _download_batch(
    yf_tickers: list[str],
    period: str = "10y",
) -> dict[str, pd.DataFrame]:
    """Download OHLCV for all tickers via yf.download().

    Returns dict mapping yf_ticker -> DataFrame.
    """
    _logger.info(
        "Downloading %d tickers via yfinance...",
        len(yf_tickers),
    )
    t0 = time.time()

    raw = yf.download(
        yf_tickers,
        period=period,
        group_by="ticker",
        auto_adjust=False,
        threads=True,
    )

    elapsed = time.time() - t0
    _logger.info(
        "yfinance download done in %.1fs — shape %s",
        elapsed, raw.shape,
    )

    # Split multi-ticker DataFrame into per-ticker dfs
    result: dict[str, pd.DataFrame] = {}
    if len(yf_tickers) == 1:
        # Single ticker: no multi-level columns
        ticker = yf_tickers[0]
        if not raw.empty:
            result[ticker] = raw
    else:
        for ticker in yf_tickers:
            try:
                df = raw[ticker].dropna(how="all")
                if not df.empty:
                    result[ticker] = df
                else:
                    _logger.warning(
                        "No data for %s", ticker,
                    )
            except KeyError:
                _logger.warning(
                    "Ticker %s not in response", ticker,
                )

    _logger.info(
        "Got data for %d/%d tickers",
        len(result), len(yf_tickers),
    )
    return result


async def _write_to_iceberg(
    ticker_map: dict[str, str],
    data: dict[str, pd.DataFrame],
) -> dict:
    """Write downloaded data to Iceberg + update registry.

    Args:
        ticker_map: yf_ticker -> canonical symbol mapping
        data: yf_ticker -> DataFrame from yfinance
    """
    from backend.db.engine import get_session_factory
    from backend.db.pg_stocks import upsert_registry
    from backend.tools._stock_shared import _require_repo

    repo = _require_repo()
    factory = get_session_factory()
    loop = asyncio.get_running_loop()

    written = 0
    failed = 0
    skipped = 0
    today = date.today()

    for yf_ticker, df in data.items():
        # Use yf_ticker (.NS format) as the storage key
        # to match existing system conventions.
        store_as = yf_ticker
        try:
            # Ensure DatetimeIndex
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            df.index = df.index.tz_localize(None)

            rows = await loop.run_in_executor(
                None, repo.insert_ohlcv, store_as, df,
            )

            # Update registry
            df_min = df.index.min().date()
            df_max = df.index.max().date()

            async with factory() as session:
                await upsert_registry(session, {
                    "ticker": store_as,
                    "last_fetch_date": today,
                    "total_rows": rows,
                    "date_range_start": df_min,
                    "date_range_end": df_max,
                    "market": "india",
                })

            written += 1
            if written % 50 == 0:
                _logger.info(
                    "Progress: %d written", written,
                )
        except Exception:
            _logger.exception(
                "Failed to write %s", symbol,
            )
            failed += 1

    return {
        "written": written,
        "failed": failed,
        "skipped": skipped,
    }


async def _update_cursor(total_written: int) -> None:
    """Update the nifty500_bulk cursor to completed."""
    from backend.db.engine import get_session_factory
    from backend.pipeline.cursor import (
        get_cursor,
        set_cursor_status,
    )

    factory = get_session_factory()
    async with factory() as session:
        cursor = await get_cursor(
            session, "nifty500_bulk",
        )
        if cursor:
            cursor.last_processed_id = (
                cursor.total_tickers
            )
            await session.commit()
            await set_cursor_status(
                session, "nifty500_bulk", "completed",
            )
            _logger.info(
                "Cursor nifty500_bulk → completed",
            )


async def run(
    batch: int | None = None,
    tickers_csv: str | None = None,
    period: str = "10y",
) -> None:
    """Main entry point."""
    stocks = await _load_tickers(batch, tickers_csv)
    if not stocks:
        _logger.error("No tickers found in stock_master")
        return

    # Build yf_ticker -> symbol map
    ticker_map: dict[str, str] = {}
    yf_tickers: list[str] = []
    for s in stocks:
        yf_t = s["yf_ticker"]
        ticker_map[yf_t] = s["symbol"]
        yf_tickers.append(yf_t)

    # Include sector indices for forecast enrichment
    from tools._stock_registry import SECTOR_INDICES

    for idx in SECTOR_INDICES:
        if idx not in ticker_map:
            ticker_map[idx] = idx
            yf_tickers.append(idx)

    _logger.info(
        "Bulk downloading %d tickers (period=%s)",
        len(yf_tickers), period,
    )

    # Download in chunks of 100 to avoid yfinance
    # memory issues with very large batches
    chunk_size = 100
    all_data: dict[str, pd.DataFrame] = {}

    for i in range(0, len(yf_tickers), chunk_size):
        chunk = yf_tickers[i:i + chunk_size]
        _logger.info(
            "Chunk %d-%d of %d",
            i + 1, min(i + chunk_size, len(yf_tickers)),
            len(yf_tickers),
        )
        chunk_data = _download_batch(chunk, period)
        all_data.update(chunk_data)

    # Write to Iceberg
    result = await _write_to_iceberg(
        ticker_map, all_data,
    )

    _logger.info(
        "Bulk complete: written=%d failed=%d "
        "no_data=%d (of %d total)",
        result["written"],
        result["failed"],
        len(yf_tickers) - len(all_data),
        len(yf_tickers),
    )

    # Update cursor
    if not tickers_csv:
        await _update_cursor(result["written"])

    # Auto-fill company_info gaps from stock_master
    _logger.info("Filling company_info gaps...")
    from backend.pipeline.jobs.fill_gaps import (
        fill_company_info_gaps,
    )
    gaps = fill_company_info_gaps()
    _logger.info(
        "Gap fill: patched=%d skipped=%d",
        gaps["patched"], gaps["skipped"],
    )


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s %(message)s"
        ),
    )

    parser = argparse.ArgumentParser(
        description="Bulk download OHLCV via yfinance",
    )
    parser.add_argument(
        "--batch", type=int, default=None,
        help="Limit to first N tickers",
    )
    parser.add_argument(
        "--tickers", default=None,
        help="Comma-separated tickers (skip DB)",
    )
    parser.add_argument(
        "--period", default="10y",
        help="History period (default: 10y)",
    )
    args = parser.parse_args()

    asyncio.run(run(args.batch, args.tickers, args.period))


if __name__ == "__main__":
    main()
