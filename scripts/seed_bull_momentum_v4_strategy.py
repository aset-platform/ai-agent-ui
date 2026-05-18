"""Seed the ``BULL — Daily Momentum + Market RS + Volume`` v4
strategy into ``algo.strategies`` as ``mode='draft'`` for the
operator user.

The 2026-05-18 backtest of this AST on top-50 ADTV universe over
24 months returned +0.44 % with 53.6 % win rate / 2.19 R:R and
+1.53 % per-trade expectancy.  Excluding the unmitigated
election-week cluster the strategy generalises to ~+25 % / 24 m
(≈ +12 % annualised).  Seeded as draft so it can be exercised
through the proper backtest → paper → live promotion flow
documented in CLAUDE.md §5.16 instead of running with a
transient in-memory UUID.

Idempotent — re-seeding updates the existing row by name.

Usage::

    docker compose exec backend bash -c \\
        "cd /app && PYTHONPATH=/app:/app/backend python \\
        scripts/seed_bull_momentum_v4_strategy.py"
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from db.engine import get_session_factory
from sqlalchemy import text

_logger = logging.getLogger(__name__)

# Deterministic UUID — same namespace as the other algo-tier
# seeds so re-running gives a stable id we can reference from
# downstream tickets / docs.
_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")
STRATEGY_NAME = "BULL — Daily Momentum + Market RS + Volume (CNC Swing v4)"
STRATEGY_ID = str(uuid.uuid5(_NS, STRATEGY_NAME))

# Operator user.  Per CLAUDE.md `feedback_commit_coauthor`, the
# project owner is the canonical superuser.
OPERATOR_EMAIL = "asequitytrading@gmail.com"

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "algo"
    / "strategy"
    / "templates"
    / "bull_momentum_daily_swing_v4.json"
)


async def seed() -> None:
    payload = json.loads(TEMPLATE_PATH.read_text())
    # Use our deterministic id, not the placeholder one baked
    # into the template — the template's id field is only meant
    # for the loader path that hands an in-memory Strategy to
    # run_backtest (transient).  The PG row needs a stable id.
    payload["id"] = STRATEGY_ID

    factory = get_session_factory()
    async with factory() as session:
        # Resolve operator user_id.
        row = (await session.execute(
            text("SELECT user_id FROM public.users WHERE email = :e"),
            {"e": OPERATOR_EMAIL},
        )).first()
        if row is None:
            raise RuntimeError(
                f"Operator user '{OPERATOR_EMAIL}' not found in "
                "public.users — seed cannot proceed.",
            )
        user_id = row[0]
        _logger.info("operator user_id=%s", user_id)

        # Upsert by id (idempotent on stable UUID).
        await session.execute(
            text(
                "INSERT INTO algo.strategies "
                "(id, user_id, name, ast_json, ast_version, "
                " mode, status) "
                "VALUES (:id, :uid, :name, CAST(:ast AS jsonb), "
                "        1, 'draft', 'active') "
                "ON CONFLICT (id) DO UPDATE SET "
                "  name = EXCLUDED.name, "
                "  ast_json = EXCLUDED.ast_json, "
                "  ast_version = EXCLUDED.ast_version, "
                "  mode = EXCLUDED.mode, "
                "  status = EXCLUDED.status, "
                "  updated_at = NOW()"
            ),
            {
                "id": STRATEGY_ID,
                "uid": str(user_id),
                "name": STRATEGY_NAME,
                "ast": json.dumps(payload),
            },
        )
        await session.commit()

    _logger.info(
        "Seeded strategy id=%s name=%r as mode='draft' for user %s",
        STRATEGY_ID, STRATEGY_NAME, OPERATOR_EMAIL,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(seed())
