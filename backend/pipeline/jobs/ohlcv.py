"""OHLCV bulk and daily ingestion jobs.

Crash-safe cursor-based bulk ingestion with per-ticker
advance, and a simpler daily delta job for already-loaded
stocks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select

from backend.db.engine import get_session_factory
from backend.db.models.stock_master import StockMaster
from backend.db.pg_stocks import get_registry, upsert_registry
from backend.pipeline.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_HISTORY_YEARS,
    MAX_CONCURRENCY,
    REQUEST_DELAY_S,
)
from backend.pipeline.cursor import (
    advance_cursor,
    get_cursor,
    get_next_batch,
    log_skipped,
    set_cursor_status,
)
from backend.pipeline.observability import (
    PipelineLogger,
    RateLimitTracker,
    retry_with_backoff,
)
from backend.pipeline.router import get_ohlcv_source
from backend.pipeline.sources.base import (
    SourceError,
    SourceErrorCategory,
)

_logger = logging.getLogger(__name__)
_pl = PipelineLogger(_logger)


# ------------------------------------------------------------------
# Per-ticker processing (shared by bulk and daily)
# ------------------------------------------------------------------


async def _process_ticker(
    stock: StockMaster,
    source,
    cursor_name: str | None,
    session_factory,
    rate_tracker: RateLimitTracker,
) -> str:
    """Fetch and store OHLCV for one ticker.

    Returns:
        ``"ok"``, ``"skipped"``, or ``"failed"``.
    """
    from backend.pipeline.sources.nse import (
        NseSource as _Nse,
    )

    symbol = stock.symbol
    # Pick the right symbol format for the source:
    # NseSource expects plain symbol, others expect .NS
    nse_sym = stock.nse_symbol or symbol
    yf_sym = stock.yf_ticker or f"{symbol}.NS"
    fetch_sym = (
        nse_sym if isinstance(source, _Nse) else yf_sym
    )
    # Storage key: use .NS format to match existing
    # system conventions (Iceberg, registry, frontend).
    store_as = yf_sym
    today = date.today()
    yesterday = today - timedelta(days=1)

    async with session_factory() as session:
        reg = await get_registry(
            session, ticker=store_as,
        )

    # Decide date range ------------------------------------------
    if reg and reg.get("date_range_end"):
        end_date = reg["date_range_end"]
        if end_date >= yesterday:
            _pl.ticker_skipped(symbol, "fresh")
            return "skipped"
        start_date = end_date + timedelta(days=1)
    else:
        start_date = today - timedelta(
            days=365 * DEFAULT_HISTORY_YEARS
        )

    # Fetch -------------------------------------------------------
    t0 = time.monotonic()
    try:
        df = await retry_with_backoff(
            lambda s=fetch_sym, sd=start_date, td=today: (
                source.fetch_ohlcv(s, start=sd, end=td)
            ),
            ticker=symbol,
            logger=_pl,
        )
    except SourceError as exc:
        if exc.category == SourceErrorCategory.RATE_LIMIT:
            await rate_tracker.record_rate_limit()
        _pl.ticker_failed(
            symbol, exc.category.value, str(exc),
        )
        if cursor_name:
            async with session_factory() as session:
                await log_skipped(
                    session,
                    cursor_name,
                    symbol,
                    "bulk",
                    str(exc),
                    exc.category.value,
                )
        return "failed"

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    src_name = (
        type(source).__name__
        .lower()
        .replace("source", "")
    )
    _pl.ticker_fetched(symbol, src_name, elapsed_ms)

    if df.empty:
        _pl.ticker_skipped(symbol, "empty_response")
        return "skipped"

    # Rename columns to Title Case for StockRepository
    # (insert_ohlcv expects Open/High/Low/Close/Volume).
    _COL_MAP = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "adj_close": "Adj Close",
        "volume": "Volume",
        "date": "Date",
    }
    df = df.rename(columns=_COL_MAP)

    # Set Date as DatetimeIndex (insert_ohlcv reads df.index)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Write to Iceberg --------------------------------------------
    from backend.tools._stock_shared import _require_repo

    repo = _require_repo()
    loop = asyncio.get_running_loop()
    try:
        rows = await loop.run_in_executor(
            None, repo.insert_ohlcv, store_as, df,
        )
    except Exception:
        _logger.exception(
            "Iceberg write failed for %s", store_as,
        )
        if cursor_name:
            async with session_factory() as session:
                await log_skipped(
                    session,
                    cursor_name,
                    symbol,
                    "bulk",
                    "Iceberg write failed",
                    "unknown",
                )
        return "failed"

    rate_tracker.record_success()

    # Update stock_registry ---------------------------------------
    df_min = df.index.min()
    df_max = df.index.max()
    # Normalise to date objects
    if hasattr(df_min, "date"):
        df_min = df_min.date()
    if hasattr(df_max, "date"):
        df_max = df_max.date()

    async with session_factory() as session:
        existing = await get_registry(
            session, ticker=store_as,
        )
        total = rows
        if existing and existing.get("total_rows"):
            total += existing["total_rows"]

        await upsert_registry(session, {
            "ticker": store_as,
            "last_fetch_date": today,
            "total_rows": total,
            "date_range_start": (
                existing["date_range_start"]
                if existing
                and existing.get("date_range_start")
                else df_min
            ),
            "date_range_end": df_max,
            "market": "india",
        })

    return "ok"


# ------------------------------------------------------------------
# Bulk job (cursor-based)
# ------------------------------------------------------------------


async def run_bulk(
    cursor_name: str = "nifty500_sample_bulk",
    batch_size: int | None = None,
) -> dict:
    """Run one batch of bulk OHLCV ingestion.

    Reads the cursor, fetches the next batch of tickers,
    processes each with semaphore-controlled concurrency,
    and advances the cursor per-ticker for crash safety.

    Returns:
        Summary dict with processed/skipped/failed counts.
    """
    if batch_size is None:
        batch_size = DEFAULT_BATCH_SIZE

    session_factory = get_session_factory()
    source = get_ohlcv_source("bulk")
    rate_tracker = RateLimitTracker()
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    # Read cursor -------------------------------------------------
    async with session_factory() as session:
        cursor = await get_cursor(session, cursor_name)
    if cursor is None:
        raise ValueError(
            f"Cursor not found: {cursor_name}"
        )

    last_id = cursor.last_processed_id
    total = cursor.total_tickers

    _pl.batch_started(cursor_name, batch_size, last_id)

    async with session_factory() as session:
        await set_cursor_status(
            session, cursor_name, "in_progress",
        )

    # Get next batch ----------------------------------------------
    async with session_factory() as session:
        stocks = await get_next_batch(
            session, cursor_name,
        )

    if not stocks:
        async with session_factory() as session:
            await set_cursor_status(
                session, cursor_name, "completed",
            )
        _logger.info(
            "Cursor %s completed — no more tickers",
            cursor_name,
        )
        return {
            "cursor": cursor_name,
            "status": "completed",
            "processed": 0,
            "skipped": 0,
            "failed": 0,
        }

    # Process each ticker -----------------------------------------
    processed = 0
    skipped = 0
    failed = 0
    t_batch = time.monotonic()

    async def _bounded(stock: StockMaster) -> str:
        async with sem:
            result = await _process_ticker(
                stock,
                source,
                cursor_name,
                session_factory,
                rate_tracker,
            )
            # Per-ticker cursor advance for crash safety
            async with session_factory() as session:
                await advance_cursor(
                    session, cursor_name, stock.id,
                )
            _pl.cursor_progress(
                cursor_name, stock.id, total,
            )
            await asyncio.sleep(REQUEST_DELAY_S)
            return result

    # Run sequentially to respect rate limits and
    # maintain deterministic cursor ordering.
    for stock in stocks:
        try:
            outcome = await _bounded(stock)
        except SourceError:
            # Batch-level rate limit — stop early
            _logger.warning(
                "Batch-level rate limit hit, "
                "stopping early at id=%d",
                stock.id,
            )
            failed += 1
            break

        if outcome == "ok":
            processed += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            failed += 1

    duration_s = time.monotonic() - t_batch
    _pl.batch_completed(
        cursor_name, processed, skipped,
        failed, duration_s,
    )

    # Set final status --------------------------------------------
    # Check if more tickers remain after this batch.
    async with session_factory() as session:
        remaining = await get_next_batch(
            session, cursor_name,
        )
    if not remaining:
        async with session_factory() as session:
            await set_cursor_status(
                session, cursor_name, "completed",
            )
        status = "completed"
    else:
        status = "in_progress"

    return {
        "cursor": cursor_name,
        "status": status,
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "duration_s": round(duration_s, 2),
    }


# ------------------------------------------------------------------
# Daily delta job
# ------------------------------------------------------------------


async def run_daily() -> dict:
    """Run daily delta OHLCV for all active, bulk-loaded stocks.

    Queries stock_master for active tickers that already have
    registry data, then fetches from date_range_end + 1 to
    today.

    Returns:
        Summary dict with processed/skipped/failed counts.
    """
    session_factory = get_session_factory()
    source = get_ohlcv_source("daily")
    rate_tracker = RateLimitTracker()
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    # Get eligible stocks -----------------------------------------
    async with session_factory() as session:
        result = await session.execute(
            select(StockMaster).where(
                StockMaster.is_active.is_(True),
            )
        )
        all_stocks = list(result.scalars().all())

    # Filter to those with registry date_range_end ----------------
    eligible: list[StockMaster] = []
    async with session_factory() as session:
        for stock in all_stocks:
            reg = await get_registry(
                session,
                ticker=stock.yf_ticker or stock.symbol,
            )
            if reg and reg.get("date_range_end"):
                eligible.append(stock)

    _logger.info(
        "Daily OHLCV: %d eligible out of %d active",
        len(eligible),
        len(all_stocks),
    )

    if not eligible:
        return {
            "job": "daily",
            "processed": 0,
            "skipped": 0,
            "failed": 0,
        }

    processed = 0
    skipped = 0
    failed = 0
    t0 = time.monotonic()

    async def _bounded(stock: StockMaster) -> str:
        async with sem:
            result = await _process_ticker(
                stock,
                source,
                None,  # no cursor for daily
                session_factory,
                rate_tracker,
            )
            await asyncio.sleep(REQUEST_DELAY_S)
            return result

    for stock in eligible:
        try:
            outcome = await _bounded(stock)
        except SourceError:
            _logger.warning(
                "Batch-level rate limit in daily job, "
                "stopping early at %s",
                stock.symbol,
            )
            failed += 1
            break

        if outcome == "ok":
            processed += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            failed += 1

    duration_s = time.monotonic() - t0

    _logger.info(
        "Daily OHLCV done: processed=%d skipped=%d "
        "failed=%d duration=%.2fs",
        processed, skipped, failed, duration_s,
    )

    return {
        "job": "daily",
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "duration_s": round(duration_s, 2),
    }
