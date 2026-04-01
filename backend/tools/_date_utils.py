"""Date parsing and recency utilities for news tools.

Handles three date formats returned by news sources:
- Unix timestamps (yfinance ``providerPublishTime``)
- RFC 2822 (RSS ``published`` fields)
- ISO 8601 (SerpAPI results)

Conservative policy: items with unparseable dates are
**kept**, not dropped.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_logger = logging.getLogger(__name__)


def parse_published(raw: str) -> datetime | None:
    """Parse a publication date string to UTC datetime.

    Supports Unix timestamps (int or str), RFC 2822,
    and ISO 8601.

    Returns:
        ``datetime`` in UTC, or ``None`` if unparseable.
    """
    if not raw:
        return None

    raw = str(raw).strip()

    # 1. Unix timestamp (int or numeric string).
    try:
        ts = float(raw)
        if 1_000_000_000 < ts < 2_000_000_000:
            return datetime.fromtimestamp(
                ts, tz=timezone.utc,
            )
    except (ValueError, TypeError, OSError):
        pass

    # 2. RFC 2822  ("Thu, 27 Mar 2026 14:30:00 GMT").
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 3. ISO 8601  ("2026-03-27T14:30:00Z").
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

    _logger.debug("Unparseable date: %s", raw[:60])
    return None


def is_within_window(
    published: str,
    days: int,
) -> bool:
    """Check if a published date is within ``days`` ago.

    Returns ``True`` for unparseable dates (conservative).
    """
    dt = parse_published(published)
    if dt is None:
        return True  # Keep items with unknown dates.

    now = datetime.now(timezone.utc)
    age = now - dt
    return age.total_seconds() <= days * 86_400


def time_decay_weight(published: str) -> float:
    """Compute a time-decay multiplier for scoring.

    | Age         | Weight |
    |-------------|--------|
    | 0-2 days    | 1.0    |
    | 3-7 days    | 0.5    |
    | 8-30 days   | 0.25   |
    | >30 days    | 0.1    |
    | Unparseable | 0.5    |
    """
    dt = parse_published(published)
    if dt is None:
        return 0.5

    now = datetime.now(timezone.utc)
    age_days = (now - dt).total_seconds() / 86_400

    if age_days <= 2:
        return 1.0
    if age_days <= 7:
        return 0.5
    if age_days <= 30:
        return 0.25
    return 0.1
