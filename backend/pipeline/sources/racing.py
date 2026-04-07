"""Racing source that runs two sources concurrently."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

import pandas as pd

from backend.pipeline.sources.base import (
    OHLCVSource,
    SourceError,
    SourceErrorCategory,
)

_logger = logging.getLogger(__name__)


class RacingSource:
    """Races two OHLCV sources and returns the first success.

    Both *primary* and *secondary* are launched concurrently.
    The first source to return a valid DataFrame wins; the
    other task is cancelled.  If both fail, a combined
    ``SourceError`` is raised.
    """

    def __init__(
        self,
        primary: OHLCVSource,
        secondary: OHLCVSource,
    ) -> None:
        self._primary = primary
        self._secondary = secondary

    async def fetch_ohlcv(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Race both sources for *symbol*."""
        primary_task = asyncio.create_task(
            self._primary.fetch_ohlcv(
                symbol, start, end,
            ),
            name="primary",
        )
        secondary_task = asyncio.create_task(
            self._secondary.fetch_ohlcv(
                symbol, start, end,
            ),
            name="secondary",
        )

        pending: set[asyncio.Task[pd.DataFrame]] = {
            primary_task,
            secondary_task,
        }
        errors: list[str] = []

        while pending:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                exc = task.exception()
                if exc is None:
                    # Winner — cancel remaining tasks.
                    winner = task.get_name()
                    _logger.info(
                        "RacingSource: %s won for %s",
                        winner, symbol,
                    )
                    for p in pending:
                        p.cancel()
                    # Await cancelled tasks for cleanup.
                    for p in pending:
                        try:
                            await p
                        except (
                            asyncio.CancelledError,
                            Exception,
                        ):
                            pass
                    return task.result()

                # Task failed — record and continue.
                name = task.get_name()
                errors.append(f"{name}: {exc}")
                _logger.warning(
                    "RacingSource: %s failed for %s: %s",
                    name, symbol, exc,
                )

        raise SourceError(
            SourceErrorCategory.UNKNOWN,
            f"All sources failed for {symbol}: "
            + "; ".join(errors),
        )
