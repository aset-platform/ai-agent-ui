"""Market index ticker — Nifty 50 + Sensex.

Provides ``GET /market/indices`` for the header ticker.
Dual-source: NSE India (Nifty) + Yahoo Finance (Sensex).
Redis cache (30s market / 300s off-hours) + PG persistence.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime

import httpx
import pytz
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select

from auth.dependencies import get_current_user
from backend.db.engine import get_session_factory
from backend.db.models.market_index import MarketIndex
from cache import get_cache

_logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_CACHE_KEY = "market:indices"
_TTL_MARKET_OPEN = 30
_TTL_MARKET_CLOSED = 300
_UPSTREAM_TIMEOUT = 10.0
# Yahoo's ^BSESN feed periodically freezes mid-session
# (stops emitting new ticks). When the last trade time is
# older than this during market hours, fall back to Google.
_YAHOO_STALE_SECONDS = 300

_nse_client: httpx.AsyncClient | None = None
_yahoo_client: httpx.AsyncClient | None = None
_yahoo_crumb: str | None = None


def _is_market_open() -> bool:
    """True if IST is Mon-Fri 09:00-15:30."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_t = now.replace(
        hour=15, minute=30, second=0, microsecond=0,
    )
    return open_t <= now <= close_t


async def _needs_seed_today() -> bool:
    """True if PG has no row or row is from a previous day."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(MarketIndex).where(
                    MarketIndex.id == 1,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return True
    fetched_date = row.fetched_at.astimezone(IST).date()
    today_ist = datetime.now(IST).date()
    return fetched_date < today_ist


async def _get_nse_client() -> httpx.AsyncClient:
    global _nse_client
    if _nse_client is None or _nse_client.is_closed:
        _nse_client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                    "10_15_7) AppleWebKit/537.36 (KHTML, "
                    "like Gecko) Chrome/120.0.0.0 Safari/"
                    "537.36"
                ),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=_UPSTREAM_TIMEOUT,
            follow_redirects=True,
        )
        await _nse_client.get("https://www.nseindia.com")
    return _nse_client


async def _fetch_nifty() -> dict | None:
    """Fetch Nifty 50 from NSE India /api/allIndices."""
    try:
        client = await _get_nse_client()
        resp = await client.get(
            "https://www.nseindia.com/api/allIndices",
        )
        if resp.status_code == 403:
            global _nse_client
            _nse_client = None
            client = await _get_nse_client()
            resp = await client.get(
                "https://www.nseindia.com/api/allIndices",
            )
        resp.raise_for_status()
        data = resp.json()
        for idx in data.get("data", []):
            if idx.get("index") == "NIFTY 50":
                return {
                    "price": idx["last"],
                    "change": idx["variation"],
                    "change_pct": idx["percentChange"],
                    "prev_close": idx["previousClose"],
                    "open": idx["open"],
                    "high": idx["high"],
                    "low": idx["low"],
                }
        _logger.warning("NIFTY 50 not found in NSE response")
        return None
    except Exception:
        _logger.warning(
            "NSE India fetch failed", exc_info=True,
        )
        return None


async def _get_yahoo_client() -> httpx.AsyncClient:
    global _yahoo_client
    if _yahoo_client is None or _yahoo_client.is_closed:
        _yahoo_client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                    "10_15_7) AppleWebKit/537.36"
                ),
            },
            timeout=_UPSTREAM_TIMEOUT,
            follow_redirects=True,
        )
    return _yahoo_client


async def _refresh_yahoo_crumb() -> str | None:
    global _yahoo_crumb
    try:
        client = await _get_yahoo_client()
        await client.get("https://fc.yahoo.com")
        resp = await client.get(
            "https://query2.finance.yahoo.com"
            "/v1/test/getcrumb",
        )
        resp.raise_for_status()
        _yahoo_crumb = resp.text.strip()
        return _yahoo_crumb
    except Exception:
        _logger.warning(
            "Yahoo crumb refresh failed", exc_info=True,
        )
        return None


async def _fetch_yahoo_quote(
    symbol: str,
) -> tuple[dict | None, str]:
    """Fetch a quote from Yahoo Finance v7.

    Returns (data_dict | None, market_state).
    """
    market_state = "CLOSED"
    try:
        client = await _get_yahoo_client()
        if _yahoo_crumb is None:
            await _refresh_yahoo_crumb()
        if _yahoo_crumb is None:
            return None, market_state

        url = (
            "https://query2.finance.yahoo.com"
            "/v7/finance/quote"
            f"?symbols={symbol}&crumb={_yahoo_crumb}"
        )
        resp = await client.get(url)
        if resp.status_code == 401:
            await _refresh_yahoo_crumb()
            if _yahoo_crumb is None:
                return None, market_state
            url = (
                "https://query2.finance.yahoo.com"
                "/v7/finance/quote"
                f"?symbols={symbol}"
                f"&crumb={_yahoo_crumb}"
            )
            resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        results = (
            data.get("quoteResponse", {}).get("result", [])
        )
        if not results:
            _logger.warning(
                "No results for %s in Yahoo response",
                symbol,
            )
            return None, market_state

        q = results[0]
        market_state = q.get("marketState", "CLOSED")
        return {
            "price": q["regularMarketPrice"],
            "change": q["regularMarketChange"],
            "change_pct": q[
                "regularMarketChangePercent"
            ],
            "prev_close": q[
                "regularMarketPreviousClose"
            ],
            "open": q.get("regularMarketOpen", 0),
            "high": q.get("regularMarketDayHigh", 0),
            "low": q.get("regularMarketDayLow", 0),
            "_last_trade_ts": q.get(
                "regularMarketTime", 0,
            ),
        }, market_state
    except Exception:
        _logger.warning(
            "Yahoo Finance fetch failed for %s",
            symbol,
            exc_info=True,
        )
        return None, market_state


def _is_yahoo_quote_stale(quote: dict) -> bool:
    """True if Yahoo's quote is too old during market hours."""
    if not _is_market_open():
        return False
    ts = quote.get("_last_trade_ts") or 0
    if ts <= 0:
        return False
    return (time.time() - ts) > _YAHOO_STALE_SECONDS


async def _fetch_google_finance_price(
    ticker: str,
) -> float | None:
    """Scrape last price from Google Finance.

    Used as a fallback when the primary Yahoo feed freezes
    (a recurring ^BSESN issue during market hours).
    """
    url = f"https://www.google.com/finance/quote/{ticker}"
    try:
        client = await _get_yahoo_client()
        resp = await client.get(url)
        resp.raise_for_status()
        m = re.search(
            r'data-last-price="([\d.]+)"', resp.text,
        )
        if m is None:
            _logger.warning(
                "Google Finance price not found for %s",
                ticker,
            )
            return None
        return float(m.group(1))
    except Exception:
        _logger.warning(
            "Google Finance fetch failed for %s",
            ticker, exc_info=True,
        )
        return None


async def _fetch_sensex() -> tuple[dict | None, str]:
    """Fetch Sensex — Yahoo primary, Google fallback.

    Yahoo's ^BSESN feed freezes mid-session; when stale we
    overlay Google Finance's live price on Yahoo's other
    fields (prev_close stays valid intraday).
    """
    quote, market_state = await _fetch_yahoo_quote("^BSESN")
    if quote is None or _is_yahoo_quote_stale(quote):
        live = await _fetch_google_finance_price(
            "SENSEX:INDEXBOM",
        )
        if live is not None:
            base = quote or {}
            prev = base.get("prev_close") or live
            change = live - prev
            change_pct = (
                (change / prev * 100) if prev else 0.0
            )
            return {
                "price": live,
                "change": change,
                "change_pct": change_pct,
                "prev_close": prev,
                "open": base.get("open", 0),
                "high": base.get("high", 0),
                "low": base.get("low", 0),
            }, market_state or "REGULAR"
    return quote, market_state


async def _fetch_nifty_yahoo() -> dict | None:
    """Fallback: fetch Nifty from Yahoo (^NSEI)."""
    data, _ = await _fetch_yahoo_quote("^NSEI")
    return data


async def _persist_to_pg(
    nifty: dict,
    sensex: dict,
    market_state: str,
    fetched_at: datetime,
) -> None:
    """Upsert the single row in stocks.market_indices."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(MarketIndex).where(
                    MarketIndex.id == 1,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = MarketIndex(
                id=1,
                nifty_data=nifty,
                sensex_data=sensex,
                market_state=market_state,
                fetched_at=fetched_at,
            )
            session.add(row)
        else:
            row.nifty_data = nifty
            row.sensex_data = sensex
            row.market_state = market_state
            row.fetched_at = fetched_at
        await session.commit()


async def _read_from_pg() -> dict | None:
    """Read persisted data from PG."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(MarketIndex).where(
                    MarketIndex.id == 1,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "nifty": row.nifty_data,
        "sensex": row.sensex_data,
        "market_state": "CLOSED",
        "timestamp": row.fetched_at.isoformat(),
        "stale": False,
    }


async def _fetch_and_cache() -> dict | None:
    """Fetch from upstreams, cache in Redis + PG."""
    nifty_task = asyncio.create_task(_fetch_nifty())
    sensex_task = asyncio.create_task(_fetch_sensex())
    nifty, (sensex, market_state) = await asyncio.gather(
        nifty_task, sensex_task,
    )

    if nifty is None:
        nifty = await _fetch_nifty_yahoo()

    if nifty is None and sensex is None:
        return None

    now = datetime.now(IST)
    result = {
        "nifty": nifty or {},
        "sensex": sensex or {},
        "market_state": market_state,
        "timestamp": now.isoformat(),
        "stale": False,
    }

    cache = get_cache()
    ttl = (
        _TTL_MARKET_OPEN if _is_market_open()
        else _TTL_MARKET_CLOSED
    )
    cache.set(_CACHE_KEY, json.dumps(result), ttl=ttl)

    if nifty and sensex:
        await _persist_to_pg(
            nifty, sensex, market_state, now,
        )

    return result


def create_market_router() -> APIRouter:
    """Build the ``/market`` router."""
    router = APIRouter(
        prefix="/market",
        tags=["market"],
    )

    @router.get("/indices")
    async def get_indices(
        _user=Depends(get_current_user),
    ) -> JSONResponse:
        """Return Nifty 50 + Sensex with cache."""
        cache = get_cache()
        hit = cache.get(_CACHE_KEY)
        if hit is not None:
            return JSONResponse(content=json.loads(hit))

        if not _is_market_open():
            needs_seed = await _needs_seed_today()
            if not needs_seed:
                pg_data = await _read_from_pg()
                if pg_data is not None:
                    cache.set(
                        _CACHE_KEY,
                        json.dumps(pg_data),
                        ttl=_TTL_MARKET_CLOSED,
                    )
                    return JSONResponse(content=pg_data)

        result = await _fetch_and_cache()
        if result is not None:
            return JSONResponse(content=result)

        pg_data = await _read_from_pg()
        if pg_data is not None:
            pg_data["stale"] = True
            return JSONResponse(content=pg_data)

        return JSONResponse(
            status_code=503,
            content={
                "detail": "Market data unavailable",
            },
        )

    return router
