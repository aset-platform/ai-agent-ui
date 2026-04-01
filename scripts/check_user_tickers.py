"""Check auth.user_tickers table contents."""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "backend"),
)
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.ai-agent-ui/backend.env"))

from pyiceberg.catalog import load_catalog

cat = load_catalog("local")

# user_tickers
tbl = cat.load_table("auth.user_tickers")
df = tbl.scan().to_pandas()
_logger.info("=== auth.user_tickers ===")
_logger.info(df.to_string())
_logger.info(f"\nTotal: {len(df)} rows")

# Also check users to map user_ids
_logger.info("\n=== auth.users (id + email) ===")
utbl = cat.load_table("auth.users")
udf = utbl.scan().to_pandas()[["user_id", "email", "role"]]
_logger.info(udf.to_string())
