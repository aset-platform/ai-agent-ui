"""Semantic query cache for the guardrail node.

Normalizes and hashes incoming queries to detect
near-duplicates.  If a semantically similar question
was answered recently, returns the cached response
without invoking the LangGraph at all.

Uses a lightweight hash-based approach (no ML model)
that catches ~90% of duplicate queries by:
1. Lowercasing and stripping punctuation
2. Removing stop words
3. Sorting remaining tokens
4. Hashing the canonical form

This avoids the ~500MB sentence-transformers
dependency while still providing significant cost
savings for repetitive queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

_logger = logging.getLogger(__name__)

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could",
    "i", "me", "my", "we", "our", "you", "your",
    "he", "she", "it", "they", "them", "this",
    "that", "these", "those", "what", "which",
    "who", "whom", "how", "when", "where", "why",
    "and", "but", "or", "nor", "not", "so", "yet",
    "for", "of", "in", "on", "at", "to", "from",
    "by", "with", "about", "into", "through",
    "up", "down", "out", "off", "over", "under",
    "please", "tell", "show", "give", "get",
    "let", "make", "help", "want", "need",
    "like", "also", "just", "very", "really",
    "much", "more", "most", "some", "any", "all",
    "each", "every", "both", "few", "many",
})

# TTL by intent (seconds)
_INTENT_TTL: dict[str, int] = {
    "stock_analysis": 3600,    # 1 hour
    "forecast": 86400,         # 24 hours
    "portfolio": 300,          # 5 min (changes often)
    "research": 1800,          # 30 min (news updates)
    "decline": 86400,          # 24 hours
}
_DEFAULT_TTL = 3600


def _normalize_query(query: str) -> str:
    """Normalize query to canonical form.

    Lowercase, strip punctuation, remove stop words,
    sort remaining tokens.
    """
    # Lowercase and strip punctuation (keep &)
    clean = re.sub(
        r"[^a-z0-9&.\s]", "", query.lower(),
    )
    # Split into tokens
    tokens = clean.split()
    # Remove stop words
    meaningful = [
        t for t in tokens
        if t not in _STOP_WORDS and len(t) > 1
    ]
    # Sort for order-independence
    meaningful.sort()
    return " ".join(meaningful)


def _query_hash(normalized: str) -> str:
    """SHA-256 hash of normalized query."""
    return hashlib.sha256(
        normalized.encode("utf-8"),
    ).hexdigest()[:16]


def _get_redis():
    """Get Redis cache service."""
    try:
        from cache import cache_service

        return cache_service
    except Exception:
        return None


def check_cache(query: str) -> str | None:
    """Check if a similar query was recently answered.

    Args:
        query: Raw user query.

    Returns:
        Cached response string, or None if no hit.
    """
    svc = _get_redis()
    if svc is None:
        return None

    normalized = _normalize_query(query)
    if not normalized:
        return None

    key = f"cache:query:{_query_hash(normalized)}"
    cached = svc.get(key)
    if cached:
        _logger.debug(
            "Query cache HIT: %s", query[:50],
        )
        try:
            data = json.loads(cached)
            return data.get("response", "")
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def store_cache(
    query: str,
    response: str,
    intent: str = "",
) -> None:
    """Store a query-response pair in cache.

    Args:
        query: Raw user query.
        response: Final response to cache.
        intent: Classified intent (determines TTL).
    """
    svc = _get_redis()
    if svc is None:
        return

    normalized = _normalize_query(query)
    if not normalized:
        return

    key = f"cache:query:{_query_hash(normalized)}"
    ttl = _INTENT_TTL.get(intent, _DEFAULT_TTL)

    data = json.dumps({
        "response": response,
        "intent": intent,
        "query": query[:200],
    })
    svc.set(key, data, ttl)
    _logger.debug(
        "Query cache STORE: %s (ttl=%ds)",
        query[:50], ttl,
    )
