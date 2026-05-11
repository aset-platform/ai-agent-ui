"""Backfill ``stocks.daily_factors`` over a date range.

Idempotent via the repo's NaN-replaceable upsert.

Usage::

    docker compose exec backend \
      python scripts/backfill_factors.py 2026-02-08 2026-05-08
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

from backend.algo.factors.compute_job import run_compute_job

_logger = logging.getLogger(__name__)


def main(start_iso: str, end_iso: str) -> None:
    """Backfill the full window in a single bulk run.

    Calls ``run_compute_job(as_of=end, days=N)`` once — compute_job
    bulk-loads the whole universe's OHLCV in one DuckDB read,
    iterates the per-ticker compute in memory across all days,
    and writes a single Iceberg commit at the end.

    The previous implementation called run_compute_job day-by-day
    which forced a fresh OHLCV bulk-read for every backfill day
    (180 reads instead of 1). On a 6-month / 800-ticker window
    that dropped wall time from ~10 hours to ~10–30 minutes.
    """
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    n_days = (end - start).days + 1
    _logger.info(
        "Bulk backfill: %s → %s (%d days)", start, end, n_days,
    )
    try:
        n = run_compute_job(as_of=end, days=n_days)
        _logger.info(
            "Backfill total: %d rows across %d days",
            n, n_days,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception(
            "Bulk backfill failed for %s → %s: %s",
            start, end, exc,
        )
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 3:
        sys.stderr.write(
            "usage: python scripts/backfill_factors.py "
            "<start YYYY-MM-DD> <end YYYY-MM-DD>\n"
        )
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
