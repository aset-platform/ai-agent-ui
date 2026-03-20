"""Seed initial LLM pricing rates into Iceberg.

Idempotent — skips if current pricing already exists.

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    python scripts/seed_llm_pricing.py
"""

import logging
import os
import sys
from datetime import date

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
_BACKEND_DIR = os.path.join(_SCRIPT_DIR, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from paths import (  # noqa: E402
    ICEBERG_CATALOG_URI,
    ICEBERG_WAREHOUSE_URI,
)

os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__URI",
    ICEBERG_CATALOG_URI,
)
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__WAREHOUSE",
    ICEBERG_WAREHOUSE_URI,
)

_STOCKS_DIR = os.path.join(_SCRIPT_DIR, "stocks")
if _STOCKS_DIR not in sys.path:
    sys.path.insert(0, _STOCKS_DIR)

from stocks.repository import StockRepository  # noqa: E402

_logger = logging.getLogger(__name__)

# Initial pricing as of Mar 2026 ($/1M tokens).
_INITIAL_RATES = [
    {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "input_cost": 0.59,
        "output_cost": 0.79,
    },
    {
        "provider": "groq",
        "model": "moonshotai/kimi-k2-instruct",
        "input_cost": 0.40,
        "output_cost": 0.40,
    },
    {
        "provider": "groq",
        "model": "qwen/qwen3-32b",
        "input_cost": 0.34,
        "output_cost": 0.34,
    },
    {
        "provider": "groq",
        "model": ("meta-llama/llama-4-scout-17b" "-16e-instruct"),
        "input_cost": 0.11,
        "output_cost": 0.34,
    },
    {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "input_cost": 3.00,
        "output_cost": 15.00,
    },
]


def seed_pricing() -> None:
    """Insert initial pricing if none exists."""
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s | %(levelname)-8s" " | %(name)s | %(message)s"),
    )
    repo = StockRepository()
    existing = repo.get_current_pricing()
    if not existing.empty:
        _logger.info(
            "Pricing already seeded (%d rows)" " — skipping.",
            len(existing),
        )
        return

    effective = date(2026, 3, 1)
    for rate in _INITIAL_RATES:
        pid = repo.add_pricing(
            provider=rate["provider"],
            model=rate["model"],
            input_cost=rate["input_cost"],
            output_cost=rate["output_cost"],
            effective_from=effective,
        )
        _logger.info(
            "Seeded pricing: %s/%s → $%.2f/$%.2f" " (id=%s)",
            rate["provider"],
            rate["model"],
            rate["input_cost"],
            rate["output_cost"],
            pid,
        )
    _logger.info("LLM pricing seed complete.")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    seed_pricing()
