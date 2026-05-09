# backend/algo/strategy/repo.py
"""Async CRUD for ``algo.strategies``.

The table stores the Pydantic-validated AST as JSONB; reads
re-parse to enforce schema even after server restarts that
might have changed the AST grammar (re-validation is cheap
relative to a backtest run).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.strategy.ast import Strategy, parse_strategy

_logger = logging.getLogger(__name__)


async def list_strategies(
    session: AsyncSession,
    user_id: UUID,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Return strategy summary rows (no full AST) for the user.

    For the full AST, callers must hit ``get_strategy``.
    """
    sql = (
        "SELECT id, name, mode, status, created_at, updated_at, "
        "archived_at "
        "FROM algo.strategies "
        "WHERE user_id = :uid "
    )
    if not include_archived:
        sql += "AND archived_at IS NULL "
    sql += "ORDER BY updated_at DESC LIMIT 200"
    rows = (
        await session.execute(text(sql), {"uid": user_id})
    ).mappings()
    return [dict(r) for r in rows]


async def get_strategy(
    session: AsyncSession, user_id: UUID, strategy_id: UUID,
) -> Strategy | None:
    """Fetch + re-parse one strategy. Returns None on miss."""
    row = (await session.execute(
        text(
            "SELECT id, name, ast_json, mode, status "
            "FROM algo.strategies "
            "WHERE user_id = :uid AND id = :sid"
        ),
        {"uid": user_id, "sid": strategy_id},
    )).mappings().first()
    if row is None:
        return None
    payload = dict(row["ast_json"])
    payload["id"] = str(row["id"])
    payload["name"] = row["name"]
    return parse_strategy(payload)


async def create_strategy(
    session: AsyncSession, user_id: UUID, strategy: Strategy,
) -> UUID:
    """Persist a new strategy. Returns the row id (== strategy.id)."""
    new_id = strategy.id or uuid4()
    now = datetime.now(timezone.utc)
    await session.execute(
        text(
            "INSERT INTO algo.strategies "
            "(id, user_id, name, ast_json, mode, status, "
            " created_at, updated_at) "
            "VALUES (:id, :uid, :name, :ast, 'draft', 'active', "
            " :now, :now)"
        ),
        {
            "id": new_id,
            "uid": user_id,
            "name": strategy.name,
            "ast": json.dumps(
                strategy.model_dump(mode="json", by_alias=True),
            ),
            "now": now,
        },
    )
    await session.commit()
    return new_id


async def update_strategy(
    session: AsyncSession,
    user_id: UUID,
    strategy_id: UUID,
    strategy: Strategy,
) -> bool:
    """Replace the AST for a user-owned strategy.

    Returns False on miss.
    """
    now = datetime.now(timezone.utc)
    res = await session.execute(
        text(
            "UPDATE algo.strategies SET "
            "name = :name, ast_json = :ast, updated_at = :now "
            "WHERE user_id = :uid AND id = :sid "
            "AND archived_at IS NULL"
        ),
        {
            "name": strategy.name,
            "ast": json.dumps(
                strategy.model_dump(mode="json", by_alias=True),
            ),
            "now": now,
            "uid": user_id,
            "sid": strategy_id,
        },
    )
    await session.commit()
    return res.rowcount > 0


async def archive_strategy(
    session: AsyncSession, user_id: UUID, strategy_id: UUID,
) -> bool:
    """Soft-delete a strategy. Returns False on miss."""
    now = datetime.now(timezone.utc)
    res = await session.execute(
        text(
            "UPDATE algo.strategies SET archived_at = :now "
            "WHERE user_id = :uid AND id = :sid "
            "AND archived_at IS NULL"
        ),
        {"now": now, "uid": user_id, "sid": strategy_id},
    )
    await session.commit()
    return res.rowcount > 0


async def hard_delete_strategy(
    session: AsyncSession, user_id: UUID, strategy_id: UUID,
) -> bool:
    """Hard delete (only allowed on archived rows).

    Returns False on miss.
    """
    res = await session.execute(
        text(
            "DELETE FROM algo.strategies "
            "WHERE user_id = :uid AND id = :sid "
            "AND archived_at IS NOT NULL"
        ),
        {"uid": user_id, "sid": strategy_id},
    )
    await session.commit()
    return res.rowcount > 0
