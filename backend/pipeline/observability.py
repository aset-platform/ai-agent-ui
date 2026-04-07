"""Pipeline observability: structured logging and retry utilities."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from backend.pipeline.config import (
    MAX_CONSECUTIVE_429,
    MAX_RETRIES,
    RATE_LIMIT_BACKOFF_S,
    RETRY_BACKOFF_BASE_S,
)
from backend.pipeline.sources.base import (
    SourceError,
    SourceErrorCategory,
)

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Structured logging
# ------------------------------------------------------------------


class PipelineLogger:
    """Structured logging for pipeline operations."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def batch_started(
        self,
        cursor_name: str,
        batch_size: int,
        last_processed_id: int,
    ) -> None:
        """Log pipeline.batch.started event."""
        self._log.info(
            "pipeline.batch.started cursor=%s "
            "batch_size=%d last_processed_id=%d",
            cursor_name,
            batch_size,
            last_processed_id,
        )

    def ticker_fetched(
        self,
        ticker: str,
        source: str,
        duration_ms: int,
    ) -> None:
        """Log pipeline.ticker.fetched event."""
        self._log.info(
            "pipeline.ticker.fetched ticker=%s "
            "source=%s duration_ms=%d",
            ticker,
            source,
            duration_ms,
        )

    def ticker_skipped(
        self,
        ticker: str,
        reason: str,
    ) -> None:
        """Log pipeline.ticker.skipped event."""
        self._log.info(
            "pipeline.ticker.skipped ticker=%s reason=%s",
            ticker,
            reason,
        )

    def ticker_failed(
        self,
        ticker: str,
        category: str,
        error: str,
    ) -> None:
        """Log pipeline.ticker.failed event."""
        self._log.warning(
            "pipeline.ticker.failed ticker=%s "
            "category=%s error=%s",
            ticker,
            category,
            error,
        )

    def batch_completed(
        self,
        cursor_name: str,
        processed: int,
        skipped: int,
        failed: int,
        duration_s: float,
    ) -> None:
        """Log pipeline.batch.completed event."""
        self._log.info(
            "pipeline.batch.completed cursor=%s "
            "processed=%d skipped=%d failed=%d "
            "duration_s=%.2f",
            cursor_name,
            processed,
            skipped,
            failed,
            duration_s,
        )

    def cursor_progress(
        self,
        cursor_name: str,
        last_processed_id: int,
        total: int,
    ) -> None:
        """Log pipeline.cursor.progress event with pct."""
        pct = (
            (last_processed_id / total * 100)
            if total > 0
            else 0
        )
        self._log.info(
            "pipeline.cursor.progress cursor=%s "
            "last_processed_id=%d total=%d pct=%.1f%%",
            cursor_name,
            last_processed_id,
            total,
            pct,
        )


# ------------------------------------------------------------------
# Retry with exponential backoff
# ------------------------------------------------------------------


async def retry_with_backoff(
    coro_factory: Callable[[], Any],
    ticker: str,
    max_retries: int = MAX_RETRIES,
    backoff_base: float = RETRY_BACKOFF_BASE_S,
    logger: PipelineLogger | None = None,
) -> Any:
    """Retry an async operation with exponential backoff.

    *coro_factory* is a zero-arg callable that returns a new
    awaitable on each invocation so the operation can be
    re-attempted.

    On ``SourceError`` with ``RATE_LIMIT``: re-raises
    immediately (caller handles batch-level backoff).
    On other ``SourceError``: retries up to *max_retries*.
    Returns the result on success; raises the last
    ``SourceError`` on exhaustion.
    """
    last_err: SourceError | None = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except SourceError as exc:
            if exc.category == SourceErrorCategory.RATE_LIMIT:
                raise
            last_err = exc
            if attempt < max_retries - 1:
                delay = backoff_base * (2 ** attempt)
                if logger:
                    logger.ticker_failed(
                        ticker,
                        exc.category.value,
                        str(exc),
                    )
                _logger.warning(
                    "Retry %d/%d for ticker=%s "
                    "delay=%.1fs error=%s",
                    attempt + 1,
                    max_retries,
                    ticker,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
    # All attempts exhausted — raise last error.
    raise last_err  # type: ignore[misc]


# ------------------------------------------------------------------
# Rate-limit tracker (batch-level backoff)
# ------------------------------------------------------------------


class RateLimitTracker:
    """Tracks consecutive 429 errors for batch-level backoff."""

    def __init__(
        self,
        max_consecutive: int = MAX_CONSECUTIVE_429,
        backoff_s: float = RATE_LIMIT_BACKOFF_S,
    ) -> None:
        self._consecutive_429 = 0
        self._max = max_consecutive
        self._backoff_s = backoff_s

    def record_success(self) -> None:
        """Reset counter on success."""
        self._consecutive_429 = 0

    async def record_rate_limit(self) -> None:
        """Record a 429 and back off.

        Raises ``SourceError`` if max consecutive hit.
        """
        self._consecutive_429 += 1
        if self._consecutive_429 >= self._max:
            raise SourceError(
                SourceErrorCategory.RATE_LIMIT,
                f"Hit {self._max} consecutive 429s, "
                "pausing cursor",
            )
        _logger.warning(
            "Rate limit hit (%d/%d), backing off %.0fs",
            self._consecutive_429,
            self._max,
            self._backoff_s,
        )
        await asyncio.sleep(self._backoff_s)

    @property
    def should_pause(self) -> bool:
        """True when max consecutive 429s reached."""
        return self._consecutive_429 >= self._max
