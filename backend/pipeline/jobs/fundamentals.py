"""Fundamentals ingestion job — company_info + dividends.

Fetches yfinance .info and .dividends for each ticker in the
stock_master universe using keyset cursor pagination.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from functools import partial

import pandas as pd
import yfinance as yf
from sqlalchemy import func, select

from backend.db.engine import get_session_factory
from backend.db.models.stock_master import StockMaster
from backend.pipeline.config import (
    DEFAULT_BATCH_SIZE,
    REQUEST_DELAY_S,
)
from backend.pipeline.cursor import (
    advance_cursor,
    create_cursor,
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
from backend.pipeline.sources.base import (
    SourceError,
    SourceErrorCategory,
    classify_error,
)
from backend.tools._stock_shared import _require_repo

_logger = logging.getLogger(__name__)
_plog = PipelineLogger(_logger)

_CURSOR_NAME = "nifty500_fundamentals"


# ------------------------------------------------------------------
# yfinance helpers (sync — run in executor)
# ------------------------------------------------------------------


def _fetch_info(yf_ticker: str) -> dict:
    """Fetch .info dict from yfinance (blocking)."""
    try:
        return yf.Ticker(yf_ticker).info or {}
    except Exception as exc:
        cat = classify_error(exc)
        raise SourceError(cat, str(exc), exc) from exc


def _fetch_dividends(yf_ticker: str) -> pd.DataFrame:
    """Fetch .dividends Series from yfinance (blocking).

    Returns a DataFrame with columns [date, dividend].
    """
    try:
        series = yf.Ticker(yf_ticker).dividends
        if series is None or series.empty:
            return pd.DataFrame(columns=["date", "dividend"])
        df = series.reset_index()
        df.columns = ["date", "dividend"]
        # Normalise date to datetime.date
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    except Exception as exc:
        cat = classify_error(exc)
        raise SourceError(cat, str(exc), exc) from exc


def _write_company_info(repo, ticker: str, info: dict) -> None:
    """Write company_info to Iceberg (blocking)."""
    try:
        repo.insert_company_info(ticker, info)
    except Exception as exc:
        cat = classify_error(exc)
        raise SourceError(cat, str(exc), exc) from exc


def _write_dividends(
    repo,
    ticker: str,
    df: pd.DataFrame,
    currency: str,
) -> int:
    """Write dividends to Iceberg (blocking)."""
    try:
        return repo.insert_dividends(
            ticker,
            df,
            currency=currency,
        )
    except Exception as exc:
        cat = classify_error(exc)
        raise SourceError(cat, str(exc), exc) from exc


# ------------------------------------------------------------------
# Per-ticker processing
# ------------------------------------------------------------------


async def _process_ticker(
    stock: StockMaster,
    repo,
    session_factory,
    cursor_name: str,
    semaphore: asyncio.Semaphore,
    rate_tracker: RateLimitTracker,
) -> str:
    """Fetch and persist fundamentals for one ticker.

    Returns:
        ``"ok"``, ``"skipped"``, or ``"failed"``.
    """
    loop = asyncio.get_running_loop()
    ticker = stock.symbol
    yf_sym = stock.yf_ticker
    t0 = time.monotonic()

    async def _do_fetch():
        async with semaphore:
            # 1. Fetch info
            info = await loop.run_in_executor(
                None,
                partial(_fetch_info, yf_sym),
            )
            await asyncio.sleep(REQUEST_DELAY_S)

            # 2. Fetch dividends
            div_df = await loop.run_in_executor(
                None,
                partial(_fetch_dividends, yf_sym),
            )
            return info, div_df

    try:
        info, div_df = await retry_with_backoff(
            _do_fetch,
            ticker,
            logger=_plog,
        )
    except SourceError as exc:
        if exc.category == SourceErrorCategory.RATE_LIMIT:
            await rate_tracker.record_rate_limit()
        _plog.ticker_failed(
            ticker,
            exc.category.value,
            str(exc),
        )
        async with session_factory() as sess:
            await log_skipped(
                sess,
                cursor_name,
                ticker,
                "fundamentals",
                str(exc),
                exc.category.value,
            )
            await advance_cursor(
                sess,
                cursor_name,
                stock.id,
            )
        return "failed"

    # -- persist to Iceberg ----------------------------------------
    try:
        await loop.run_in_executor(
            None,
            partial(_write_company_info, repo, yf_sym, info),
        )
        currency = stock.currency or "INR"
        rows_added = 0
        if not div_df.empty:
            rows_added = await loop.run_in_executor(
                None,
                partial(
                    _write_dividends,
                    repo,
                    yf_sym,
                    div_df,
                    currency,
                ),
            )
    except SourceError as exc:
        _plog.ticker_failed(
            ticker,
            exc.category.value,
            str(exc),
        )
        async with session_factory() as sess:
            await log_skipped(
                sess,
                cursor_name,
                ticker,
                "fundamentals",
                str(exc),
                exc.category.value,
            )
            await advance_cursor(
                sess,
                cursor_name,
                stock.id,
            )
        return "failed"

    # -- update stock_master PG fields -----------------------------
    try:
        async with session_factory() as sess:
            async with sess.begin():
                result = await sess.execute(
                    select(StockMaster).where(
                        StockMaster.id == stock.id,
                    )
                )
                sm = result.scalar_one_or_none()
                if sm:
                    changed = False
                    new_sector = info.get("sector")
                    if new_sector and sm.sector != new_sector:
                        sm.sector = new_sector
                        changed = True
                    new_industry = info.get("industry")
                    if new_industry and sm.industry != new_industry:
                        sm.industry = new_industry
                        changed = True
                    new_mcap = info.get("marketCap") or info.get("market_cap")
                    if new_mcap and sm.market_cap != new_mcap:
                        sm.market_cap = int(new_mcap)
                        changed = True
                    if changed:
                        sm.updated_at = datetime.now(
                            timezone.utc,
                        )
    except Exception as exc:
        _logger.warning(
            "stock_master update failed for %s: %s",
            ticker,
            exc,
        )

    # -- advance cursor --------------------------------------------
    async with session_factory() as sess:
        await advance_cursor(
            sess,
            cursor_name,
            stock.id,
        )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    _plog.ticker_fetched(ticker, "yfinance", elapsed_ms)
    rate_tracker.record_success()

    _logger.debug(
        "Fundamentals OK ticker=%s divs_added=%d",
        ticker,
        rows_added,
    )
    return "ok"


# ------------------------------------------------------------------
# Batch entry point
# ------------------------------------------------------------------


async def run_fundamentals(
    cursor_name: str = _CURSOR_NAME,
    batch_size: int | None = None,
) -> dict:
    """Run one batch of fundamentals ingestion.

    Uses its own cursor (independent from OHLCV).

    Per ticker (via yf_ticker):
    1. ``yf.Ticker(t).info``  -> Iceberg company_info
    2. ``yf.Ticker(t).dividends`` -> Iceberg dividends
    3. Update stock_master.sector, industry, market_cap

    Returns:
        Summary dict with processed/skipped/failed counts.
    """
    factory = get_session_factory()
    repo = _require_repo()
    summary: dict = {
        "cursor": cursor_name,
        "processed": 0,
        "failed": 0,
        "batch_size": batch_size or DEFAULT_BATCH_SIZE,
        "status": "ok",
    }

    # -- ensure cursor exists --------------------------------------
    async with factory() as session:
        cursor = await get_cursor(session, cursor_name)
        if cursor is None:
            total = await session.execute(
                select(func.count(StockMaster.id)).where(
                    StockMaster.is_active.is_(True),
                )
            )
            total_count = total.scalar() or 0
            bs = batch_size or DEFAULT_BATCH_SIZE
            cursor = await create_cursor(
                session,
                cursor_name,
                total_count,
                bs,
            )
        elif batch_size and cursor.batch_size != batch_size:
            cursor.batch_size = batch_size
            await session.commit()

    # -- set status in_progress ------------------------------------
    async with factory() as session:
        await set_cursor_status(
            session,
            cursor_name,
            "in_progress",
        )

    # -- fetch batch -----------------------------------------------
    async with factory() as session:
        batch = await get_next_batch(session, cursor_name)
        last_id = cursor.last_processed_id
        total = cursor.total_tickers

    if not batch:
        async with factory() as session:
            await set_cursor_status(
                session,
                cursor_name,
                "completed",
            )
        summary["status"] = "completed"
        _logger.info(
            "Fundamentals cursor %s completed",
            cursor_name,
        )
        return summary

    _plog.batch_started(
        cursor_name,
        len(batch),
        last_id,
    )

    # -- process tickers concurrently ------------------------------
    # Concurrency=1 to avoid Iceberg SQLite catalog
    # commit conflicts on company_info/dividends.
    sem = asyncio.Semaphore(1)
    rate_tracker = RateLimitTracker()
    t0 = time.monotonic()

    tasks = [
        _process_ticker(
            stock,
            repo,
            factory,
            cursor_name,
            sem,
            rate_tracker,
        )
        for stock in batch
    ]
    results = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    for r in results:
        if isinstance(r, Exception):
            summary["failed"] += 1
            _logger.error(
                "Unexpected error in fundamentals: %s",
                r,
            )
        elif r == "ok":
            summary["processed"] += 1
        else:
            summary["failed"] += 1

    duration_s = time.monotonic() - t0
    _plog.batch_completed(
        cursor_name,
        summary["processed"],
        0,
        summary["failed"],
        duration_s,
    )

    # -- pause cursor on rate limit escalation ---------------------
    if rate_tracker.should_pause:
        async with factory() as session:
            await set_cursor_status(
                session,
                cursor_name,
                "paused",
            )
        summary["status"] = "paused"
        _logger.warning(
            "Fundamentals cursor %s paused (rate limit)",
            cursor_name,
        )
    else:
        async with factory() as session:
            cur = await get_cursor(session, cursor_name)
            if cur:
                _plog.cursor_progress(
                    cursor_name,
                    cur.last_processed_id,
                    cur.total_tickers,
                )

    return summary
