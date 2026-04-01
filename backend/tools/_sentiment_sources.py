"""Multi-source headline fetcher for sentiment scoring.

Fetches news headlines from three sources in priority order:
1. yfinance ``t.news`` (weight 1.0)
2. Yahoo Finance RSS (weight 0.8)
3. Google News RSS (weight 0.6)

Deduplicates using ``difflib.SequenceMatcher`` (≥0.8 threshold)
and keeps the item with the highest source weight.

No new dependencies — uses ``feedparser`` (already installed)
and ``difflib`` (stdlib).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import quote_plus

_logger = logging.getLogger(__name__)

# Source weights — higher = more trusted.
_WEIGHT_YFINANCE = 1.0
_WEIGHT_YAHOO_RSS = 0.8
_WEIGHT_GOOGLE_RSS = 0.6

# Dedup threshold — 0.8 = 80 % title similarity.
_DEDUP_THRESHOLD = 0.8

# Feed timeout in seconds.
_FEED_TIMEOUT = 10

# Max headlines per source.
_MAX_PER_SOURCE = 10


@dataclass
class HeadlineItem:
    """A single news headline with source metadata."""

    title: str
    source: str = ""  # "yfinance" | "yahoo_rss" | "google_rss"
    weight: float = 0.0
    published: str = ""


def fetch_all_headlines(
    ticker: str,
    max_age_days: int = 7,
) -> list[HeadlineItem]:
    """Fetch headlines from all sources, deduplicate.

    Sources are tried in priority order.  If a source
    fails, it is skipped and the next source is tried.
    Results are filtered to the last ``max_age_days``.

    Args:
        ticker: Stock ticker symbol (e.g. ``AAPL``).
        max_age_days: Only keep headlines published
            within this many days.  Defaults to 7.

    Returns:
        Deduplicated list of :class:`HeadlineItem` sorted
        by weight descending, filtered by recency.
    """
    from tools._date_utils import is_within_window

    items: list[HeadlineItem] = []

    for fetcher in (
        _fetch_yfinance,
        _fetch_yahoo_rss,
        _fetch_google_rss,
    ):
        try:
            items.extend(fetcher(ticker))
        except Exception:
            _logger.debug(
                "%s failed for %s",
                fetcher.__name__,
                ticker,
                exc_info=True,
            )

    if not items:
        _logger.info(
            "No headlines from any source for %s",
            ticker,
        )
        return []

    deduped = _deduplicate(items)

    # Filter by recency.
    recent = [
        h for h in deduped
        if is_within_window(h.published, max_age_days)
    ]

    _logger.debug(
        "Headlines for %s: %d raw → %d deduped"
        " → %d within %dd",
        ticker,
        len(items),
        len(deduped),
        len(recent),
        max_age_days,
    )
    return recent


# ------------------------------------------------------------------
# Per-source fetchers
# ------------------------------------------------------------------


def _fetch_yfinance(
    ticker: str,
) -> list[HeadlineItem]:
    """Fetch headlines via yfinance ``Ticker.news``."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    news = t.news or []
    items: list[HeadlineItem] = []

    for item in news[:_MAX_PER_SOURCE]:
        c = item.get("content", item)
        title = c.get("title") or item.get("title", "")
        if not title:
            continue
        pub = c.get("pubDate") or item.get("providerPublishTime", "")
        items.append(
            HeadlineItem(
                title=title,
                source="yfinance",
                weight=_WEIGHT_YFINANCE,
                published=str(pub) if pub else "",
            )
        )
    return items


def _fetch_yahoo_rss(
    ticker: str,
) -> list[HeadlineItem]:
    """Fetch headlines via Yahoo Finance RSS feed."""
    import feedparser

    url = (
        "https://feeds.finance.yahoo.com/rss/2.0/"
        f"headline?s={quote_plus(ticker)}"
        "&region=US&lang=en-US"
    )
    feed = feedparser.parse(url)
    items: list[HeadlineItem] = []

    for entry in feed.entries[:_MAX_PER_SOURCE]:
        title = getattr(entry, "title", "")
        if not title:
            continue
        items.append(
            HeadlineItem(
                title=title,
                source="yahoo_rss",
                weight=_WEIGHT_YAHOO_RSS,
                published=getattr(entry, "published", ""),
            )
        )
    return items


def _fetch_google_rss(
    ticker: str,
) -> list[HeadlineItem]:
    """Fetch headlines via Google News RSS feed."""
    import feedparser

    url = (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(ticker + ' stock')}"
        "&hl=en-US&gl=US&ceid=US:en"
    )
    feed = feedparser.parse(url)
    items: list[HeadlineItem] = []

    for entry in feed.entries[:_MAX_PER_SOURCE]:
        title = getattr(entry, "title", "")
        if not title:
            continue
        items.append(
            HeadlineItem(
                title=title,
                source="google_rss",
                weight=_WEIGHT_GOOGLE_RSS,
                published=getattr(entry, "published", ""),
            )
        )
    return items


# ------------------------------------------------------------------
# Deduplication
# ------------------------------------------------------------------


def _deduplicate(
    items: list[HeadlineItem],
    threshold: float = _DEDUP_THRESHOLD,
) -> list[HeadlineItem]:
    """Remove duplicate headlines by fuzzy title match.

    For each pair with similarity ≥ ``threshold``, the
    item with the lower source weight is dropped.

    Args:
        items: Raw headlines from all sources.
        threshold: Minimum similarity ratio to consider
            as duplicate (0.0–1.0).

    Returns:
        Deduplicated list sorted by weight descending.
    """
    if len(items) <= 1:
        return list(items)

    from tools._date_utils import parse_published

    def _sort_key(h: HeadlineItem):
        dt = parse_published(h.published)
        ts = dt.timestamp() if dt else 0.0
        return (h.weight, ts)

    # Sort by weight desc, then recency desc.
    ranked = sorted(
        items,
        key=_sort_key,
        reverse=True,
    )
    keep: list[HeadlineItem] = []

    for candidate in ranked:
        c_lower = candidate.title.lower()
        is_dup = False
        for kept in keep:
            ratio = SequenceMatcher(
                None,
                c_lower,
                kept.title.lower(),
            ).ratio()
            if ratio >= threshold:
                is_dup = True
                break
        if not is_dup:
            keep.append(candidate)

    return keep
