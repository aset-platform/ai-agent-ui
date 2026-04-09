"""Extensible job executor registry.

Register new job types with ``@register_job("type_name")``.
The decorated function receives ``(scope, run_id, repo)``
and is responsible for doing the work + updating the run
record's ``tickers_done`` counter.

Example::

    @register_job("gap_fill")
    def execute_gap_fill(scope, run_id, repo):
        ...
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import datetime, timezone

from market_utils import is_indian_market

_iceberg_write_lock = threading.Lock()

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

JOB_EXECUTORS: dict[str, Callable[..., None]] = {}


def register_job(job_type: str):
    """Decorator to register a job executor function."""

    def wrapper(fn: Callable[..., None]):
        JOB_EXECUTORS[job_type] = fn
        _logger.info(
            "Registered job executor: %s",
            job_type,
        )
        return fn

    return wrapper


# ------------------------------------------------------------------
# Parallel fetch infrastructure
# ------------------------------------------------------------------


def _parallel_fetch(
    tickers: list[str],
    fetch_fn,
    repo,
    run_id: str,
    cancel_event=None,
    max_workers: int = 5,
) -> tuple[int, list[str], bool]:
    """Fetch tickers in parallel with progress tracking.

    Args:
        tickers: List of ticker symbols.
        fetch_fn: Callable(ticker) that does the work.
            May raise on failure.
        repo: StockRepository for progress updates.
        run_id: Scheduler run ID.
        cancel_event: Threading event for cancellation.
        max_workers: Concurrent fetch threads.

    Returns:
        (done_count, error_list, cancelled) tuple.
    """
    done = 0
    errors: list[str] = []
    cancelled = False
    lock = threading.Lock()

    with ThreadPoolExecutor(
        max_workers=max_workers,
    ) as pool:
        future_map = {pool.submit(fetch_fn, t): t for t in tickers}
        for future in as_completed(future_map):
            if cancel_event and cancel_event.is_set():
                pool.shutdown(
                    wait=False,
                    cancel_futures=True,
                )
                cancelled = True
                break
            ticker = future_map[future]
            try:
                future.result()
            except Exception as exc:
                _logger.warning(
                    "[scheduler] %s failed: %s",
                    ticker,
                    exc,
                )
                with lock:
                    errors.append(
                        f"{ticker}: {exc}",
                    )
            with lock:
                done += 1
            repo.update_scheduler_run(
                run_id,
                {"tickers_done": done},
            )

    return done, errors, cancelled


# ------------------------------------------------------------------
# Built-in: data_refresh
# ------------------------------------------------------------------


@register_job("data_refresh")
def execute_data_refresh(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
) -> None:
    """Batch refresh all tickers matching *scope*.

    Uses ``batch_data_refresh`` for parallel yfinance
    fetch with sequential Iceberg writes (no commit
    conflicts). Skips technical analysis, sentiment,
    and Prophet forecast (compute-heavy; handled by
    interactive ``run_full_refresh`` or separate jobs).

    Args:
        cancel_event: Optional ``threading.Event``.
            When set, the fetch phase stops and marks
            the run as ``cancelled``.
    """
    from backend.jobs.batch_refresh import (
        batch_data_refresh,
    )

    registry = repo.get_all_registry()
    tickers = list(registry.keys())

    if scope == "india":
        tickers = [
            t
            for t in tickers
            if is_indian_market(
                t,
                registry.get(t, {}).get("market"),
            )
        ]
    elif scope == "us":
        tickers = [
            t
            for t in tickers
            if not is_indian_market(
                t,
                registry.get(t, {}).get("market"),
            )
        ]

    # Resolve canonical symbols to yfinance tickers
    yf_tickers = []
    for t in tickers:
        if t.endswith((".NS", ".BO")):
            yf_tickers.append(t)
        else:
            meta = registry.get(t, {})
            mkt = meta.get("market", "")
            if mkt.upper() in (
                "NSE",
                "BSE",
                "INDIA",
            ):
                yf_tickers.append(f"{t}.NS")
            else:
                yf_tickers.append(t)

    batch_data_refresh(
        yf_tickers,
        repo,
        run_id,
        cancel_event=cancel_event,
        max_workers=5,
    )


# ------------------------------------------------------------------
# Built-in: fetch_quarterly
# ------------------------------------------------------------------


@register_job("fetch_quarterly")
def execute_fetch_quarterly(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
) -> None:
    """Fetch quarterly financial statements for tickers.

    Calls ``_fetch_and_store_quarterly(ticker, force=True)``
    for each ticker, populating balance sheet fields
    (current_assets, current_liabilities,
    shares_outstanding).
    """
    from tools.stock_data_tool import (
        _fetch_and_store_quarterly,
    )
    from tools._stock_shared import _require_repo

    stock_repo = _require_repo()
    registry = repo.get_all_registry()
    tickers = list(registry.keys())

    if scope == "india":
        tickers = [
            t
            for t in tickers
            if is_indian_market(
                t,
                registry.get(t, {}).get("market"),
            )
        ]
    elif scope == "us":
        tickers = [
            t
            for t in tickers
            if not is_indian_market(
                t,
                registry.get(t, {}).get("market"),
            )
        ]

    # Resolve canonical symbols to yfinance tickers
    yf_map: dict[str, str] = {}
    for t in tickers:
        if t.endswith((".NS", ".BO")):
            continue
        meta = registry.get(t, {})
        mkt = meta.get("market", "")
        if mkt.upper() in ("NSE", "BSE", "INDIA"):
            yf_map[t] = f"{t}.NS"

    total = len(tickers)
    repo.update_scheduler_run(
        run_id,
        {"tickers_total": total},
    )

    def _quarterly_one(ticker):
        yf_ticker = yf_map.get(ticker, ticker)
        _logger.info(
            "[scheduler] Quarterly fetch %s",
            yf_ticker,
        )
        with _iceberg_write_lock:
            result = _fetch_and_store_quarterly(
                yf_ticker,
                stock_repo,
                force=True,
            )
        if result.startswith("Error"):
            raise RuntimeError(result)

    done, errors, cancelled = _parallel_fetch(
        tickers,
        _quarterly_one,
        repo,
        run_id,
        cancel_event,
        max_workers=5,
    )

    # Final status
    if cancelled:
        status = "cancelled"
    elif errors:
        status = "failed"
    else:
        status = "success"
    now = datetime.now(timezone.utc)
    repo.update_scheduler_run(
        run_id,
        {
            "status": status,
            "completed_at": now,
            "tickers_done": done,
            "error_message": ("; ".join(errors[:5]) if errors else None),
        },
    )
    _logger.info(
        "[scheduler] Run %s finished: %s (%d/%d)",
        run_id,
        status,
        done,
        total,
    )
