"""CLI entry point for the stock data pipeline.

Usage::

    python -m backend.pipeline.runner <command> [args]

Commands: seed, bulk, fundamentals, daily, status, skipped,
retry, reset.
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

    # daily --------------------------------------------------------
    sub.add_parser(
        "daily",
        help="Run daily OHLCV delta",
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
    sub.add_parser(
        "fill-gaps",
        help="Patch empty company_info from stock_master",
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
        "screen": _cmd_screen,
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
