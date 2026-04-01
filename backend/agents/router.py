"""Keyword-based intent router for agent delegation.

Classifies user input and returns the appropriate
agent ID.  Replaces manual agent selection in the
frontend — users no longer need to pick an agent.

The router is intentionally simple (keyword matching)
to add zero latency.  Can be upgraded to LLM-based
classification later without changing the interface.
"""

from __future__ import annotations

import logging
import re

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Guardrail — blocked topic keywords (pre-filter)
# ---------------------------------------------------------------

_BLOCKED_KEYWORDS: set[str] = {
    "porn",
    "pornography",
    "xxx",
    "nude",
    "nudes",
    "naked",
    "sex",
    "sexual",
    "hentai",
    "erotic",
    "kill",
    "murder",
    "bomb",
    "weapon",
    "weapons",
    "gun",
    "guns",
    "torture",
    "terroris",
    "suicide",
    "hack",
    "hacking",
    "exploit",
    "malware",
    "ransomware",
    "phishing",
    "drugs",
    "cocaine",
    "heroin",
    "meth",
}

# Response when blocked content is detected.
BLOCKED_RESPONSE = (
    "I'm a financial assistant on the ASET Platform "
    "and can only help with stock analysis, portfolio "
    "management, and market-related questions. "
    "I cannot assist with that topic. "
    "Please ask about stocks, forecasts, or your "
    "portfolio instead."
)


def is_blocked(user_input: str) -> bool:
    """Return True if input contains blocked content.

    Uses simple keyword matching as a first layer.
    The LLM system prompts provide a second layer.
    """
    tokens = set(
        re.findall(r"[a-z]+", user_input.lower()),
    )
    if tokens & _BLOCKED_KEYWORDS:
        _logger.warning(
            "Guardrail blocked input: matched=%s",
            tokens & _BLOCKED_KEYWORDS,
        )
        return True
    return False


# ---------------------------------------------------------------
# Stock/financial intent keywords
# ---------------------------------------------------------------

# Keywords that signal stock/financial intent.
# Checked as whole-word matches (case-insensitive).
_STOCK_KEYWORDS: set[str] = {
    "stock",
    "stocks",
    "share",
    "shares",
    "sector",
    "sectors",
    "ticker",
    "tickers",
    "analyze",
    "analyse",
    "analysis",
    "forecast",
    "price",
    "prices",
    "dividend",
    "dividends",
    "portfolio",
    "market",
    "markets",
    "bull",
    "bear",
    "bearish",
    "bullish",
    "nifty",
    "sensex",
    "nasdaq",
    "s&p",
    "dow",
    "bse",
    "nse",
    "rsi",
    "macd",
    "sma",
    "ema",
    "candlestick",
    "ohlc",
    "volume",
    "earnings",
    "revenue",
    "eps",
    "pe",
    "roe",
    "debt",
    "quarterly",
    "annual",
    "returns",
    "volatility",
    "sharpe",
    "drawdown",
    "hedge",
    "etf",
    "mutual",
    "fund",
    "invest",
    "investing",
    "investment",
    "trader",
    "trading",
    "buy",
    "sell",
    "hold",
    "watchlist",
    "sector",
    "industry",
    "ipo",
    "rally",
    "crash",
    "correction",
    "breakout",
    "support",
    "resistance",
}

# Patterns that look like ticker symbols (1-5 uppercase
# letters, optionally with .NS / .BO suffix).
_TICKER_PATTERN = re.compile(
    r"\b[A-Z]{1,5}(?:\.[A-Z]{1,2})?\b"
)


def route(
    user_input: str,
    history: list[dict] | None = None,
) -> str:
    """Classify user intent and return agent ID.

    Args:
        user_input: The user's message text.
        history: Optional conversation history
            (reserved for future context-aware routing).

    Returns:
        ``"stock"`` for financial queries,
        ``"general"`` otherwise.
    """
    text = user_input.lower()
    tokens = set(re.findall(r"[a-z&]+", text))

    if tokens & _STOCK_KEYWORDS:
        _logger.debug(
            "Router → stock (keyword match in: %s)",
            tokens & _STOCK_KEYWORDS,
        )
        return "stock"

    # Check for ticker-like symbols (e.g. AAPL, RELIANCE.NS)
    if _TICKER_PATTERN.search(user_input):
        candidates = _TICKER_PATTERN.findall(
            user_input,
        )
        # Filter out common English words
        _common = {
            "I", "A", "IT", "IS", "AM", "AN",
            "AT", "AS", "BE", "BY", "DO", "GO",
            "HE", "IF", "IN", "ME", "MY", "NO",
            "OF", "OK", "ON", "OR", "SO", "TO",
            "UP", "US", "WE",
        }
        tickers = [
            c for c in candidates
            if c not in _common
        ]
        if tickers:
            _logger.debug(
                "Router → stock (ticker pattern: %s)",
                tickers,
            )
            return "stock"

    _logger.debug("Router → general")
    return "general"
