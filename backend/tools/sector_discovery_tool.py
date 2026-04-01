"""Sector-based stock discovery tool.

Queries Iceberg ``company_info`` for stocks in a given sector,
checks analysis freshness, and falls back to a curated list of
popular stocks when Iceberg has no data.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from langchain_core.tools import tool
from tools._stock_shared import _get_repo

_logger = logging.getLogger(__name__)

# Sector name aliases for fuzzy matching.
_SECTOR_ALIASES: dict[str, str] = {
    "banking": "Financial Services",
    "banks": "Financial Services",
    "finance": "Financial Services",
    "financials": "Financial Services",
    "nbfc": "Financial Services",
    "tech": "Technology",
    "it": "Technology",
    "software": "Technology",
    "pharma": "Healthcare",
    "healthcare": "Healthcare",
    "health": "Healthcare",
    "energy": "Energy",
    "oil": "Energy",
    "power": "Utilities",
    "utilities": "Utilities",
    "fmcg": "Consumer Defensive",
    "consumer": "Consumer Defensive",
    "auto": "Consumer Cyclical",
    "automobile": "Consumer Cyclical",
    "real estate": "Real Estate",
    "realty": "Real Estate",
    "metals": "Basic Materials",
    "mining": "Basic Materials",
    "telecom": "Communication Services",
    "media": "Communication Services",
    "infra": "Industrials",
    "infrastructure": "Industrials",
    "industrial": "Industrials",
}

# Curated popular stocks per sector (Indian + US).
_POPULAR_SECTOR_STOCKS: dict[str, list[dict]] = {
    "Financial Services": [
        {"ticker": "SBIN.NS", "name": "State Bank of India"},
        {"ticker": "HDFCBANK.NS", "name": "HDFC Bank"},
        {"ticker": "ICICIBANK.NS", "name": "ICICI Bank"},
        {"ticker": "KOTAKBANK.NS", "name": "Kotak Mahindra Bank"},
        {"ticker": "AXISBANK.NS", "name": "Axis Bank"},
        {"ticker": "JPM", "name": "JPMorgan Chase"},
        {"ticker": "BAC", "name": "Bank of America"},
        {"ticker": "GS", "name": "Goldman Sachs"},
    ],
    "Technology": [
        {"ticker": "TCS.NS", "name": "Tata Consultancy Services"},
        {"ticker": "INFY.NS", "name": "Infosys"},
        {"ticker": "WIPRO.NS", "name": "Wipro"},
        {"ticker": "HCLTECH.NS", "name": "HCL Technologies"},
        {"ticker": "AAPL", "name": "Apple"},
        {"ticker": "MSFT", "name": "Microsoft"},
        {"ticker": "GOOGL", "name": "Alphabet"},
    ],
    "Healthcare": [
        {"ticker": "SUNPHARMA.NS", "name": "Sun Pharma"},
        {"ticker": "DRREDDY.NS", "name": "Dr. Reddy's"},
        {"ticker": "CIPLA.NS", "name": "Cipla"},
        {"ticker": "DIVISLAB.NS", "name": "Divi's Laboratories"},
        {"ticker": "JNJ", "name": "Johnson & Johnson"},
        {"ticker": "PFE", "name": "Pfizer"},
    ],
    "Energy": [
        {"ticker": "RELIANCE.NS", "name": "Reliance Industries"},
        {"ticker": "ONGC.NS", "name": "ONGC"},
        {"ticker": "BPCL.NS", "name": "BPCL"},
        {"ticker": "XOM", "name": "Exxon Mobil"},
        {"ticker": "CVX", "name": "Chevron"},
    ],
    "Consumer Defensive": [
        {"ticker": "HINDUNILVR.NS", "name": "Hindustan Unilever"},
        {"ticker": "ITC.NS", "name": "ITC"},
        {"ticker": "NESTLEIND.NS", "name": "Nestle India"},
        {"ticker": "BRITANNIA.NS", "name": "Britannia Industries"},
    ],
    "Industrials": [
        {"ticker": "LT.NS", "name": "Larsen & Toubro"},
        {"ticker": "ADANIENT.NS", "name": "Adani Enterprises"},
        {"ticker": "SIEMENS.NS", "name": "Siemens India"},
    ],
    "Utilities": [
        {"ticker": "POWERGRID.NS", "name": "Power Grid Corp"},
        {"ticker": "NTPC.NS", "name": "NTPC"},
        {"ticker": "TATAPOWER.NS", "name": "Tata Power"},
    ],
    "Communication Services": [
        {"ticker": "BHARTIARTL.NS", "name": "Bharti Airtel"},
        {"ticker": "IDEA.NS", "name": "Vodafone Idea"},
    ],
}


def _normalize_sector(sector: str) -> str:
    """Resolve sector aliases to canonical names.

    Args:
        sector: User-provided sector string.

    Returns:
        Canonical sector name.
    """
    lower = sector.strip().lower()
    return _SECTOR_ALIASES.get(lower, sector.strip())


def _check_freshness(
    ticker: str,
    repo,
) -> tuple[str, str | None]:
    """Check analysis freshness for a ticker.

    Args:
        ticker: Stock ticker symbol.
        repo: StockRepository instance.

    Returns:
        Tuple of (status, last_analysed_date_str).
        status is "fresh", "stale", or "no_data".
    """
    try:
        latest = repo.get_latest_analysis_summary(ticker)
        if latest is None:
            return "no_data", None
        ad = latest.get("analysis_date")
        if ad is None:
            return "stale", None
        if hasattr(ad, "date"):
            ad = ad.date()
        if ad == date.today():
            return "fresh", str(ad)
        return "stale", str(ad)
    except Exception:
        return "no_data", None


@tool
def suggest_sector_stocks(sector: str) -> str:
    """Suggest stocks in a sector with data freshness status.

    Queries Iceberg for stocks matching the sector. Falls
    back to a curated popular-stocks list when Iceberg has
    no data. Each stock includes a freshness indicator:
    ``fresh`` (analysed today), ``stale`` (older), or
    ``no_data`` (never analysed).

    Args:
        sector: Sector name or alias, e.g.
            ``"Financial Services"``, ``"banking"``,
            ``"pharma"``.

    Returns:
        JSON string with sector, stocks list, and source.
    """
    canonical = _normalize_sector(sector)
    _logger.info(
        "suggest_sector_stocks | sector=%s "
        "(canonical=%s)",
        sector,
        canonical,
    )

    stocks: list[dict] = []
    source = "popular"

    repo = _get_repo()
    if repo is not None:
        try:
            df = repo.get_stocks_by_sector(canonical)
            if not df.empty:
                source = "iceberg"
                for _, row in df.iterrows():
                    ticker = row.get("ticker", "")
                    status, last_date = _check_freshness(
                        ticker, repo,
                    )
                    stocks.append({
                        "ticker": ticker,
                        "company_name": row.get(
                            "company_name", "",
                        ),
                        "status": status,
                        "last_analysed": last_date,
                    })
        except Exception as exc:
            _logger.warning(
                "Iceberg sector scan failed: %s", exc,
            )

    # Fallback to popular list if Iceberg empty
    if not stocks:
        popular = _POPULAR_SECTOR_STOCKS.get(
            canonical, [],
        )
        if not popular:
            # Try case-insensitive match
            for key, val in _POPULAR_SECTOR_STOCKS.items():
                if key.lower() == canonical.lower():
                    popular = val
                    break

        for s in popular:
            status = "no_data"
            last_date = None
            if repo is not None:
                status, last_date = _check_freshness(
                    s["ticker"], repo,
                )
            stocks.append({
                "ticker": s["ticker"],
                "company_name": s["name"],
                "status": status,
                "last_analysed": last_date,
            })

    if not stocks:
        available = sorted(_POPULAR_SECTOR_STOCKS.keys())
        return json.dumps({
            "sector": canonical,
            "stocks": [],
            "source": "none",
            "available_sectors": available,
        })

    return json.dumps({
        "sector": canonical,
        "stocks": stocks[:10],  # Cap at 10
        "source": source,
    })
