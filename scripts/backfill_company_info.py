#!/usr/bin/env python
"""One-time backfill: fetch company info for all registered
tickers that have no data in the ``company_info`` table.

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    PYTHONPATH=backend python scripts/backfill_company_info.py
"""

import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_logger = logging.getLogger(__name__)


def main() -> None:
    import yfinance as yf
    from tools._stock_shared import _require_repo

    repo = _require_repo()
    registry = repo.get_all_registry()
    if not registry:
        _logger.info("No tickers in registry.")
        return

    all_tickers = list(registry.keys())
    _logger.info("Registry has %d tickers", len(all_tickers))

    # Find tickers missing company info.
    info_df = repo.get_company_info_batch(all_tickers)
    existing = set()
    if info_df is not None and not info_df.empty:
        existing = set(info_df["ticker"].unique())

    missing = [t for t in all_tickers if t not in existing]
    _logger.info(
        "%d tickers already have info, " "%d need backfill",
        len(existing),
        len(missing),
    )

    if not missing:
        _logger.info("Nothing to backfill.")
        return

    ok = 0
    fail = 0
    for i, ticker in enumerate(missing, 1):
        try:
            info = yf.Ticker(ticker).info
            if info and info.get("longName"):
                repo.insert_company_info(ticker, info)
                _logger.info(
                    "[%d/%d] %s — %s",
                    i,
                    len(missing),
                    ticker,
                    info.get("longName", "?"),
                )
                ok += 1
            else:
                _logger.warning(
                    "[%d/%d] %s — no info from yfinance",
                    i,
                    len(missing),
                    ticker,
                )
                fail += 1
            # Be polite to yfinance.
            time.sleep(0.5)
        except Exception as exc:
            _logger.error(
                "[%d/%d] %s — %s",
                i,
                len(missing),
                ticker,
                exc,
            )
            fail += 1

    _logger.info(
        "Backfill complete: %d ok, %d failed",
        ok,
        fail,
    )


if __name__ == "__main__":
    sys.exit(main() or 0)
