"""On-demand feature backfill helper (ASETPLTFRM-402 / FE-3).

The daily compute step
(:mod:`backend.algo.jobs.intraday_features_daily_compute`) is the
primary writer of ``stocks.intraday_features``. This module exposes
:func:`backfill_features_window` so that ad-hoc / on-demand callers
— most notably FE-4's
``load_intraday_features_window`` on a partition-chunk miss — can
fill a specific window without waiting for the daily cron.

Internally we delegate to the daily compute job's shared async
entrypoint with an explicit ``tickers`` + ``period_start`` /
``period_end`` payload. The job already implements the
NaN-replaceable upsert, per-batch error isolation, structured
stats roll-up, AND the FE-4 partition-chunk Redis cache
invalidation (called from ``_write_features_batch`` after a
successful ``retry_iceberg_op``) — the on-demand backfill path
benefits from the same guarantees for free.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from backend.algo.features.version import FEATURE_SET_VERSION

_logger = logging.getLogger(__name__)


async def backfill_features_window(
    tickers: list[str],
    interval_sec: int,
    period_start: date,
    period_end: date,
    *,
    feature_set_version: str = FEATURE_SET_VERSION,
    batch_size: int = 50,
) -> dict[str, Any]:
    """Compute + persist features for an explicit window.

    Mirrors the daily compute job's stats shape. ``tickers`` must
    be a non-empty list — there is no implicit universe fallback
    on this path; callers know exactly which symbols they need.

    Args:
        tickers: Symbols to compute (e.g. ``["RELIANCE.NS"]``).
        interval_sec: Bar cadence — one of ``(900, 300, 60)``.
        period_start, period_end: Inclusive ISO window bounds.
        feature_set_version: Stamped onto every emitted row.
        batch_size: Tickers per upsert commit.

    Returns:
        The structured stats dict from the daily compute job.
    """
    from backend.algo.jobs.intraday_features_daily_compute import (
        run_intraday_features_daily_compute_job,
    )

    if not tickers:
        _logger.warning(
            "[features-backfill] called with empty ticker list — " "no-op",
        )
        return {
            "status": "skipped_empty_universe",
            "universe_size": 0,
            "tickers_processed": 0,
            "tickers_failed": 0,
            "rows_written": 0,
            "feature_set_version": feature_set_version,
            "window": [
                period_start.isoformat(),
                period_end.isoformat(),
            ],
            "interval_sec": interval_sec,
            "failures": [],
        }

    payload = {
        "tickers": list(tickers),
        "interval_sec": int(interval_sec),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "feature_set_version": feature_set_version,
        "batch_size": int(batch_size),
    }
    _logger.info(
        "[features-backfill] tickers=%d interval_sec=%d "
        "window=%s..%s version=%s",
        len(tickers),
        interval_sec,
        period_start.isoformat(),
        period_end.isoformat(),
        feature_set_version,
    )
    return await run_intraday_features_daily_compute_job(payload)
