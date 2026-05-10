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
    """Replay run_compute_job day-by-day across the range."""
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    cur = start
    total = 0
    while cur <= end:
        try:
            n = run_compute_job(as_of=cur, days=1)
            _logger.info("Backfilled %s: %d rows", cur, n)
            total += n
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "Backfill failed for %s: %s", cur, exc,
            )
        cur += timedelta(days=1)
    _logger.info(
        "Backfill total: %d rows across %d days",
        total, (end - start).days + 1,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 3:
        sys.stderr.write(
            "usage: python scripts/backfill_factors.py "
            "<start YYYY-MM-DD> <end YYYY-MM-DD>\n"
        )
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
