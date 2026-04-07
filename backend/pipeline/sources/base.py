"""Base protocol and error types for OHLCV data sources."""

from __future__ import annotations

import logging
from datetime import date
from enum import Enum
from typing import Protocol

import pandas as pd

_logger = logging.getLogger(__name__)


class SourceErrorCategory(str, Enum):
    """Classification of source fetch errors."""

    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    PARSE_ERROR = "parse_error"
    UNKNOWN = "unknown"


class SourceError(Exception):
    """Wraps source exceptions with a category."""

    def __init__(
        self,
        category: SourceErrorCategory,
        message: str,
        original: Exception | None = None,
    ) -> None:
        self.category = category
        self.message = message
        self.original = original
        super().__init__(message)


class OHLCVSource(Protocol):
    """Fetches OHLCV data for a single ticker."""

    async def fetch_ohlcv(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Return DataFrame with columns:

        date, open, high, low, close, adj_close, volume.
        """
        ...


def classify_error(exc: Exception) -> SourceErrorCategory:
    """Classify an exception into an error category."""
    msg = str(exc).lower()
    if "429" in msg or "rate" in msg or "throttl" in msg:
        return SourceErrorCategory.RATE_LIMIT
    if "timeout" in msg or "timed out" in msg:
        return SourceErrorCategory.TIMEOUT
    if (
        "not found" in msg
        or "no data" in msg
        or "404" in msg
    ):
        return SourceErrorCategory.NOT_FOUND
    if "parse" in msg or "decode" in msg or "format" in msg:
        return SourceErrorCategory.PARSE_ERROR
    return SourceErrorCategory.UNKNOWN
