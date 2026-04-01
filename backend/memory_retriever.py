"""Retrieve relevant memories via pgvector cosine similarity.

Queries the ``user_memories`` table for the top-K most
relevant memories to the current user message, scoped
to the authenticated user.

Typical usage::

    memories = await retrieve_memories(
        user_id, "What was RELIANCE forecast?",
    )
    # → [{content, memory_type, session_id, score}]
"""

from __future__ import annotations

import logging

from config import get_settings

_logger = logging.getLogger(__name__)


async def retrieve_memories(
    user_id: str,
    query: str,
    top_k: int | None = None,
    exclude_session: str | None = None,
) -> list[dict]:
    """Retrieve top-K memories by cosine similarity.

    Args:
        user_id: Scope to this user's memories only.
        query: Current user message (embedded as the
            query vector).
        top_k: Number of memories to return. Defaults
            to ``settings.memory_top_k`` (5).
        exclude_session: Optionally exclude memories
            from this session (e.g., current session).

    Returns:
        List of dicts with ``content``,
        ``memory_type``, ``session_id``, ``score``.
        Empty list on any failure.
    """
    s = get_settings()
    if not s.memory_enabled:
        return []

    if top_k is None:
        top_k = s.memory_top_k

    from embedding_service import (
        get_embedding_service,
    )

    svc = get_embedding_service()
    query_vec = svc.embed(query)
    if query_vec is None:
        return []

    try:
        return await _query_pgvector(
            user_id, query_vec, top_k,
            exclude_session,
        )
    except Exception:
        _logger.debug(
            "Memory retrieval failed",
            exc_info=True,
        )
        return []


async def _query_pgvector(
    user_id: str,
    query_vec: list[float],
    top_k: int,
    exclude_session: str | None,
) -> list[dict]:
    """Run cosine similarity query against pgvector."""
    from sqlalchemy import select, and_

    from db.engine import get_session_factory
    from db.models.memory import UserMemory

    factory = get_session_factory()
    async with factory() as session:
        # Build WHERE conditions.
        conditions = [
            UserMemory.user_id == user_id,
        ]
        if exclude_session:
            conditions.append(
                UserMemory.session_id
                != exclude_session,
            )

        # pgvector cosine distance: <=>
        # Score = 1 - distance (higher = more similar)
        distance = UserMemory.embedding.cosine_distance(
            query_vec,
        )

        stmt = (
            select(
                UserMemory.content,
                UserMemory.memory_type,
                UserMemory.session_id,
                UserMemory.structured,
                (1 - distance).label("score"),
            )
            .where(and_(*conditions))
            .order_by(distance)
            .limit(top_k)
        )

        result = await session.execute(stmt)
        rows = result.all()

        memories = []
        for row in rows:
            memories.append({
                "content": row.content,
                "memory_type": row.memory_type,
                "session_id": row.session_id,
                "structured": row.structured,
                "score": (
                    round(float(row.score), 4)
                    if row.score is not None
                    else 0.0
                ),
            })

        if memories:
            _logger.debug(
                "Retrieved %d memories for user "
                "%s (top score=%.3f)",
                len(memories),
                user_id[:8],
                memories[0]["score"],
            )
        return memories


def format_memories_for_prompt(
    memories: list[dict],
    token_budget: int | None = None,
) -> str:
    """Format retrieved memories as a prompt block.

    Args:
        memories: From :func:`retrieve_memories`.
        token_budget: Max chars (~4 chars/token).
            Defaults to ``settings.memory_token_budget
            * 4``.

    Returns:
        Formatted string for system prompt injection,
        or empty string if no memories.
    """
    if not memories:
        return ""

    s = get_settings()
    max_chars = (token_budget or s.memory_token_budget) * 4

    lines: list[str] = []
    chars = 0
    for m in memories:
        line = f"- {m['content']}"
        if chars + len(line) > max_chars:
            break
        lines.append(line)
        chars += len(line)

    if not lines:
        return ""

    return "[Memory context]\n" + "\n".join(lines)
