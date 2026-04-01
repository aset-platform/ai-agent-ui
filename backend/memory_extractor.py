"""Extract and persist per-user memories from chat turns.

After each assistant response, this module:

1. Embeds the session summary → upserts a ``summary``
   memory in ``user_memories``.
2. Extracts structured facts via a lightweight LLM
   prompt → embeds and inserts each as a ``fact``
   or ``preference`` memory.

All writes are async (asyncpg) and fire-and-forget.
If embedding or DB fails, the turn is silently skipped
(no user-visible error).

Typical usage::

    import asyncio
    asyncio.run_coroutine_threadsafe(
        extract_and_store_memories(...),
        loop,
    )
"""

from __future__ import annotations

import json
import logging
import uuid

from config import get_settings

_logger = logging.getLogger(__name__)

_FACT_PROMPT = (
    "Extract structured facts from this exchange. "
    "Return a JSON array of objects. Each object has: "
    '"type" ("fact" or "preference"), '
    '"content" (one sentence), '
    '"tickers" (list of ticker symbols), '
    '"metrics" (list of metric names like RSI, MACD), '
    '"agent" (agent name or empty string).\n'
    "Only include facts worth remembering across "
    "sessions. If none, return [].\n\n"
    "User: {user_input}\n"
    "Assistant: {response}\n\n"
    "JSON array:"
)


def _get_fact_llm():
    """Cheapest LLM for fact extraction.

    Same cascade as summary: Ollama → Groq.
    """
    try:
        from llm_fallback import FallbackLLM
        from message_compressor import (
            MessageCompressor,
        )
        from token_budget import get_token_budget

        s = get_settings()
        tiers = [
            t.strip()
            for t in s.groq_model_tiers.split(",")
            if t.strip()
        ][:2]
        ollama = (
            s.ollama_model
            if s.ollama_enabled
            else None
        )
        return FallbackLLM(
            groq_models=tiers,
            anthropic_model=None,
            temperature=0,
            agent_id="fact_extractor",
            token_budget=get_token_budget(),
            compressor=MessageCompressor(),
            cascade_profile="tool",
            ollama_model=ollama,
            ollama_first=True,
        )
    except Exception:
        return None


def _parse_facts(raw: str) -> list[dict]:
    """Parse LLM output into fact dicts."""
    text = raw.strip()
    # Strip markdown code fences if present.
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            ln for ln in lines
            if not ln.startswith("```")
        )
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [
                d for d in data
                if isinstance(d, dict)
                and d.get("content")
            ]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


async def extract_and_store_memories(
    user_id: str,
    session_id: str,
    user_input: str,
    response: str,
    summary: str,
    turn_number: int,
    agent_id: str = "",
) -> None:
    """Extract memories and persist to pgvector.

    Fire-and-forget — all exceptions are caught
    and logged at DEBUG level.

    Args:
        user_id: Authenticated user UUID.
        session_id: Current chat session ID.
        user_input: The user's message.
        response: The assistant's response.
        summary: Current rolling session summary.
        turn_number: Turn count in this session.
        agent_id: Agent that handled the turn.
    """
    s = get_settings()
    if not s.memory_enabled:
        return

    # Skip if response is too short (no real content
    # to extract — e.g., "I will call tool X").
    if len(response.strip()) < 50:
        _logger.debug(
            "Skipping memory extraction: "
            "response too short (%d chars)",
            len(response),
        )
        return

    from embedding_service import (
        get_embedding_service,
    )

    svc = get_embedding_service()

    try:
        await _upsert_summary_memory(
            svc, user_id, session_id,
            summary, turn_number,
        )
    except Exception:
        _logger.debug(
            "Summary memory upsert failed",
            exc_info=True,
        )

    try:
        await _extract_and_store_facts(
            svc, user_id, session_id,
            user_input, response,
            turn_number, agent_id,
        )
    except Exception:
        _logger.debug(
            "Fact extraction failed",
            exc_info=True,
        )


async def _upsert_summary_memory(
    svc,
    user_id: str,
    session_id: str,
    summary: str,
    turn_number: int,
) -> None:
    """Embed and upsert the session summary."""
    if not summary:
        return

    vec = svc.embed(summary)
    if vec is None:
        return

    from db.engine import get_session_factory
    from sqlalchemy import select, and_

    factory = get_session_factory()
    async with factory() as session:
        from db.models.memory import UserMemory

        stmt = select(UserMemory).where(
            and_(
                UserMemory.user_id == user_id,
                UserMemory.session_id == session_id,
                UserMemory.memory_type == "summary",
            ),
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.content = summary
            existing.embedding = vec
            existing.turn_number = turn_number
        else:
            session.add(
                UserMemory(
                    memory_id=str(uuid.uuid4()),
                    user_id=user_id,
                    session_id=session_id,
                    memory_type="summary",
                    content=summary,
                    embedding=vec,
                    turn_number=turn_number,
                ),
            )
        await session.commit()
        _logger.debug(
            "Summary memory %s for session %s",
            "updated" if existing else "created",
            session_id[:8],
        )


async def _extract_and_store_facts(
    svc,
    user_id: str,
    session_id: str,
    user_input: str,
    response: str,
    turn_number: int,
    agent_id: str,
) -> None:
    """Extract structured facts via LLM, embed, store."""
    llm = _get_fact_llm()
    if llm is None:
        return

    from langchain_core.messages import HumanMessage

    prompt = _FACT_PROMPT.format(
        user_input=user_input[:300],
        response=response[:500],
    )

    try:
        result = llm.invoke(
            [HumanMessage(content=prompt)],
        )
        raw = (
            result.content
            if hasattr(result, "content")
            else str(result)
        )
    except Exception:
        _logger.debug(
            "Fact LLM invoke failed",
            exc_info=True,
        )
        return

    facts = _parse_facts(raw)
    if not facts:
        return

    from db.engine import get_session_factory
    from db.models.memory import UserMemory

    factory = get_session_factory()
    async with factory() as session:
        for fact in facts:
            content = fact["content"]
            vec = svc.embed(content)
            if vec is None:
                continue

            mem_type = fact.get("type", "fact")
            if mem_type not in (
                "fact", "preference",
            ):
                mem_type = "fact"

            structured = {
                k: fact.get(k, [])
                for k in (
                    "tickers", "metrics",
                )
            }
            structured["agent"] = (
                fact.get("agent") or agent_id
            )

            session.add(
                UserMemory(
                    memory_id=str(uuid.uuid4()),
                    user_id=user_id,
                    session_id=session_id,
                    memory_type=mem_type,
                    content=content,
                    structured=structured,
                    embedding=vec,
                    turn_number=turn_number,
                ),
            )

        await session.commit()
        _logger.debug(
            "Stored %d facts for session %s",
            len(facts),
            session_id[:8],
        )
