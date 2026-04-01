"""Manually trigger sentiment refresh for all tickers."""

import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)


import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.ai-agent-ui/backend.env"))

# Export keys for Groq/Anthropic.
for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY"):
    v = os.environ.get(k, "")
    if v:
        os.environ[k] = v

from jobs.gap_filler import refresh_all_sentiment

count = refresh_all_sentiment()
_logger.info(f"Scored {count} tickers")
