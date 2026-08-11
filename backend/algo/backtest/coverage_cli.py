"""On-demand 15m coverage-gap report (PRE-5).

Runs the tested ``coverage_gap_summary`` over a universe and prints
tickers with real missing full trading days — surfacing ingest
dropouts (a ticker that fell out of the ingest universe) that a
24-of-25-bar "partial" day would not.

    docker compose exec backend python -m \
        backend.algo.backtest.coverage_cli [--full] [--days 90]

Default universe is the F&O model universe; ``--full`` reports over
every ticker with any 15m history.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from backend.algo.backtest.coverage import coverage_gap_summary

_logger = logging.getLogger(__name__)


def _full_universe() -> list[str]:
    from backend.db.duckdb_engine import query_iceberg_table

    rows = query_iceberg_table(
        "stocks.intraday_bars",
        "SELECT DISTINCT ticker FROM intraday_bars WHERE interval_sec=900",
        [],
    )
    return sorted(r["ticker"] for r in rows)


def _fno_universe() -> list[str]:
    from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
        load_fno_universe,
    )

    return load_fno_universe()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args(argv)

    tickers = _full_universe() if args.full else _fno_universe()
    summary = coverage_gap_summary(
        tickers=tickers,
        as_of=date.today(),
        lookback_days=args.days,
    )

    scope = "full" if args.full else "fno"
    _logger.info(
        "15m coverage gap report | scope=%s as_of=%s lookback=%dd "
        "reference_days=%d tickers=%d with_gaps=%d",
        scope, summary.as_of, summary.lookback_days,
        summary.reference_days, summary.total_tickers,
        len(summary.gap_tickers),
    )
    for g in summary.gap_tickers:
        first = g.missing_dates[0] if g.missing_dates else "-"
        last = g.missing_dates[-1] if g.missing_dates else "-"
        _logger.info(
            "  %s: present=%d/%d missing=%d span=%s..%s",
            g.ticker, g.present_days, g.expected_days,
            len(g.missing_dates), first, last,
        )
    if not summary.gap_tickers:
        _logger.info("  (no coverage gaps)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
