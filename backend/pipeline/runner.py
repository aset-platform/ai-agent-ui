"""CLI entry point for the stock data pipeline.

Usage::

    python -m backend.pipeline.runner <command> [args]

Data commands: seed, bulk, fundamentals, daily,
    quarterly, bulk-download, fill-gaps, correct.
Compute commands: analytics, sentiment, forecast,
    screen, indices.
Pipeline: refresh (full chain with --scope/--force).
Utility: status, skipped, retry, reset, download.
"""

import argparse
import asyncio
import logging
import sys

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Arg parser
# ------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Stock data pipeline CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # seed ---------------------------------------------------------
    p_seed = sub.add_parser(
        "seed",
        help="Seed stock_master from CSV",
    )
    p_seed.add_argument("--csv", required=True)
    p_seed.add_argument(
        "--update",
        action="store_true",
        help="Update existing stocks and reconcile tags",
    )

    # bulk ---------------------------------------------------------
    p_bulk = sub.add_parser(
        "bulk",
        help="Run one OHLCV bulk batch",
    )
    p_bulk.add_argument(
        "--cursor",
        default="nifty500_sample_bulk",
    )
    p_bulk.add_argument(
        "--batch-size",
        type=int,
        default=None,
    )
    p_bulk.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )
    p_bulk.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even if OHLCV is fresh",
    )

    # fundamentals -------------------------------------------------
    p_fund = sub.add_parser(
        "fundamentals",
        help="Run one fundamentals batch",
    )
    p_fund.add_argument(
        "--cursor",
        default="nifty500_fundamentals",
    )
    p_fund.add_argument(
        "--batch-size",
        type=int,
        default=None,
    )
    p_fund.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )
    p_fund.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch company info + dividends",
    )

    # daily --------------------------------------------------------
    p_daily = sub.add_parser(
        "daily",
        help="Run daily OHLCV delta",
    )
    p_daily.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )
    p_daily.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even if OHLCV is fresh",
    )

    # status -------------------------------------------------------
    p_status = sub.add_parser(
        "status",
        help="Show cursor progress",
    )
    p_status.add_argument(
        "--cursor",
        default="nifty500_sample_bulk",
    )

    # skipped ------------------------------------------------------
    p_skip = sub.add_parser(
        "skipped",
        help="List failed tickers",
    )
    p_skip.add_argument(
        "--cursor",
        default="nifty500_sample_bulk",
    )

    # retry --------------------------------------------------------
    p_retry = sub.add_parser(
        "retry",
        help="Retry failed tickers",
    )
    p_retry.add_argument(
        "--cursor",
        default="nifty500_sample_bulk",
    )
    p_retry.add_argument(
        "--all",
        action="store_true",
        dest="all_categories",
        help="Retry all categories, not just transient",
    )
    p_retry.add_argument(
        "--ticker",
        default=None,
        help="Retry a specific ticker symbol",
    )

    # download -----------------------------------------------------
    p_dl = sub.add_parser(
        "download",
        help="Download Nifty 500 list from NSE → CSV",
    )
    p_dl.add_argument(
        "--output",
        default="data/universe/nifty500.csv",
        help="Output CSV path",
    )

    # bulk-download ------------------------------------------------
    p_bd = sub.add_parser(
        "bulk-download",
        help="Fast yfinance batch OHLCV + fill gaps",
    )
    p_bd.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Limit to first N tickers",
    )
    p_bd.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated tickers (skip DB)",
    )
    p_bd.add_argument(
        "--period",
        default="10y",
        help="History period (default: 10y)",
    )

    # fill-gaps ----------------------------------------------------
    p_fill = sub.add_parser(
        "fill-gaps",
        help="Patch empty company_info from stock_master",
    )
    p_fill.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )

    # correct ------------------------------------------------------
    p_correct = sub.add_parser(
        "correct",
        help="Re-fetch from NSE for a specific ticker",
    )
    p_correct.add_argument(
        "--ticker",
        required=True,
        help="Ticker symbol to correct",
    )

    # reset --------------------------------------------------------
    p_reset = sub.add_parser(
        "reset",
        help="Reset cursor to start",
    )
    p_reset.add_argument(
        "--cursor",
        default="nifty500_sample_bulk",
    )
    p_reset.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )

    # quarterly ----------------------------------------------------
    p_qtr = sub.add_parser(
        "quarterly",
        help="Fetch quarterly statements (batch)",
    )
    p_qtr.add_argument(
        "--cursor",
        default="nse_universe_quarterly",
    )
    p_qtr.add_argument(
        "--batch-size",
        type=int,
        default=50,
    )
    p_qtr.add_argument(
        "--force",
        action="store_true",
        help="Skip 7-day freshness check",
    )
    p_qtr.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )

    # screen -------------------------------------------------------
    p_screen = sub.add_parser(
        "screen",
        help=("Compute Piotroski F-Score for" " stock_master"),
    )
    p_screen.add_argument(
        "--tickers",
        default=None,
        help=("Comma-separated tickers (default: all)"),
    )
    p_screen.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )

    # analytics ----------------------------------------------------
    p_analytics = sub.add_parser(
        "analytics",
        help="Compute analysis summary (indicators)",
    )
    p_analytics.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )
    p_analytics.add_argument(
        "--force",
        action="store_true",
        help="Recompute even if analysed today",
    )

    # sentiment ----------------------------------------------------
    p_sentiment = sub.add_parser(
        "sentiment",
        help="Run LLM sentiment scoring",
    )
    p_sentiment.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )
    p_sentiment.add_argument(
        "--force",
        action="store_true",
        help="Re-score even if scored today",
    )

    # forecast -----------------------------------------------------
    p_forecast = sub.add_parser(
        "forecast",
        help="Run Prophet price forecasts",
    )
    p_forecast.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )
    p_forecast.add_argument(
        "--force",
        action="store_true",
        help="Skip 7-day freshness check",
    )

    # recommend ----------------------------------------------------
    p_recommend = sub.add_parser(
        "recommend",
        help=(
            "Generate LLM portfolio "
            "recommendations"
        ),
    )
    p_recommend.add_argument(
        "--scope",
        default="india",
        choices=["all", "india", "us"],
    )
    p_recommend.add_argument(
        "--force",
        action="store_true",
        help="Ignore 30-day freshness gate",
    )
    p_recommend.add_argument(
        "--user",
        default=None,
        help="Single user_id (default: all users)",
    )

    # indices ------------------------------------------------------
    sub.add_parser(
        "indices",
        help="Refresh market indices (VIX, etc.)",
    )

    # refresh (full pipeline) --------------------------------------
    p_refresh = sub.add_parser(
        "refresh",
        help="Full pipeline: data → analytics "
        "→ sentiment → piotroski",
    )
    p_refresh.add_argument(
        "--scope",
        default="all",
        choices=["all", "india", "us"],
    )
    p_refresh.add_argument(
        "--force",
        action="store_true",
        help="Force all steps (bypass freshness)",
    )
    p_refresh.add_argument(
        "--skip-forecast",
        action="store_true",
        help="Exclude Prophet forecasts",
    )

    return parser


# ------------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------------


async def _dispatch(args: argparse.Namespace) -> None:
    """Route to the appropriate async handler."""
    handlers = {
        "seed": _cmd_seed,
        "bulk": _cmd_bulk,
        "fundamentals": _cmd_fundamentals,
        "daily": _cmd_daily,
        "status": _cmd_status,
        "skipped": _cmd_skipped,
        "retry": _cmd_retry,
        "download": _cmd_download,
        "bulk-download": _cmd_bulk_download,
        "fill-gaps": _cmd_fill_gaps,
        "correct": _cmd_correct,
        "reset": _cmd_reset,
        "quarterly": _cmd_quarterly,
        "screen": _cmd_screen,
        "analytics": _cmd_analytics,
        "sentiment": _cmd_sentiment,
        "forecast": _cmd_forecast,
        "recommend": _cmd_recommend,
        "indices": _cmd_indices,
        "refresh": _cmd_refresh,
    }
    handler = handlers.get(args.command)
    if handler is None:
        _logger.error("Unknown command: %s", args.command)
        sys.exit(1)
    await handler(args)


# ------------------------------------------------------------------
# Command handlers
# ------------------------------------------------------------------


async def _cmd_seed(args: argparse.Namespace) -> None:
    from backend.pipeline.jobs.seed_universe import (
        seed_from_csv,
    )

    result = await seed_from_csv(
        args.csv,
        update=args.update,
    )
    _logger.info(
        "Seed complete: inserted=%d updated=%d "
        "skipped=%d tags_added=%d tags_removed=%d "
        "errors=%d",
        result["inserted"],
        result["updated"],
        result["skipped"],
        result["tags_added"],
        result["tags_removed"],
        len(result["errors"]),
    )
    if result["errors"]:
        for err in result["errors"]:
            _logger.warning("  %s", err)


async def _cmd_bulk(args: argparse.Namespace) -> None:
    from backend.pipeline.jobs.ohlcv import run_bulk

    result = await run_bulk(
        cursor_name=args.cursor,
        batch_size=args.batch_size,
    )
    _logger.info(
        "Bulk complete: cursor=%s status=%s "
        "processed=%d skipped=%d failed=%d",
        result["cursor"],
        result["status"],
        result["processed"],
        result["skipped"],
        result["failed"],
    )


async def _cmd_fundamentals(
    args: argparse.Namespace,
) -> None:
    from backend.pipeline.jobs.fundamentals import (
        run_fundamentals,
    )

    result = await run_fundamentals(
        cursor_name=args.cursor,
        batch_size=args.batch_size,
    )
    _logger.info(
        "Fundamentals complete: cursor=%s status=%s " "processed=%d failed=%d",
        result["cursor"],
        result["status"],
        result["processed"],
        result["failed"],
    )


async def _cmd_daily(
    _args: argparse.Namespace,
) -> None:
    from backend.pipeline.jobs.ohlcv import run_daily

    result = await run_daily()
    _logger.info(
        "Daily complete: processed=%d skipped=%d " "failed=%d",
        result["processed"],
        result["skipped"],
        result["failed"],
    )


async def _cmd_status(args: argparse.Namespace) -> None:
    from backend.db.engine import get_session_factory
    from backend.pipeline.cursor import (
        get_cursor,
        get_skipped,
    )

    factory = get_session_factory()
    async with factory() as session:
        cursor = await get_cursor(
            session,
            args.cursor,
        )
        if cursor is None:
            _logger.error(
                "Cursor not found: %s",
                args.cursor,
            )
            sys.exit(1)

        unresolved = await get_skipped(
            session,
            cursor_name=args.cursor,
            resolved=False,
        )

    total = cursor.total_tickers
    pos = cursor.last_processed_id
    pct = (pos / total * 100.0) if total > 0 else 0.0
    _logger.info("Cursor: %s", cursor.cursor_name)
    _logger.info("Status: %s", cursor.status)
    _logger.info(
        "Progress: %d/%d (%.1f%%)",
        pos,
        total,
        pct,
    )
    _logger.info("Batch size: %d", cursor.batch_size)
    _logger.info(
        "Skipped (unresolved): %d",
        len(unresolved),
    )


async def _cmd_skipped(args: argparse.Namespace) -> None:
    from backend.db.engine import get_session_factory
    from backend.pipeline.cursor import get_skipped

    factory = get_session_factory()
    async with factory() as session:
        records = await get_skipped(
            session,
            cursor_name=args.cursor,
            resolved=False,
        )

    if not records:
        _logger.info(
            "No skipped tickers for cursor %s",
            args.cursor,
        )
        return

    # Header
    _logger.info(
        "%-12s %-14s %-14s %8s  %s",
        "Ticker",
        "Job",
        "Category",
        "Attempts",
        "Last Attempt",
    )
    _logger.info("-" * 68)

    for rec in records:
        last = (
            rec.last_attempted_at.strftime(
                "%Y-%m-%d %H:%M",
            )
            if rec.last_attempted_at
            else "—"
        )
        _logger.info(
            "%-12s %-14s %-14s %8d  %s",
            rec.ticker,
            rec.job_type,
            rec.error_category,
            rec.attempts,
            last,
        )


async def _cmd_retry(args: argparse.Namespace) -> None:
    from sqlalchemy import select

    from backend.db.engine import get_session_factory
    from backend.db.models.stock_master import StockMaster
    from backend.pipeline.cursor import (
        get_retryable_skipped,
        get_skipped,
        mark_resolved,
    )
    from backend.pipeline.jobs.ohlcv import (
        _process_ticker,
    )
    from backend.pipeline.observability import (
        RateLimitTracker,
    )
    from backend.pipeline.router import get_ohlcv_source

    factory = get_session_factory()
    source = get_ohlcv_source("retry")  # NSE fallback
    rate_tracker = RateLimitTracker()

    # Get retryable records ----------------------------------------
    async with factory() as session:
        if args.ticker:
            records = await get_skipped(
                session,
                cursor_name=args.cursor,
                ticker=args.ticker,
                resolved=False,
            )
        else:
            records = await get_retryable_skipped(
                session,
                args.cursor,
                all_categories=args.all_categories,
            )

    if not records:
        _logger.info("No retryable tickers found")
        return

    _logger.info(
        "Retrying %d ticker(s)...",
        len(records),
    )

    ok = 0
    failed = 0

    for rec in records:
        # Look up StockMaster for this ticker
        async with factory() as session:
            result = await session.execute(
                select(StockMaster).where(
                    StockMaster.symbol == rec.ticker,
                )
            )
            stock = result.scalar_one_or_none()

        if stock is None:
            _logger.warning(
                "Stock not found: %s, skipping",
                rec.ticker,
            )
            failed += 1
            continue

        outcome = await _process_ticker(
            stock=stock,
            source=source,
            cursor_name=args.cursor,
            session_factory=factory,
            rate_tracker=rate_tracker,
        )

        if outcome in ("ok", "skipped"):
            async with factory() as session:
                await mark_resolved(session, rec.id)
            ok += 1
            _logger.info(
                "Retried OK: %s (%s)",
                rec.ticker,
                outcome,
            )
        else:
            failed += 1
            _logger.warning(
                "Retry failed: %s (%s)",
                rec.ticker,
                outcome,
            )

    _logger.info(
        "Retry complete: ok=%d failed=%d",
        ok,
        failed,
    )


async def _cmd_download(
    args: argparse.Namespace,
) -> None:
    import os
    import sys

    # download_nifty500 is a standalone script — import
    # its main function.
    script = os.path.join(
        os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)),
        ),
        "scripts",
        "download_nifty500.py",
    )
    # Add project root to path for the script
    proj_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)),
    )
    if proj_root not in sys.path:
        sys.path.insert(0, proj_root)

    from scripts.download_nifty500 import main as dl_main

    dl_main()


async def _cmd_bulk_download(
    args: argparse.Namespace,
) -> None:
    from scripts.bulk_download_ohlcv import run

    await run(
        batch=args.batch,
        tickers_csv=args.tickers,
        period=args.period,
    )


async def _cmd_fill_gaps(
    _args: argparse.Namespace,
) -> None:
    from backend.pipeline.jobs.fill_gaps import (
        fill_company_info_gaps,
    )

    result = fill_company_info_gaps()
    _logger.info(
        "Fill gaps: patched=%d skipped=%d " "no_master=%d total=%d",
        result["patched"],
        result["skipped"],
        result["no_master"],
        result["total"],
    )


async def _cmd_correct(args: argparse.Namespace) -> None:
    from sqlalchemy import select

    from backend.db.engine import get_session_factory
    from backend.db.models.stock_master import StockMaster
    from backend.pipeline.jobs.ohlcv import (
        _process_ticker,
    )
    from backend.pipeline.observability import (
        RateLimitTracker,
    )
    from backend.pipeline.router import get_ohlcv_source

    ticker = args.ticker.upper().strip()
    factory = get_session_factory()
    source = get_ohlcv_source("correct")  # NSE
    rate_tracker = RateLimitTracker()

    async with factory() as session:
        result = await session.execute(
            select(StockMaster).where(
                StockMaster.symbol == ticker,
            )
        )
        stock = result.scalar_one_or_none()

    if stock is None:
        _logger.error(
            "Stock not found in stock_master: %s",
            ticker,
        )
        return

    _logger.info(
        "Correcting %s via NSE (jugaad-data)...",
        ticker,
    )
    outcome = await _process_ticker(
        stock=stock,
        source=source,
        cursor_name=None,
        session_factory=factory,
        rate_tracker=rate_tracker,
    )
    _logger.info(
        "Correct %s: %s",
        ticker,
        outcome,
    )


async def _cmd_reset(args: argparse.Namespace) -> None:
    from backend.db.engine import get_session_factory
    from backend.pipeline.cursor import reset_cursor

    if not args.yes:
        _logger.warning(
            "This will reset cursor '%s' to position 0. "
            "Use --yes to confirm.",
            args.cursor,
        )
        try:
            answer = input(
                f"Reset cursor '{args.cursor}'? [y/N] ",
            )
        except (EOFError, KeyboardInterrupt):
            _logger.info("Aborted")
            return
        if answer.strip().lower() != "y":
            _logger.info("Aborted")
            return

    factory = get_session_factory()
    async with factory() as session:
        await reset_cursor(session, args.cursor)
    _logger.info("Cursor '%s' reset to 0", args.cursor)


async def _cmd_quarterly(
    args: argparse.Namespace,
) -> None:
    from backend.db.engine import get_session_factory
    from backend.db.models.stock_master import (
        StockMaster,
    )
    from backend.pipeline.cursor import (
        advance_cursor,
        create_cursor,
        get_cursor,
        get_next_batch,
        set_cursor_status,
    )
    from sqlalchemy import func, select
    from tools._stock_shared import _require_repo
    from tools.stock_data_tool import (
        _fetch_and_store_quarterly,
    )

    cursor_name = args.cursor
    batch_size = args.batch_size
    force = args.force
    factory = get_session_factory()
    repo = _require_repo()

    # Ensure cursor exists
    async with factory() as session:
        cursor = await get_cursor(
            session,
            cursor_name,
        )
        if cursor is None:
            total = await session.execute(
                select(func.count(StockMaster.id)).where(
                    StockMaster.is_active.is_(True),
                )
            )
            total_count = total.scalar() or 0
            cursor = await create_cursor(
                session,
                cursor_name,
                total_count,
                batch_size,
            )
        if cursor.status == "completed":
            _logger.info(
                "Cursor %s already completed",
                cursor_name,
            )
            return

    async with factory() as session:
        await set_cursor_status(
            session,
            cursor_name,
            "in_progress",
        )

    # Fetch batch
    async with factory() as session:
        batch = await get_next_batch(
            session,
            cursor_name,
        )

    if not batch:
        async with factory() as session:
            await set_cursor_status(
                session,
                cursor_name,
                "completed",
            )
        _logger.info(
            "Quarterly cursor %s completed",
            cursor_name,
        )
        return

    processed = 0
    failed = 0
    for stock in batch:
        ticker = stock.yf_ticker
        try:
            msg = _fetch_and_store_quarterly(
                ticker,
                repo,
                force=force,
            )
            _logger.info("quarterly | %s | %s", ticker, msg)
        except Exception:
            _logger.warning(
                "quarterly | %s failed",
                ticker,
                exc_info=True,
            )
            failed += 1
        processed += 1
        async with factory() as session:
            await advance_cursor(
                session,
                cursor_name,
                stock.id,
            )

    _logger.info(
        "Quarterly batch: cursor=%s processed=%d " "failed=%d",
        cursor_name,
        processed,
        failed,
    )


async def _cmd_screen(
    args: argparse.Namespace,
) -> None:
    from backend.pipeline.screener.screen import (
        run_screen,
    )

    tickers = None
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    result = await run_screen(tickers=tickers)
    _logger.info(
        "Screen: scored=%d skipped=%d failed=%d "
        "strong=%d moderate=%d weak=%d (%.1fs)",
        result["scored"],
        result["skipped"],
        result["failed"],
        result["strong"],
        result["moderate"],
        result["weak"],
        result["elapsed_s"],
    )


# ------------------------------------------------------------------
# Scheduler-backed commands (reuse executor functions)
# ------------------------------------------------------------------


def _make_cli_repo():
    """Create a StockRepository for CLI use."""
    from tools._stock_shared import _require_repo

    return _require_repo()


async def _cmd_analytics(
    args: argparse.Namespace,
) -> None:
    """Compute analysis summary for tickers."""
    import uuid

    from jobs.executor import execute_compute_analytics

    repo = _make_cli_repo()
    run_id = str(uuid.uuid4())
    scope = getattr(args, "scope", "all")
    force = getattr(args, "force", False)
    _logger.info(
        "Analytics: scope=%s force=%s run=%s",
        scope,
        force,
        run_id,
    )
    repo.append_scheduler_run({
        "run_id": run_id,
        "job_id": "cli",
        "job_name": f"CLI analytics ({scope})",
        "job_type": "compute_analytics",
        "scope": scope,
        "status": "running",
        "started_at": __import__(
            "datetime",
        ).datetime.now(
            __import__("datetime").timezone.utc,
        ),
        "completed_at": None,
        "duration_secs": None,
        "tickers_total": 0,
        "tickers_done": 0,
        "error_message": None,
        "trigger_type": "cli",
        "pipeline_run_id": None,
    })
    execute_compute_analytics(
        scope, run_id, repo, force=force,
    )
    _logger.info("Analytics complete: run=%s", run_id)


async def _cmd_sentiment(
    args: argparse.Namespace,
) -> None:
    """Run LLM sentiment scoring."""
    import uuid

    from jobs.executor import execute_run_sentiment

    repo = _make_cli_repo()
    run_id = str(uuid.uuid4())
    scope = getattr(args, "scope", "all")
    force = getattr(args, "force", False)
    _logger.info(
        "Sentiment: scope=%s force=%s run=%s",
        scope,
        force,
        run_id,
    )
    repo.append_scheduler_run({
        "run_id": run_id,
        "job_id": "cli",
        "job_name": f"CLI sentiment ({scope})",
        "job_type": "run_sentiment",
        "scope": scope,
        "status": "running",
        "started_at": __import__(
            "datetime",
        ).datetime.now(
            __import__("datetime").timezone.utc,
        ),
        "completed_at": None,
        "duration_secs": None,
        "tickers_total": 0,
        "tickers_done": 0,
        "error_message": None,
        "trigger_type": "cli",
        "pipeline_run_id": None,
    })
    execute_run_sentiment(
        scope, run_id, repo, force=force,
    )
    _logger.info("Sentiment complete: run=%s", run_id)


async def _cmd_forecast(
    args: argparse.Namespace,
) -> None:
    """Run Prophet price forecasts."""
    import uuid

    from jobs.executor import execute_run_forecasts

    repo = _make_cli_repo()
    run_id = str(uuid.uuid4())
    scope = getattr(args, "scope", "all")
    force = getattr(args, "force", False)
    _logger.info(
        "Forecast: scope=%s force=%s run=%s",
        scope,
        force,
        run_id,
    )
    repo.append_scheduler_run({
        "run_id": run_id,
        "job_id": "cli",
        "job_name": f"CLI forecast ({scope})",
        "job_type": "run_forecasts",
        "scope": scope,
        "status": "running",
        "started_at": __import__(
            "datetime",
        ).datetime.now(
            __import__("datetime").timezone.utc,
        ),
        "completed_at": None,
        "duration_secs": None,
        "tickers_total": 0,
        "tickers_done": 0,
        "error_message": None,
        "trigger_type": "cli",
        "pipeline_run_id": None,
    })
    execute_run_forecasts(
        scope, run_id, repo, force=force,
    )
    _logger.info("Forecast complete: run=%s", run_id)


async def _cmd_recommend(
    args: argparse.Namespace,
) -> None:
    """Generate LLM portfolio recommendations.

    Uses the same Smart Funnel pipeline as the
    scheduler and dashboard Refresh button.
    """
    import asyncio
    import time as _time
    import uuid as _uuid
    from datetime import date, datetime, timedelta, timezone

    from jobs.recommendation_engine import (
        stage1_prefilter,
        stage2_gap_analysis,
        stage3_llm_reasoning,
    )
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool
    from config import get_settings
    from backend.db.models.recommendation import (
        Recommendation as RecModel,
        RecommendationRun as RunModel,
    )

    scope = getattr(args, "scope", "india")
    force = getattr(args, "force", False)

    _logger.info(
        "Recommend: scope=%s force=%s",
        scope,
        force,
    )

    # Stage 1
    t0 = _time.monotonic()
    candidates = stage1_prefilter(scope=scope)
    if candidates.empty:
        _logger.warning("No candidates — aborting")
        return
    _logger.info(
        "Stage 1: %d candidates (%.1fs)",
        len(candidates),
        _time.monotonic() - t0,
    )

    # Find users with portfolios
    from db.duckdb_engine import query_iceberg_df

    user_df = query_iceberg_df(
        "stocks.portfolio_transactions",
        "SELECT DISTINCT user_id "
        "FROM portfolio_transactions",
    )
    if user_df.empty:
        _logger.warning("No users with portfolios")
        return

    user_ids = user_df["user_id"].tolist()
    if getattr(args, "user", None):
        user_ids = [args.user]

    _logger.info(
        "Processing %d user(s)",
        len(user_ids),
    )

    eng = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(
        eng, class_=AsyncSession,
    )

    _freshness = 30
    generated = 0
    skipped = 0

    for uid in user_ids:
        # Freshness gate
        if not force:
            async with factory() as s:
                from backend.db.pg_stocks import (
                    get_latest_recommendation_run,
                )
                latest = (
                    await get_latest_recommendation_run(
                        s, uid, scope=scope,
                    )
                )
            if latest:
                ca = latest.get("created_at")
                if ca:
                    if isinstance(ca, str):
                        ca = datetime.fromisoformat(ca)
                    if ca.tzinfo is None:
                        ca = ca.replace(
                            tzinfo=timezone.utc,
                        )
                    age = (
                        datetime.now(timezone.utc) - ca
                    )
                    if age.days < _freshness:
                        _logger.info(
                            "User %s: fresh (%dd) "
                            "— skip",
                            uid[:8],
                            age.days,
                        )
                        skipped += 1
                        continue

        # Stage 2 + 3
        s2 = stage2_gap_analysis(
            uid, candidates, scope=scope,
        )
        if not s2.get(
            "portfolio_summary", {},
        ).get("total_holdings"):
            _logger.info(
                "User %s: no %s holdings — skip",
                uid[:8],
                scope,
            )
            skipped += 1
            continue

        s3 = stage3_llm_reasoning(s2)
        recs = s3.get("recommendations", [])

        # Persist
        run_id = str(_uuid.uuid4())
        cand_map = {
            c["ticker"]: c
            for c in s2.get("candidates", [])
        }
        async with factory() as s:
            s.add(RunModel(
                run_id=run_id,
                user_id=uid,
                run_date=date.today(),
                run_type="cli",
                scope=scope,
                portfolio_snapshot=s2.get(
                    "portfolio_summary", {},
                ),
                health_score=s3.get(
                    "health_score", 0,
                ),
                health_label=s3.get(
                    "health_label", "unknown",
                ),
                health_assessment=s3.get(
                    "portfolio_health_assessment",
                ),
                candidates_scanned=len(candidates),
                candidates_passed=len(
                    s2.get("candidates", []),
                ),
                llm_model=s3.get("llm_model"),
                llm_tokens_used=s3.get(
                    "llm_tokens_used",
                ),
            ))
            for r in recs:
                ticker = r.get("ticker")
                c = cand_map.get(ticker, {})
                s.add(RecModel(
                    id=str(_uuid.uuid4()),
                    run_id=run_id,
                    tier=r.get("tier", "discovery"),
                    category=r.get(
                        "category", "general",
                    ),
                    ticker=ticker,
                    action=r.get("action", "hold"),
                    severity=r.get(
                        "severity", "low",
                    ),
                    rationale=r.get(
                        "rationale", "",
                    ),
                    expected_impact=r.get(
                        "expected_impact",
                    ),
                    data_signals=r.get(
                        "data_signals", {},
                    ),
                    price_at_rec=(
                        r.get("price_at_rec")
                        or c.get("current_price")
                    ),
                    target_price=(
                        r.get("target_price")
                        or c.get("target_price")
                    ),
                    expected_return_pct=(
                        r.get("expected_return_pct")
                        or c.get("forecast_3m_pct")
                    ),
                    status="active",
                ))
            await s.commit()

        _logger.info(
            "User %s: %d recs generated",
            uid[:8],
            len(recs),
        )
        generated += 1

    await eng.dispose()
    elapsed = _time.monotonic() - t0
    _logger.info(
        "Recommend done: %d generated, "
        "%d skipped (%.1fs)",
        generated,
        skipped,
        elapsed,
    )


async def _cmd_indices(
    args: argparse.Namespace,
) -> None:
    """Refresh market indices."""
    from jobs.gap_filler import refresh_market_indices

    count = refresh_market_indices()
    _logger.info("Market indices: %d rows", count)


async def _cmd_refresh(
    args: argparse.Namespace,
) -> None:
    """Full pipeline: data → analytics → sentiment → piotroski.

    Mirrors the scheduler pipeline chain but runs
    from the CLI sequentially.
    """
    import uuid

    from jobs.executor import (
        execute_compute_analytics,
        execute_data_refresh,
        execute_run_forecasts,
        execute_run_sentiment,
    )

    repo = _make_cli_repo()
    scope = getattr(args, "scope", "all")
    force = getattr(args, "force", False)
    skip_fc = getattr(args, "skip_forecast", False)

    steps = [
        ("data_refresh", execute_data_refresh),
        (
            "compute_analytics",
            execute_compute_analytics,
        ),
        ("run_sentiment", execute_run_sentiment),
    ]
    if not skip_fc:
        steps.append(
            ("run_forecasts", execute_run_forecasts),
        )

    pipeline_run_id = str(uuid.uuid4())
    _logger.info(
        "Refresh pipeline: scope=%s force=%s "
        "steps=%d run=%s",
        scope,
        force,
        len(steps),
        pipeline_run_id,
    )

    for i, (job_type, executor_fn) in enumerate(
        steps, 1,
    ):
        run_id = str(uuid.uuid4())
        _logger.info(
            "Step %d/%d: %s (run=%s)",
            i,
            len(steps),
            job_type,
            run_id,
        )
        repo.append_scheduler_run({
            "run_id": run_id,
            "job_id": "cli",
            "job_name": f"CLI {job_type} ({scope})",
            "job_type": job_type,
            "scope": scope,
            "status": "running",
            "started_at": __import__(
                "datetime",
            ).datetime.now(
                __import__("datetime").timezone.utc,
            ),
            "completed_at": None,
            "duration_secs": None,
            "tickers_total": 0,
            "tickers_done": 0,
            "error_message": None,
            "trigger_type": "cli",
            "pipeline_run_id": pipeline_run_id,
        })
        try:
            executor_fn(
                scope, run_id, repo, force=force,
            )
            _logger.info(
                "Step %d/%d: %s complete",
                i,
                len(steps),
                job_type,
            )
        except Exception as exc:
            _logger.error(
                "Step %d/%d: %s failed: %s",
                i,
                len(steps),
                job_type,
                exc,
            )
            _logger.info(
                "Pipeline aborted at step %d", i,
            )
            return

    # Piotroski (async, always runs)
    from backend.pipeline.screener.screen import (
        run_screen,
    )

    _logger.info("Step final: Piotroski F-Score")
    result = await run_screen()
    _logger.info(
        "Piotroski: scored=%d strong=%d "
        "moderate=%d weak=%d (%.1fs)",
        result["scored"],
        result["strong"],
        result["moderate"],
        result["weak"],
        result["elapsed_s"],
    )
    _logger.info("Refresh pipeline complete")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    """Parse args and dispatch to the appropriate command."""
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s %(levelname)s " "%(name)s %(message)s"),
    )

    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    asyncio.run(_dispatch(args))


if __name__ == "__main__":
    main()
