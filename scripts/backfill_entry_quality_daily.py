"""One-time backfill: populate ``stocks.entry_quality_daily`` (EQD)
for the last N trading days (PRE-2).

The recurring ``entry_quality_snapshot`` job only ever computes an
*as-of-today* snapshot, so EQD has no history before the job first
ran. This script reuses the exact same compute + idempotent scoped
upsert as the daily job, driving it once per historical trading day
via the new ``as_of`` payload param — so each row is recomputed as
it would have been on that date (``trade_date`` follows the last
in-window bar).

Bounded to the last 60 trading days by default (per product call —
the deeper history has low modelling value). Safe to re-run: each
day's write scoped-deletes its own ``(ticker, trade_date)`` rows
first, so a second pass overwrites rather than duplicates.

Trading days are derived from ``^NSEI`` OHLCV (no holiday calendar
needed).

Usage::

    docker compose exec backend python \
        scripts/backfill_entry_quality_daily.py [--days 60] \
        [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from backend.db.duckdb_engine import query_iceberg_table
from backend.jobs.entry_quality_snapshot import (
    run_entry_quality_snapshot_job,
)

_logger = logging.getLogger(__name__)

_DEFAULT_DAYS = 60


def _last_n_trading_days(n: int, as_of: date) -> list[date]:
    """Last ``n`` trading days on/before ``as_of``, ascending.

    Derived from ^NSEI OHLCV — the index trades every session, so
    its distinct dates ARE the NSE trading calendar."""
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT DISTINCT date FROM ohlcv "
        "WHERE ticker = '^NSEI' AND date <= ? "
        "ORDER BY date DESC LIMIT ?",
        [as_of.isoformat(), n],
    )
    days = [r["date"] for r in rows]
    days = [
        d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        for d in days
    ]
    return sorted(days)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    targets = _last_n_trading_days(args.days, date.today())
    if args.limit is not None:
        targets = targets[-args.limit:]

    _logger.info(
        "EQD backfill: %d trading days %s..%s dry_run=%s",
        len(targets),
        targets[0] if targets else "-",
        targets[-1] if targets else "-",
        args.dry_run,
    )
    if args.dry_run:
        for d in targets:
            _logger.info("  would backfill as_of=%s", d.isoformat())
        return

    total = 0
    for d in targets:
        res = run_entry_quality_snapshot_job({"as_of": d.isoformat()})
        written = int(res.get("rows_written", 0))
        total += written
        _logger.info("as_of=%s rows_written=%d", d.isoformat(), written)
    _logger.info("EQD backfill complete: %d rows across %d days",
                 total, len(targets))


if __name__ == "__main__":
    main()
