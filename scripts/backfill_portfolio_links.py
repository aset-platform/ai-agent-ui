"""Backfill: auto-link portfolio tickers to watchlist.

One-time fix for tickers added to portfolio before
the auto-link feature was added.
"""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "backend"),
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.ai-agent-ui/backend.env"))

from auth.repo.repository import IcebergUserRepository
from tools._stock_shared import _require_repo

auth_repo = IcebergUserRepository()
stock_repo = _require_repo()

# Get all users
from pyiceberg.catalog import load_catalog

cat = load_catalog("local")
users_tbl = cat.load_table("auth.users")
users_df = users_tbl.scan().to_pandas()

for _, user in users_df.iterrows():
    uid = user["user_id"]
    email = user["email"]

    # Get portfolio tickers
    holdings = stock_repo.get_portfolio_holdings(uid)
    if holdings.empty:
        continue

    portfolio_tickers = list(holdings["ticker"].unique())
    existing_links = auth_repo.get_user_tickers(uid)

    linked = 0
    for ticker in portfolio_tickers:
        if ticker not in existing_links:
            try:
                auth_repo.link_ticker(
                    uid, ticker, "portfolio_backfill",
                )
                linked += 1
            except Exception as e:
                _logger.info(f"  Failed {ticker}: {e}")

    if linked:
        _logger.info(
            f"{email}: linked {linked} portfolio tickers "
            f"({portfolio_tickers})"
        )
    else:
        _logger.info(
            f"{email}: all {len(portfolio_tickers)} already linked"
        )

_logger.info("Done")
