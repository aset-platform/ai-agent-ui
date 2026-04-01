"""Tiered financial news tools.

Priority chain: Redis cache → yfinance .news (free) →
Google News RSS (free) → SerpAPI (paid, last resort).

These tools replace the general ``search_web`` tool for
the Research Agent.  They only return financial news —
never general web search results.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote_plus

import yfinance as yf
from langchain_core.tools import tool

_logger = logging.getLogger(__name__)

# Attempt feedparser import (for Google News RSS).
try:
    import feedparser  # type: ignore[import-untyped]

    _HAS_FEEDPARSER = True
except ImportError:
    _HAS_FEEDPARSER = False
    _logger.info(
        "feedparser not installed — Google News " "RSS fallback disabled",
    )


def _get_redis():
    """Lazily import Redis cache service."""
    try:
        from cache import cache_service

        return cache_service
    except Exception:
        return None


def _cache_get(key: str) -> str | None:
    """Read from Redis cache."""
    svc = _get_redis()
    if svc is None:
        return None
    return svc.get(key)


def _cache_set(key: str, val: str, ttl: int):
    """Write to Redis cache."""
    svc = _get_redis()
    if svc is None:
        return
    svc.set(key, val, ttl)


# ---------------------------------------------------------------
# Tool 1: get_ticker_news
# ---------------------------------------------------------------


@tool
def get_ticker_news(
    ticker: str,
    days_back: int = 7,
) -> str:
    """Get recent news headlines for a stock ticker.

    Sources (in order): yfinance .news → Google News RSS.
    Always fetches fresh — no Redis cache.  Never calls
    SerpAPI.  Results filtered to last ``days_back`` days.

    Args:
        ticker: Stock ticker symbol (e.g. AAPL,
            RELIANCE.NS).
        days_back: Number of days of news to return.
            Defaults to 7.  Use 30 for "last month",
            90 for "last quarter".

    Returns:
        Formatted news headlines with dates and
        publishers.
    """
    articles: list[dict] = []

    # 2. yfinance .news (free)
    # yfinance >= 1.2 nests data under "content".
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
        for item in news[:8]:
            c = item.get("content", item)
            prov = c.get("provider", {})
            canon = c.get(
                "canonicalUrl",
                {},
            )
            articles.append(
                {
                    "title": (c.get("title") or item.get("title", "")),
                    "publisher": (
                        prov.get("displayName") or item.get("publisher", "")
                    ),
                    "link": (canon.get("url") or item.get("link", "")),
                    "date": (
                        c.get("pubDate")
                        or item.get(
                            "providerPublishTime",
                            "",
                        )
                    ),
                }
            )
    except Exception:
        _logger.debug(
            "yfinance news failed for %s",
            ticker,
        )

    # 3. Google News RSS fallback (free)
    if len(articles) < 3 and _HAS_FEEDPARSER:
        try:
            url = (
                "https://news.google.com/rss/search"
                f"?q={quote_plus(ticker + ' stock')}"
                "&hl=en&gl=US&ceid=US:en"
            )
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                if not any(a["title"] == entry.title for a in articles):
                    articles.append(
                        {
                            "title": entry.title,
                            "publisher": entry.get(
                                "source",
                                {},
                            ).get("title", "Google News"),
                            "link": entry.link,
                            "date": entry.get(
                                "published",
                                "",
                            ),
                        }
                    )
        except Exception:
            _logger.debug(
                "Google News RSS failed for %s",
                ticker,
            )

    if not articles:
        return f"No news found for {ticker}."

    # Filter by recency.
    from tools._date_utils import is_within_window

    articles = [
        a for a in articles
        if is_within_window(str(a.get("date", "")), days_back)
    ]

    if not articles:
        return (
            f"No recent news for {ticker} in the "
            f"last {days_back} days."
        )

    # Format output
    lines = [
        f"**News for {ticker}** "
        f"(last {days_back} days)\n",
    ]
    for i, a in enumerate(articles[:8], 1):
        lines.append(
            f"{i}. **{a['title']}**\n"
            f"   {a['publisher']} "
            f"| {a['date']}\n"
        )

    result = "\n".join(lines)
    return f"[Source: yfinance/rss]\n{result}"


# ---------------------------------------------------------------
# Tool 2: get_analyst_recommendations
# ---------------------------------------------------------------


@tool
def get_analyst_recommendations(ticker: str) -> str:
    """Get analyst buy/hold/sell recommendations.

    Source: Redis cache (24h) → yfinance (free).
    Uses ``upgrades_downgrades`` for per-analyst ratings
    and ``recommendations`` for consensus summary.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Recent analyst ratings with dates and firms.
    """
    cache_key = f"cache:news:analyst:{ticker}"

    cached = _cache_get(cache_key)
    if cached:
        return f"[Source: cache]\n{cached}"

    try:
        t = yf.Ticker(ticker)

        lines = [
            f"**Analyst Recommendations for " f"{ticker}**\n",
        ]

        # Per-analyst upgrades/downgrades (yfinance 1.2+)
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            recent = ud.sort_index(
                ascending=False,
            ).head(10)
            for idx, row in recent.iterrows():
                dt = str(idx)[:10]
                firm = row.get("Firm", "Unknown")
                grade = row.get("ToGrade", "")
                action = row.get("Action", "")
                pt = row.get(
                    "currentPriceTarget",
                    "",
                )
                pt_str = f" (PT: ${pt})" if pt else ""
                lines.append(
                    f"- {dt}: **{firm}** — " f"{action} → {grade}{pt_str}"
                )

        # Consensus summary (buy/hold/sell counts)
        recs = t.recommendations
        if recs is not None and not recs.empty:
            curr = recs.iloc[0]
            lines.append(
                f"\n**Consensus (current month):** "
                f"Strong Buy: {curr.get('strongBuy', 0)}"
                f" | Buy: {curr.get('buy', 0)}"
                f" | Hold: {curr.get('hold', 0)}"
                f" | Sell: {curr.get('sell', 0)}"
                f" | Strong Sell: "
                f"{curr.get('strongSell', 0)}"
            )

        if len(lines) <= 1:
            return f"No analyst recommendations " f"found for {ticker}."

        result = "\n".join(lines)
        _cache_set(cache_key, result, 86400)
        return f"[Source: yfinance]\n{result}"

    except Exception as exc:
        _logger.warning(
            "Analyst recs failed for %s: %s",
            ticker,
            exc,
        )
        return f"Could not fetch analyst " f"recommendations for {ticker}."


# ---------------------------------------------------------------
# Tool 3: search_financial_news
# ---------------------------------------------------------------


@tool
def search_financial_news(
    query: str,
    days_back: int = 7,
) -> str:
    """Search for recent financial news across sources.

    Priority: Redis cache → yfinance .news for
    extracted tickers → Google News RSS → SerpAPI
    (LAST RESORT, paid — only if free sources
    return <3 results).  Results filtered to last
    ``days_back`` days.

    Args:
        query: Financial news search query.
        days_back: Number of days of news to return.
            Defaults to 7.  Use 30 for "last month",
            90 for "last quarter".

    Returns:
        Aggregated news headlines from available
        sources.
    """
    q_norm = re.sub(r'[^a-z0-9]', '_', query.lower())
    cache_key = (
        f"cache:news:search:{q_norm}:{days_back}"
    )

    cached = _cache_get(cache_key)
    if cached:
        return f"[Source: cache]\n{cached}"

    articles: list[dict] = []

    # Extract tickers from query
    ticker_pattern = re.compile(
        r"\b[A-Z]{1,5}(?:\.[A-Z]{1,2})?\b",
    )
    tickers = [
        t
        for t in ticker_pattern.findall(query)
        if t
        not in {
            "I",
            "A",
            "IT",
            "IS",
            "AM",
            "AN",
            "AT",
            "AS",
            "THE",
            "AND",
            "FOR",
        }
    ]

    # 1. yfinance .news for each ticker (free)
    for ticker in tickers[:3]:
        try:
            t = yf.Ticker(ticker)
            news = t.news or []
            for item in news[:3]:
                articles.append(
                    {
                        "title": item.get("title", ""),
                        "publisher": item.get(
                            "publisher",
                            "",
                        ),
                        "date": item.get(
                            "providerPublishTime",
                            "",
                        ),
                    }
                )
        except Exception:
            pass

    # 2. Google News RSS (free)
    if len(articles) < 3 and _HAS_FEEDPARSER:
        try:
            url = (
                "https://news.google.com/rss/search"
                f"?q={quote_plus(query)}"
                "&hl=en&gl=US&ceid=US:en"
            )
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                if not any(a["title"] == entry.title for a in articles):
                    articles.append(
                        {
                            "title": entry.title,
                            "publisher": entry.get(
                                "source",
                                {},
                            ).get(
                                "title",
                                "Google News",
                            ),
                            "date": entry.get(
                                "published",
                                "",
                            ),
                        }
                    )
        except Exception:
            pass

    # 3. SerpAPI — LAST RESORT (paid)
    if len(articles) < 3:
        try:
            from config import get_settings

            settings = get_settings()
            if settings.serpapi_api_key:
                from langchain_community.utilities import (  # noqa: E501
                    SerpAPIWrapper,
                )

                search = SerpAPIWrapper(
                    serpapi_api_key=(settings.serpapi_api_key),
                )
                raw = search.run(
                    f"{query} stock market news",
                )
                _logger.info(
                    "SerpAPI called for: %s",
                    query[:50],
                )
                articles.append(
                    {
                        "title": raw[:300],
                        "publisher": "SerpAPI",
                        "date": "",
                    }
                )
        except Exception:
            _logger.debug(
                "SerpAPI fallback failed for: %s",
                query,
            )

    if not articles:
        return f"No financial news found for: {query}"

    # Filter by recency.
    from tools._date_utils import is_within_window

    articles = [
        a for a in articles
        if is_within_window(
            str(a.get("date", "")), days_back,
        )
    ]

    if not articles:
        return (
            f"No recent financial news for: {query} "
            f"in the last {days_back} days."
        )

    lines = [
        f"**Financial News: {query}** "
        f"(last {days_back} days)\n",
    ]
    for i, a in enumerate(articles[:8], 1):
        lines.append(
            f"{i}. **{a['title']}**\n"
            f"   {a['publisher']} "
            f"| {a['date']}\n"
        )

    result = "\n".join(lines)
    _cache_set(cache_key, result, 3600)

    sources = "yfinance/rss"
    if any(a["publisher"] == "SerpAPI" for a in articles):
        sources = "yfinance/rss/serpapi"
    return f"[Source: {sources}]\n{result}"
