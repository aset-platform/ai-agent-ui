"""Conversation context for multi-turn awareness.

Tracks current topic, rolling summary, and session
metadata. In-memory store with TTL-based eviction.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """Per-session conversation state."""

    session_id: str
    current_topic: str = ""
    last_agent: str = ""
    last_intent: str = ""
    summary: str = ""
    tickers_mentioned: list[str] = field(
        default_factory=list,
    )
    user_tickers: list[str] = field(
        default_factory=list,
    )
    market_preference: str = ""
    subscription_tier: str = ""
    turn_count: int = 0
    last_updated: float = 0.0


class ConversationContextStore:
    """Thread-safe in-memory store with TTL eviction."""

    def __init__(self, ttl: int = 3600) -> None:
        self._store: dict[str, ConversationContext] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def get(
        self, session_id: str,
    ) -> ConversationContext | None:
        with self._lock:
            ctx = self._store.get(session_id)
            if ctx is None:
                return None
            age = time.time() - ctx.last_updated
            if age > self._ttl:
                del self._store[session_id]
                return None
            return ctx

    def upsert(
        self,
        session_id: str,
        ctx: ConversationContext,
    ) -> None:
        with self._lock:
            if ctx.last_updated == 0.0:
                ctx.last_updated = time.time()
            self._store[session_id] = ctx

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                k for k, v in self._store.items()
                if now - v.last_updated > self._ttl
            ]
            for k in expired:
                del self._store[k]
            if expired:
                _logger.debug(
                    "Evicted %d expired contexts",
                    len(expired),
                )


# Module-level singleton.
context_store = ConversationContextStore()

_SUMMARY_PROMPT = (
    "Update this conversation summary given the "
    "latest exchange. Keep it under 3 sentences. "
    "Include: topic discussed, key tickers/numbers "
    "mentioned, and any conclusions.\n\n"
    "Previous summary: {prev}\n"
    "User asked: {user_input}\n"
    "Assistant answered: {response}\n\n"
    "Updated summary:"
)


def _get_summary_llm():
    """Get cheapest available LLM for summarization.

    Cascade: Ollama → Groq scout → Groq versatile.
    Returns None if all unavailable.
    """
    try:
        from config import get_settings
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
            s.ollama_model if s.ollama_enabled
            else None
        )
        return FallbackLLM(
            groq_models=tiers,
            anthropic_model=None,
            temperature=0,
            agent_id="summary",
            token_budget=get_token_budget(),
            compressor=MessageCompressor(),
            cascade_profile="tool",
            ollama_model=ollama,
            ollama_first=True,
        )
    except Exception:
        return None


def update_summary(
    ctx: ConversationContext,
    user_input: str,
    response: str,
) -> None:
    """Update rolling summary in-place.

    Increments turn_count regardless of LLM availability.
    """
    ctx.turn_count += 1

    llm = _get_summary_llm()
    if llm is None:
        _logger.debug("No LLM for summary update")
        return

    from langchain_core.messages import HumanMessage

    prompt = _SUMMARY_PROMPT.format(
        prev=ctx.summary or "No previous context.",
        user_input=user_input[:300],
        response=response[:500],
    )

    try:
        result = llm.invoke(
            [HumanMessage(content=prompt)],
        )
        text = (
            result.content
            if hasattr(result, "content")
            else str(result)
        ).strip()
        if text:
            ctx.summary = text
    except Exception:
        _logger.debug(
            "Summary update failed",
            exc_info=True,
        )
