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
        "feedparser not installed — Google News "
        "RSS fallback disabled",
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
def get_ticker_news(ticker: str) -> str:
    """Get latest news headlines for a stock ticker.

    Sources (in order): Redis cache → yfinance .news →
    Google News RSS.  Never calls SerpAPI.

    Args:
        ticker: Stock ticker symbol (e.g. AAPL,
            RELIANCE.NS).

    Returns:
        Formatted news headlines with dates and
        publishers.
    """
    cache_key = f"cache:news:ticker:{ticker}"

    # 1. Redis cache (1h TTL)
    cached = _cache_get(cache_key)
    if cached:
        return f"[Source: cache]\n{cached}"

    articles: list[dict] = []

    # 2. yfinance .news (free)
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
        for item in news[:8]:
            articles.append({
                "title": item.get("title", ""),
                "publisher": item.get(
                    "publisher", "",
                ),
                "link": item.get("link", ""),
                "date": item.get(
                    "providerPublishTime", "",
                ),
            })
    except Exception:
        _logger.debug(
            "yfinance news failed for %s", ticker,
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
                if not any(
                    a["title"] == entry.title
                    for a in articles
                ):
                    articles.append({
                        "title": entry.title,
                        "publisher": entry.get(
                            "source", {},
                        ).get("title", "Google News"),
                        "link": entry.link,
                        "date": entry.get(
                            "published", "",
                        ),
                    })
        except Exception:
            _logger.debug(
                "Google News RSS failed for %s",
                ticker,
            )

    if not articles:
        return f"No news found for {ticker}."

    # Format output
    lines = [f"**Latest News for {ticker}**\n"]
    for i, a in enumerate(articles[:8], 1):
        lines.append(
            f"{i}. **{a['title']}**\n"
            f"   {a['publisher']} "
            f"| {a['date']}\n"
        )

    result = "\n".join(lines)
    _cache_set(cache_key, result, 3600)
    return f"[Source: yfinance/rss]\n{result}"


# ---------------------------------------------------------------
# Tool 2: get_analyst_recommendations
# ---------------------------------------------------------------


@tool
def get_analyst_recommendations(ticker: str) -> str:
    """Get analyst buy/hold/sell recommendations.

    Source: Redis cache (24h) → yfinance (free).

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
        recs = t.recommendations
        if recs is None or recs.empty:
            return (
                f"No analyst recommendations "
                f"found for {ticker}."
            )

        # Take last 10 recommendations
        recent = recs.tail(10).reset_index()
        lines = [
            f"**Analyst Recommendations for "
            f"{ticker}**\n"
        ]
        for _, row in recent.iterrows():
            date_str = str(row.get("Date", ""))[:10]
            firm = row.get("Firm", "Unknown")
            grade = row.get("To Grade", "")
            action = row.get("Action", "")
            lines.append(
                f"- {date_str}: **{firm}** — "
                f"{action} → {grade}"
            )

        result = "\n".join(lines)
        _cache_set(cache_key, result, 86400)
        return f"[Source: yfinance]\n{result}"

    except Exception as exc:
        _logger.warning(
            "Analyst recs failed for %s: %s",
            ticker, exc,
        )
        return (
            f"Could not fetch analyst "
            f"recommendations for {ticker}."
        )


# ---------------------------------------------------------------
# Tool 3: search_financial_news
# ---------------------------------------------------------------


@tool
def search_financial_news(query: str) -> str:
    """Search for financial news across multiple sources.

    Priority: Redis cache → yfinance .news for
    extracted tickers → Google News RSS → SerpAPI
    (LAST RESORT, paid — only if free sources
    return <3 results).

    Args:
        query: Financial news search query.

    Returns:
        Aggregated news headlines from available
        sources.
    """
    cache_key = (
        f"cache:news:search:"
        f"{re.sub(r'[^a-z0-9]', '_', query.lower())}"
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
        t for t in ticker_pattern.findall(query)
        if t not in {
            "I", "A", "IT", "IS", "AM", "AN",
            "AT", "AS", "THE", "AND", "FOR",
        }
    ]

    # 1. yfinance .news for each ticker (free)
    for ticker in tickers[:3]:
        try:
            t = yf.Ticker(ticker)
            news = t.news or []
            for item in news[:3]:
                articles.append({
                    "title": item.get("title", ""),
                    "publisher": item.get(
                        "publisher", "",
                    ),
                    "date": item.get(
                        "providerPublishTime", "",
                    ),
                })
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
                if not any(
                    a["title"] == entry.title
                    for a in articles
                ):
                    articles.append({
                        "title": entry.title,
                        "publisher": entry.get(
                            "source", {},
                        ).get(
                            "title", "Google News",
                        ),
                        "date": entry.get(
                            "published", "",
                        ),
                    })
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
                    serpapi_api_key=(
                        settings.serpapi_api_key
                    ),
                )
                raw = search.run(
                    f"{query} stock market news",
                )
                _logger.info(
                    "SerpAPI called for: %s",
                    query[:50],
                )
                articles.append({
                    "title": raw[:300],
                    "publisher": "SerpAPI",
                    "date": "",
                })
        except Exception:
            _logger.debug(
                "SerpAPI fallback failed for: %s",
                query,
            )

    if not articles:
        return f"No financial news found for: {query}"

    lines = [f"**Financial News: {query}**\n"]
    for i, a in enumerate(articles[:8], 1):
        lines.append(
            f"{i}. **{a['title']}**\n"
            f"   {a['publisher']} "
            f"| {a['date']}\n"
        )

    result = "\n".join(lines)
    _cache_set(cache_key, result, 3600)

    sources = "yfinance/rss"
    if any(
        a["publisher"] == "SerpAPI"
        for a in articles
    ):
        sources = "yfinance/rss/serpapi"
    return f"[Source: {sources}]\n{result}"
