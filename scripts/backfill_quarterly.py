"""One-time backfill: fetch quarterly results for all tickers.

Iterates over every ticker in the Iceberg registry and calls
``fetch_quarterly_results`` for each one.  Idempotent — the
tool itself checks freshness before hitting yfinance.

Usage::

    python scripts/backfill_quarterly.py
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_BACKEND_DIR = str(_PROJECT_ROOT / "backend")

# Ensure project root + backend/ on sys.path
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def main() -> None:
    """Fetch quarterly results for every registered ticker."""
    from tools.stock_data_tool import fetch_quarterly_results

    from stocks.repository import StockRepository

    repo = StockRepository()
    tickers = sorted(repo.get_all_registry().keys())

    if not tickers:
        _logger.info("Registry is empty — nothing to backfill.")
        return

    _logger.info(
        "Backfilling quarterly results for %d tickers.",
        len(tickers),
    )

    ok, fail = 0, 0
    for ticker in tickers:
        try:
            msg = fetch_quarterly_results.invoke({"ticker": ticker})
            ok += 1
            _logger.info("%s: %s", ticker, msg[:120])
        except Exception as exc:
            fail += 1
            _logger.warning("%s: FAILED — %s", ticker, str(exc)[:120])

    _logger.info(
        "Backfill complete: %d succeeded, %d failed " "out of %d tickers.",
        ok,
        fail,
        len(tickers),
    )


if __name__ == "__main__":
    main()
