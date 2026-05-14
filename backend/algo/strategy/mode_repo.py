"""Async PG repo for ``algo.strategy_mode_transitions``.

The transitions table is the audit ledger for the promotion
workflow (draft → paper → live). Every mode change writes a row;
the same table powers eligibility checks (``has_ever_been_live``)
and the history popover on the Strategies tab.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


MODE_DRAFT = "draft"
MODE_PAPER = "paper"
MODE_LIVE = "live"
ALL_MODES: tuple[str, ...] = (MODE_DRAFT, MODE_PAPER, MODE_LIVE)


@dataclass
class ModeTransition:
    """One row from algo.strategy_mode_transitions."""

    id: UUID
    strategy_id: UUID | None
    user_id: UUID
    user_email: str
    from_mode: str | None
    to_mode: str
    reason: str | None
    bypass_used: bool
    ast_hash: str | None
    transitioned_at: datetime


def hash_ast(ast: dict[str, Any]) -> str:
    """Canonical sha256 of a strategy AST.

    Stable across whitespace + key ordering so a re-save of an
    untouched AST hashes identically. Used purely for forensics —
    we never gate runtime behaviour on the hash.
    """
    canonical = json.dumps(
        ast, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def write_transition(
    session: AsyncSession,
    *,
    strategy_id: UUID,
    user_id: UUID,
    user_email: str,
    from_mode: str | None,
    to_mode: str,
    reason: str | None,
    bypass_used: bool,
    ast_hash: str | None,
) -> UUID:
    """Insert a transition row. Returns the new id."""
    row = (
        await session.execute(
            text(
                "INSERT INTO algo.strategy_mode_transitions "
                "(strategy_id, user_id, user_email, from_mode, "
                " to_mode, reason, bypass_used, ast_hash) "
                "VALUES (:sid, :uid, :email, :fm, :tm, :reason, "
                " :bypass, :hash) "
                "RETURNING id"
            ),
            {
                "sid": str(strategy_id),
                "uid": str(user_id),
                "email": user_email,
                "fm": from_mode,
                "tm": to_mode,
                "reason": reason,
                "bypass": bypass_used,
                "hash": ast_hash,
            },
        )
    ).first()
    return row[0]


async def list_transitions(
    session: AsyncSession,
    *,
    strategy_id: UUID,
    limit: int = 50,
) -> list[ModeTransition]:
    """Most-recent-first transition history for one strategy."""
    rows = (
        await session.execute(
            text(
                "SELECT id, strategy_id, user_id, user_email, "
                "       from_mode, to_mode, reason, bypass_used, "
                "       ast_hash, transitioned_at "
                "FROM algo.strategy_mode_transitions "
                "WHERE strategy_id = :sid "
                "ORDER BY transitioned_at DESC "
                "LIMIT :lim"
            ),
            {"sid": str(strategy_id), "lim": limit},
        )
    ).mappings().all()
    return [
        ModeTransition(
            id=r["id"],
            strategy_id=r["strategy_id"],
            user_id=r["user_id"],
            user_email=r["user_email"],
            from_mode=r["from_mode"],
            to_mode=r["to_mode"],
            reason=r["reason"],
            bypass_used=r["bypass_used"],
            ast_hash=r["ast_hash"],
            transitioned_at=r["transitioned_at"],
        )
        for r in rows
    ]


async def has_ever_been(
    session: AsyncSession,
    *,
    strategy_id: UUID,
    mode: str,
) -> bool:
    """True if a transition with ``to_mode=mode`` exists for this
    strategy. Cheap LIMIT-1 lookup; powers the bypass-eligibility
    check (`has_ever_been_live`)."""
    hit = (
        await session.execute(
            text(
                "SELECT 1 FROM algo.strategy_mode_transitions "
                "WHERE strategy_id = :sid AND to_mode = :m "
                "LIMIT 1"
            ),
            {"sid": str(strategy_id), "m": mode},
        )
    ).first()
    return hit is not None


async def latest_transition(
    session: AsyncSession,
    *,
    strategy_id: UUID,
) -> ModeTransition | None:
    """Top-of-history row, or None if the strategy has no
    transitions yet (brand-new draft)."""
    rows = await list_transitions(
        session, strategy_id=strategy_id, limit=1,
    )
    return rows[0] if rows else None
