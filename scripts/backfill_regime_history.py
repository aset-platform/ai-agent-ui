"""Backfill stocks.regime_history by replaying classifier_job over
a date range.

Usage::

    docker compose exec backend \
      python scripts/backfill_regime_history.py 2026-04-09 2026-05-09
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

from backend.algo.regime.classifier_job import run_classifier

_logger = logging.getLogger(__name__)


def main(start_iso: str, end_iso: str) -> None:
    """Replay classifier_job.run_classifier daily across the range."""
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    cur = start
    ok = 0
    failed = 0
    while cur <= end:
        try:
            row = run_classifier(as_of=cur)
            _logger.info(
                "Backfilled %s -> %s (stress=%s)",
                cur, row.regime_label, row.stress_prob,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            _logger.error("Backfill failed for %s: %s", cur, exc)
            failed += 1
        cur += timedelta(days=1)
    _logger.info("Backfill done: ok=%d failed=%d", ok, failed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 3:
        sys.stderr.write(
            "usage: python scripts/backfill_regime_history.py "
            "<start YYYY-MM-DD> <end YYYY-MM-DD>\n"
        )
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
