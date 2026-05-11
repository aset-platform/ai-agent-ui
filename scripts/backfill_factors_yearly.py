"""Chunked backfill of stocks.daily_factors year-by-year.

The single-shot `backfill_factors.py` works for a few months but
loads the full ticker × bar matrix into memory. For an 8-year
backfill (~2.4M rows × 800 tickers) we chunk by calendar year so
each iteration peaks at ~300K rows / ~2-3 min.

Usage::

    docker compose exec backend \
      python scripts/backfill_factors_yearly.py 2018-01-01 2025-11-11

The script logs progress per year and is idempotent — re-running
overwrites the same (ticker, bar_date) keys via the repo's
NaN-replaceable upsert.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date

from backend.algo.factors.compute_job import run_compute_job

_logger = logging.getLogger(__name__)


def _year_chunks(
    start: date, end: date,
) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        chunk_end = date(cur.year, 12, 31)
        if chunk_end > end:
            chunk_end = end
        chunks.append((cur, chunk_end))
        cur = date(cur.year + 1, 1, 1)
    return chunks


def main(start_iso: str, end_iso: str) -> None:
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    chunks = _year_chunks(start, end)
    _logger.info(
        "Yearly backfill: %s → %s (%d chunks)",
        start, end, len(chunks),
    )
    grand_total = 0
    grand_t0 = time.time()
    for i, (cs, ce) in enumerate(chunks, 1):
        n_days = (ce - cs).days + 1
        t0 = time.time()
        _logger.info(
            "[%d/%d] %s → %s (%d days)",
            i, len(chunks), cs, ce, n_days,
        )
        try:
            n = run_compute_job(as_of=ce, days=n_days)
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "[%d/%d] chunk failed (%s → %s): %s",
                i, len(chunks), cs, ce, exc,
            )
            sys.exit(1)
        elapsed = time.time() - t0
        grand_total += n
        _logger.info(
            "[%d/%d] wrote %d rows in %.1fs (running total %d)",
            i, len(chunks), n, elapsed, grand_total,
        )
    grand_elapsed = time.time() - grand_t0
    _logger.info(
        "Backfill DONE: %d rows total across %d chunks in %.1fs",
        grand_total, len(chunks), grand_elapsed,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    if len(sys.argv) != 3:
        sys.stderr.write(
            "usage: python scripts/backfill_factors_yearly.py "
            "<start YYYY-MM-DD> <end YYYY-MM-DD>\n"
        )
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
