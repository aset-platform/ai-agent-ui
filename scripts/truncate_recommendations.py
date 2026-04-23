"""One-shot: wipe all recommendation data.

Used when switching to the monthly-per-scope
generation rule (see Sprint 7 plan) so every user
starts with a clean slate.  TRUNCATE ... CASCADE
drops rows from:

* ``stocks.recommendation_runs``
* ``stocks.recommendations``          (FK cascade)
* ``stocks.recommendation_outcomes``  (FK cascade)

Run from the project root::

    PYTHONPATH=.:backend python scripts/truncate_recommendations.py

The script prompts for confirmation before executing
the TRUNCATE.  Pass ``--yes`` to skip the prompt
(e.g. when invoking from CI or deployment tooling).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from config import get_settings

_logger = logging.getLogger("truncate_recommendations")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


async def _truncate() -> tuple[int, int, int]:
    """TRUNCATE the three recommendation tables.

    Returns pre-truncate row counts as a tuple
    ``(runs, recs, outcomes)`` for audit logging.
    """
    eng = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(
        eng, class_=AsyncSession,
    )

    async with factory() as s:
        runs = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM "
                    "stocks.recommendation_runs",
                ),
            )
        ).scalar_one()
        recs = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM "
                    "stocks.recommendations",
                ),
            )
        ).scalar_one()
        outcomes = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM "
                    "stocks.recommendation_outcomes",
                ),
            )
        ).scalar_one()

        _logger.info(
            "before: runs=%d recs=%d outcomes=%d",
            runs, recs, outcomes,
        )
        await s.execute(
            text(
                "TRUNCATE TABLE "
                "stocks.recommendation_runs "
                "RESTART IDENTITY CASCADE"
            )
        )
        await s.commit()
        _logger.info("TRUNCATE committed.")

    await eng.dispose()
    return runs, recs, outcomes


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Wipe all recommendation_* rows — "
            "destructive, one-time reset."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation.",
    )
    args = parser.parse_args()

    if not args.yes:
        _logger.warning(
            "This will TRUNCATE "
            "stocks.recommendation_runs and cascade "
            "to stocks.recommendations and "
            "stocks.recommendation_outcomes."
        )
        reply = input(
            "Type 'yes' to continue: ",
        ).strip().lower()
        if reply != "yes":
            _logger.info("aborted by user.")
            return 1

    runs, recs, outcomes = asyncio.run(_truncate())
    _logger.info(
        "after: all three tables empty "
        "(wiped runs=%d recs=%d outcomes=%d)",
        runs, recs, outcomes,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
