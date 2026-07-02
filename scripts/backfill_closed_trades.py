"""One-time backfill: populate algo.closed_trades with every
historical closed trade before the recurring daily rollup job
takes over.

Reuses the exact same pairing + idempotent-upsert code as the
daily job (backend/algo/jobs/closed_trades_rollup.py), just with
a much larger (but still bounded — no full-table scan) lookback
window: 3650 days (~10 years), comfortably covering this
platform's full trading history. Safe to re-run — the unique
(buy_event_id, sell_event_id) constraint means re-running finds
0 new rows on a second pass.

Usage::

    docker compose exec backend python \
        scripts/backfill_closed_trades.py
"""
from __future__ import annotations

import logging

from backend.algo.jobs.closed_trades_rollup import (
    run_closed_trades_rollup_job,
)

_logger = logging.getLogger(__name__)

_BACKFILL_WINDOW_DAYS = 3650


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    result = run_closed_trades_rollup_job(
        {"window_days": _BACKFILL_WINDOW_DAYS},
    )
    _logger.info("backfill result: %s", result)


if __name__ == "__main__":
    main()
