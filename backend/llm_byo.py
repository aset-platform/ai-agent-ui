"""Per-request BYO (Bring-Your-Own-Model) routing helpers.

Phase B glue: when a non-superuser has exhausted their 10 free chat
calls AND configured a provider key, this module sets a per-request
:class:`ContextVar` so :class:`FallbackLLM._try_model` invokes Groq /
Anthropic with the user's key instead of the platform's.

Responsibilities
----------------
* Decide whether BYO is active for a given chat request.
* Enforce the user's monthly BYO quota via a Redis counter.
* Carry ``{groq, anthropic}`` plaintext keys through the request
  scope without ever touching persistent storage.
* Expose ``get_active_byo_context()`` for the cascade + for
  observability stamping (``key_source="user"``).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from fastapi import HTTPException

_logger = logging.getLogger(__name__)

# Free chat allowance handed to every non-superuser on sign-up.
FREE_ALLOWANCE_LIMIT: int = 10
_IST: ZoneInfo = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class BYOContext:
    """Plaintext provider keys for the current chat turn."""

    user_id: str
    groq_key: str | None = None
    anthropic_key: str | None = None

    @property
    def has_any_key(self) -> bool:
        return bool(self.groq_key or self.anthropic_key)


_byo_ctx: ContextVar[BYOContext | None] = ContextVar(
    "llm_byo_ctx", default=None,
)


def get_active_byo_context() -> BYOContext | None:
    """Return the current request's BYO context, or None."""
    return _byo_ctx.get()


def _month_key(user_id: str) -> str:
    """Redis key for the user's monthly BYO chat-turn counter.

    Resets naturally on IST month rollover — key changes.
    """
    now = datetime.now(_IST)
    return f"byo:month_counter:{user_id}:{now:%Y-%m}"


def read_byo_month_used(user_id: str) -> int:
    """Return the user's BYO chat-turn count for the current
    IST month (0 when unset / cache unavailable). Safe to call
    from async and sync code paths.
    """
    try:
        from cache import get_cache

        cache = get_cache()
        if cache is None:
            return 0
        v = cache.get(_month_key(user_id))
        return int(v) if v else 0
    except Exception:
        _logger.debug(
            "BYO counter read failed for %s",
            user_id,
            exc_info=True,
        )
        return 0


async def _check_and_increment_byo_counter(
    user_id: str, limit: int,
) -> int:
    """Atomically increment the monthly counter.

    Uses Redis INCRBY + EXPIRE in a pipeline so two
    concurrent chat requests from the same user can
    never both pass the limit check with the same
    pre-value (the GET/SET TOCTOU in the prior
    implementation). When the post-increment count
    exceeds *limit*, the increment is rolled back
    (DECRBY) and a 429 is raised — so the persisted
    counter value is always bounded by *limit*.

    Raises:
        HTTPException(429): Limit reached for this month.

    Returns:
        The new (post-increment) counter value.
    """
    try:
        from cache import get_cache
        cache = get_cache()
        if cache is None:
            return 0
        key = _month_key(user_id)
        # Atomic pipeline: INCRBY + EXPIRE as one
        # round-trip. Returns the post-increment
        # value or None when Redis is unavailable.
        new_count = cache.incr(
            key, by=1, ttl=40 * 24 * 3600,
        )
        if new_count is None:
            # Cache down — fall through. Without
            # Redis the counter is best-effort;
            # callers relying on absolute enforcement
            # should use a DB-backed counter instead.
            return 0
        if new_count > limit:
            # Roll back to keep the stored value at
            # or below the limit — concurrent losers
            # don't permanently inflate the counter.
            cache.decr(key, by=1)
            raise HTTPException(
                status_code=429,
                detail=(
                    f"BYO monthly chat limit reached ({limit}). "
                    "Raise your limit on the My LLM Usage page "
                    "or wait for next month's reset."
                ),
            )
        return new_count
    except HTTPException:
        raise
    except Exception:
        _logger.warning(
            "BYO counter failed for %s", user_id,
            exc_info=True,
        )
        return 0


async def resolve_byo_for_chat(
    user_id: str,
    role: str,
    chat_request_count: int,
    byo_monthly_limit: int,
) -> BYOContext | None:
    """Return a ``BYOContext`` if the user should route through BYO.

    Conditions (all must hold):
      * role ≠ superuser
      * chat_request_count ≥ FREE_ALLOWANCE_LIMIT
      * user has at least one configured provider key
      * monthly limit not yet exceeded (increments on success)

    Returns ``None`` when platform keys should continue to be used.
    Raises ``HTTPException(429)`` when BYO is required but the
    user's own monthly limit is exhausted — chat must block.
    """
    if role == "superuser":
        return None
    if chat_request_count < FREE_ALLOWANCE_LIMIT:
        return None

    # Fetch user's decrypted keys. Returned values are plaintext
    # and must never leak into logs / storage.
    from auth.repo import byo_repo
    from backend.db.engine import get_session_factory

    session_factory = get_session_factory()
    groq_key: str | None = None
    anthropic_key: str | None = None
    async with session_factory() as session:
        groq_key = await byo_repo.get_decrypted_key(
            session, user_id, "groq",
        )
        anthropic_key = await byo_repo.get_decrypted_key(
            session, user_id, "anthropic",
        )

    if not (groq_key or anthropic_key):
        # User is over the free allowance and has no key
        # — block the chat.
        raise HTTPException(
            status_code=429,
            detail=(
                "Free chat allowance exhausted. Configure a "
                "Groq or Anthropic key on the My LLM Usage "
                "page to keep chatting."
            ),
        )

    # Enforce user's self-set monthly limit.
    await _check_and_increment_byo_counter(
        user_id, byo_monthly_limit,
    )

    # Bump last_used_at on the keys that are about to be
    # exercised.  Fire-and-forget — chat latency must not
    # wait on a PG write.
    try:
        from sqlalchemy import update
        from backend.db.models.user_llm_key import UserLLMKey

        providers_used = []
        if groq_key:
            providers_used.append("groq")
        if anthropic_key:
            providers_used.append("anthropic")
        if providers_used:
            sf = get_session_factory()
            async with sf() as session:
                await session.execute(
                    update(UserLLMKey)
                    .where(
                        UserLLMKey.user_id == user_id,
                        UserLLMKey.provider.in_(
                            providers_used,
                        ),
                    )
                    .values(
                        last_used_at=datetime.now(
                            timezone.utc,
                        ),
                    ),
                )
                await session.commit()
    except Exception:
        _logger.debug(
            "BYO last_used_at bump failed for %s",
            user_id, exc_info=True,
        )

    ctx = BYOContext(
        user_id=user_id,
        groq_key=groq_key,
        anthropic_key=anthropic_key,
    )
    _logger.info(
        "BYO active user=%s chat_count=%d "
        "has_groq=%s has_anthropic=%s limit=%d",
        user_id,
        chat_request_count,
        bool(groq_key),
        bool(anthropic_key),
        byo_monthly_limit,
    )
    return ctx


@contextmanager
def apply_byo_context(ctx: BYOContext | None) -> Iterator[None]:
    """Scope a ``BYOContext`` to the enclosing block.

    Resets the ContextVar on exit so the next request starts clean.
    """
    if ctx is None:
        yield
        return
    token = _byo_ctx.set(ctx)
    try:
        yield
    finally:
        _byo_ctx.reset(token)


# ---------------------------------------------------------------
# Per-user LangChain client cache — avoids rebuilding ChatGroq /
# ChatAnthropic on every single _try_model() call during a turn.
# Keys are (user_id, provider, api_key_hash).
# ---------------------------------------------------------------

_client_cache: dict[tuple, object] = {}


def get_user_groq_client(
    api_key: str,
    model: str,
    temperature: float,
):
    """Build (and cache) a ChatGroq bound to the user's API key."""
    import hashlib

    from langchain_groq import ChatGroq

    kh = hashlib.sha256(api_key.encode()).hexdigest()[:12]
    cache_key = ("groq", model, kh)
    cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached
    client = ChatGroq(
        model=model,
        temperature=temperature,
        max_retries=0,
        api_key=api_key,
    )
    _client_cache[cache_key] = client
    return client


def get_user_anthropic_client(
    api_key: str,
    model: str,
    temperature: float,
):
    """Build (and cache) a ChatAnthropic bound to the user's key."""
    import hashlib

    from langchain_anthropic import ChatAnthropic

    kh = hashlib.sha256(api_key.encode()).hexdigest()[:12]
    cache_key = ("anthropic", model, kh)
    cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached
    client = ChatAnthropic(
        model=model,
        temperature=temperature,
        api_key=api_key,
    )
    _client_cache[cache_key] = client
    return client
