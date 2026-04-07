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
import time
from collections.abc import Callable
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

JOB_EXECUTORS: dict[
    str, Callable[..., None]
] = {}


def register_job(job_type: str):
    """Decorator to register a job executor function."""

    def wrapper(fn: Callable[..., None]):
        JOB_EXECUTORS[job_type] = fn
        _logger.info(
            "Registered job executor: %s", job_type,
        )
        return fn

    return wrapper


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
    """Refresh all tickers matching *scope*.

    Calls ``run_full_refresh(ticker)`` for each ticker
    in the registry, filtering by market scope.
    Updates the scheduler run record as tickers complete.

    Args:
        cancel_event: Optional ``threading.Event``.
            When set, the loop stops after the current
            ticker and marks the run as ``cancelled``.
    """
    from dashboard.services.stock_refresh import (
        run_full_refresh,
    )

    registry = repo.get_all_registry()
    tickers = list(registry.keys())

    def _is_india(t: str) -> bool:
        if t.endswith((".NS", ".BO")):
            return True
        mkt = registry.get(t, {}).get("market", "")
        return mkt.upper() in ("NSE", "BSE", "INDIA")

    if scope == "india":
        tickers = [t for t in tickers if _is_india(t)]
    elif scope == "us":
        tickers = [
            t for t in tickers if not _is_india(t)
        ]

    # Resolve canonical symbols to yfinance tickers.
    # For Indian stocks without .NS suffix, append .NS.
    # Registry already tells us the market.
    yf_map: dict[str, str] = {}
    for t in tickers:
        if t.endswith((".NS", ".BO")):
            continue  # already has suffix
        meta = registry.get(t, {})
        mkt = meta.get("market", "")
        if mkt.upper() in ("NSE", "BSE", "INDIA"):
            yf_map[t] = f"{t}.NS"

    total = len(tickers)
    repo.update_scheduler_run(
        run_id, {"tickers_total": total},
    )

    done = 0
    errors: list[str] = []

    cancelled = False
    for ticker in tickers:
        # Check for cancellation before each ticker
        if cancel_event and cancel_event.is_set():
            _logger.info(
                "[scheduler] Run %s cancelled at %d/%d",
                run_id, done, total,
            )
            cancelled = True
            break

        # Use yf_ticker for yfinance-based refresh
        refresh_ticker = yf_map.get(ticker, ticker)
        try:
            _logger.info(
                "[scheduler] Refreshing %s (%d/%d)",
                refresh_ticker,
                done + 1,
                total,
            )
            result = run_full_refresh(refresh_ticker)
            if not result.success:
                errors.append(
                    f"{refresh_ticker}: {result.error}",
                )
        except Exception as exc:
            _logger.warning(
                "[scheduler] %s refresh failed: %s",
                refresh_ticker,
                exc,
            )
            errors.append(f"{refresh_ticker}: {exc}")
        done += 1
        repo.update_scheduler_run(
            run_id, {"tickers_done": done},
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
            "error_message": (
                "; ".join(errors[:5]) if errors
                else None
            ),
        },
    )
    _logger.info(
        "[scheduler] Run %s finished: %s (%d/%d)",
        run_id,
        status,
        done,
        total,
    )
