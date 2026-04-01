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
) -> None:
    """Refresh all tickers matching *scope*.

    Calls ``run_full_refresh(ticker)`` for each ticker
    in the registry, filtering by market scope.
    Updates the scheduler run record as tickers complete.
    """
    from dashboard.services.stock_refresh import (
        run_full_refresh,
    )

    registry = repo.get_all_registry()
    tickers = list(registry.keys())

    if scope == "india":
        tickers = [
            t for t in tickers
            if t.endswith(".NS") or t.endswith(".BO")
        ]
    elif scope == "us":
        tickers = [
            t for t in tickers
            if not (
                t.endswith(".NS") or t.endswith(".BO")
            )
        ]

    total = len(tickers)
    repo.update_scheduler_run(
        run_id, {"tickers_total": total},
    )

    done = 0
    errors: list[str] = []

    for ticker in tickers:
        try:
            _logger.info(
                "[scheduler] Refreshing %s (%d/%d)",
                ticker,
                done + 1,
                total,
            )
            result = run_full_refresh(ticker)
            if not result.success:
                errors.append(
                    f"{ticker}: {result.error}",
                )
        except Exception as exc:
            _logger.warning(
                "[scheduler] %s refresh failed: %s",
                ticker,
                exc,
            )
            errors.append(f"{ticker}: {exc}")
        done += 1
        repo.update_scheduler_run(
            run_id, {"tickers_done": done},
        )

    # Final status
    status = "success" if not errors else "failed"
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
