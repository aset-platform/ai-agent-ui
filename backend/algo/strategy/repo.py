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
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.strategy.ast import Strategy, parse_strategy
from backend.algo.strategy.mode_repo import MODE_DRAFT, hash_ast

_logger = logging.getLogger(__name__)


@dataclass
class UpdateResult:
    """Outcome of ``update_strategy``.

    ``demoted_from`` is non-None when the AST edit auto-demoted a
    non-draft strategy back to ``draft``; the route uses this to
    write an audit row on the same logical transaction.
    """
    found: bool
    demoted_from: str | None = None
    ast_hash: str | None = None


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
    """Persist a new strategy. Returns the row id (== strategy.id).

    Always allocates a fresh ``id`` so callers that clone a template
    or copy an existing strategy's AST can't collide on the PK. The
    AST's ``id`` is rewritten to match the new row before serialise.
    """
    new_id = uuid4()
    strategy = strategy.model_copy(update={"id": new_id})
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
) -> UpdateResult:
    """Replace the AST for a user-owned strategy.

    Returns ``UpdateResult(found=False)`` on miss. The AST's
    ``id`` is stamped to match ``strategy_id`` before serialise
    so ``ast_json.id`` can never drift from the row PK, no matter
    what the client sent.

    Any save on a non-``draft`` strategy auto-demotes it back to
    ``draft`` in the same UPDATE; the prior mode comes back as
    ``demoted_from`` so the caller can write a transition row.
    Runtime sessions cache the AST in memory, so this demotion
    is purely a permission-gate change — in-flight orders are
    unaffected.
    """
    strategy = strategy.model_copy(update={"id": strategy_id})
    ast_json = strategy.model_dump(mode="json", by_alias=True)
    ast_hash = hash_ast(ast_json)
    now = datetime.now(timezone.utc)

    # Use a single round-trip with a CTE so we capture the prior
    # mode atomically with the UPDATE. ``demoted_from`` is the
    # pre-UPDATE mode iff it wasn't already draft.
    res = (
        await session.execute(
            text(
                "WITH prior AS ("
                "  SELECT mode FROM algo.strategies "
                "  WHERE id = :sid AND user_id = :uid "
                "    AND archived_at IS NULL "
                "), upd AS ("
                "  UPDATE algo.strategies SET "
                "    name = :name, ast_json = :ast, "
                "    mode = :draft, updated_at = :now "
                "  WHERE id = :sid AND user_id = :uid "
                "    AND archived_at IS NULL "
                "  RETURNING id"
                ") "
                "SELECT prior.mode AS prior_mode, "
                "       (SELECT count(*) FROM upd) AS rowcount "
                "FROM prior"
            ),
            {
                "name": strategy.name,
                "ast": json.dumps(ast_json),
                "draft": MODE_DRAFT,
                "now": now,
                "uid": user_id,
                "sid": strategy_id,
            },
        )
    ).mappings().first()
    await session.commit()
    if res is None or res["rowcount"] == 0:
        return UpdateResult(found=False)
    prior = res["prior_mode"]
    return UpdateResult(
        found=True,
        demoted_from=prior if prior != MODE_DRAFT else None,
        ast_hash=ast_hash,
    )


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
