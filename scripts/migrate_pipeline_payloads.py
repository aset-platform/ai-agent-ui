"""One-off: backfill ``pipeline_steps.payload`` for pipelines
that live only in PG (no Python seed script).

ASETPLTFRM-418 — scoped iceberg maintenance.

The Intraday Bars Daily Pipeline and India Regime Daily Pipeline
get their scoped payloads from their respective seed scripts
(``scripts/seed_intraday_keeper_pipeline.py``,
``scripts/seed_regime_india_pipeline.py``). The India Daily +
USA Daily pipelines, however, were seeded directly into PG (no
Python seeder lives in this repo). This script ships their
scoped ``iceberg_maintenance`` payloads via idempotent
``UPDATE`` statements scoped by ``(pipeline.name, job_type)``.

Idempotent — re-running issues the same UPDATEs which are
no-ops if the JSONB already matches.

Usage::

    docker compose exec backend python scripts/migrate_pipeline_payloads.py
"""

from __future__ import annotations

import asyncio
import json
import logging

from db.engine import get_session_factory
from sqlalchemy import text

_logger = logging.getLogger(__name__)

# Tables each daily pipeline actually writes to. Tied to the
# India / USA daily refresh + analytics + sentiment + piotroski
# + delivery + corporate-events + fundamentals chain. The
# executor accepts any table in ``_HOT_ICEBERG_TABLES |
# ALL_TABLES`` — unknown tables would be skipped with a warning.
# Tables not in ``_HOT_ICEBERG_TABLES`` still get a per-table
# backup + compact + sweep when scoped (they just aren't
# automatically picked up by the legacy unscoped run).
INDIA_DAILY_TABLES = [
    "stocks.ohlcv",
    "stocks.sentiment_scores",
    "stocks.analysis_summary",
    "stocks.piotroski_scores",
    "stocks.nse_delivery",
    "stocks.fundamentals_snapshot",
    "stocks.corporate_events",
    "stocks.promoter_holdings",
    "stocks.company_info",
    "stocks.dividends",
    "stocks.quarterly_results",
    "stocks.forecast_runs",
    "stocks.forecasts",
    "stocks.llm_usage",
]

# USA daily pipeline (steps 1-5) doesn't run NSE bhavcopy,
# corporate events, fundamentals snapshot, or promoter holdings
# — those are India-only steps. Scope to the common subset.
USA_DAILY_TABLES = [
    "stocks.ohlcv",
    "stocks.sentiment_scores",
    "stocks.analysis_summary",
    "stocks.piotroski_scores",
    "stocks.company_info",
    "stocks.dividends",
    "stocks.quarterly_results",
    "stocks.forecast_runs",
    "stocks.forecasts",
    "stocks.llm_usage",
]

PIPELINE_PAYLOADS: dict[str, list[str]] = {
    "India Daily Pipeline": INDIA_DAILY_TABLES,
    "USA Daily Pipeline": USA_DAILY_TABLES,
}


async def migrate() -> None:
    factory = get_session_factory()
    async with factory() as session:
        for pipeline_name, tables in PIPELINE_PAYLOADS.items():
            payload = {"tables": tables}
            result = await session.execute(
                text(
                    "UPDATE pipeline_steps SET "
                    "  payload = CAST(:payload AS jsonb) "
                    "FROM pipelines "
                    "WHERE pipeline_steps.pipeline_id = "
                    "          pipelines.pipeline_id "
                    "  AND pipelines.name = :name "
                    "  AND pipeline_steps.job_type = "
                    "          'iceberg_maintenance'"
                ),
                {
                    "name": pipeline_name,
                    "payload": json.dumps(payload),
                },
            )
            _logger.info(
                "%s: updated %d iceberg_maintenance " "step(s) → tables=%s",
                pipeline_name,
                result.rowcount or 0,
                tables,
            )
        await session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(migrate())
    _logger.info(
        "pipeline_steps.payload backfill complete",
    )


if __name__ == "__main__":
    main()
