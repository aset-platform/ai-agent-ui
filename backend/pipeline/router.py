"""Source router for OHLCV and fundamentals fetching."""

from __future__ import annotations

import logging

from backend.pipeline.sources.base import OHLCVSource
from backend.pipeline.sources.nse import NseSource
from backend.pipeline.sources.racing import RacingSource
from backend.pipeline.sources.yfinance import YfinanceSource

_logger = logging.getLogger(__name__)


def get_ohlcv_source(context: str) -> OHLCVSource:
    """Return the appropriate OHLCV source for *context*.

    Args:
        context: ``"bulk"``, ``"daily"``, ``"retry"``,
            ``"correct"``, or ``"chat"``.

    Returns:
        An :class:`OHLCVSource` implementation.

    Raises:
        ValueError: If *context* is not recognised.
    """
    if context in ("bulk", "daily"):
        return YfinanceSource()
    if context in ("retry", "correct"):
        return NseSource()
    if context == "chat":
        return RacingSource(
            NseSource(), YfinanceSource(),
        )
    raise ValueError(f"Unknown context: {context}")


def get_fundamentals_source() -> YfinanceSource:
    """Return a Yahoo Finance source for fundamentals."""
    return YfinanceSource()
