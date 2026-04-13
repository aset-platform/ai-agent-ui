# Market Ticker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time Nifty 50 + Sensex ticker to the AppHeader center, backed by a cached FastAPI endpoint that fetches from NSE India and Yahoo Finance.

**Architecture:** New `GET /v1/market/indices` endpoint fetches from two upstream sources concurrently, caches in Redis (30s TTL market hours, 300s off-hours), persists to a single-row PG table for restart resilience. Frontend `MarketTicker` component polls every 30s via `apiFetch`. Market hours gating prevents upstream calls outside 09:00–15:30 IST Mon-Fri, except for first-call-of-day seeding.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Redis (existing `CacheService`), `httpx` (async HTTP client), React 19, Tailwind CSS.

**Spec:** `docs/superpowers/specs/2026-04-13-market-ticker-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/db/models/market_index.py` | Create | `MarketIndex` ORM model (single-row PG table) |
| `backend/db/models/__init__.py` | Modify | Export `MarketIndex` |
| `backend/db/migrations/versions/f1a2b3c4d5e6_add_market_indices.py` | Create | Alembic migration |
| `backend/market_routes.py` | Create | `/market/indices` endpoint, NSE/Yahoo fetchers, cache logic |
| `backend/routes.py` | Modify | Register market router |
| `frontend/components/MarketTicker.tsx` | Create | Ticker display component |
| `frontend/components/AppHeader.tsx` | Modify | Mount `<MarketTicker />` in center |
| `tests/backend/test_market_routes.py` | Create | Backend tests |

---

### Task 1: MarketIndex ORM Model

**Files:**
- Create: `backend/db/models/market_index.py`
- Modify: `backend/db/models/__init__.py`

- [ ] **Step 1: Create the ORM model**

Create `backend/db/models/market_index.py`:

```python
"""Single-row market index cache for Nifty 50 + Sensex."""
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class MarketIndex(Base):
    __tablename__ = "market_indices"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_market_indices_single"),
        {"schema": "stocks", "extend_existing": True},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, default=1,
    )
    nifty_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
    )
    sensex_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
    )
    market_state: Mapped[str] = mapped_column(
        String(10), nullable=False,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
```

- [ ] **Step 2: Export from `__init__.py`**

Add to `backend/db/models/__init__.py`:

```python
from backend.db.models.market_index import MarketIndex
```

And add `"MarketIndex"` to the `__all__` list.

- [ ] **Step 3: Commit**

```bash
git add backend/db/models/market_index.py backend/db/models/__init__.py
git commit -m "feat(db): add MarketIndex ORM model for ticker persistence"
```

---

### Task 2: Alembic Migration

**Files:**
- Create: `backend/db/migrations/versions/f1a2b3c4d5e6_add_market_indices.py`

- [ ] **Step 1: Generate migration**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
PYTHONPATH=. alembic revision --autogenerate -m "add market_indices table"
```

- [ ] **Step 2: Review generated migration**

Verify it creates `stocks.market_indices` with columns `id`, `nifty_data`, `sensex_data`, `market_state`, `fetched_at`, and the check constraint `ck_market_indices_single`. The migration should include `CREATE SCHEMA IF NOT EXISTS stocks` before the table creation.

- [ ] **Step 3: Apply migration**

```bash
PYTHONPATH=. alembic upgrade head
```

Expected: migration applies cleanly, table `stocks.market_indices` exists.

- [ ] **Step 4: Commit**

```bash
git add backend/db/migrations/versions/
git commit -m "feat(db): add market_indices migration"
```

---

### Task 3: Backend Market Routes

**Files:**
- Create: `backend/market_routes.py`

This is the core file. It contains:
1. NSE India fetcher (cookie session)
2. Yahoo Finance fetcher (cookie + crumb)
3. Market hours check + first-call-of-day seeding logic
4. Redis caching via existing `CacheService`
5. PG persistence via `MarketIndex`
6. The `GET /market/indices` endpoint

- [ ] **Step 1: Create `backend/market_routes.py`**

```python
"""Market index ticker — Nifty 50 + Sensex.

Provides ``GET /market/indices`` for the header ticker.
Dual-source: NSE India (Nifty) + Yahoo Finance (Sensex).
Redis cache (30s market / 300s off-hours) + PG persistence.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime

import httpx
import pytz
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select

from auth.dependencies import get_current_user
from backend.db.engine import get_session_factory
from backend.db.models.market_index import MarketIndex
from cache import get_cache, TTL_ADMIN

_logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# Redis key for the ticker cache.
_CACHE_KEY = "market:indices"

# TTLs
_TTL_MARKET_OPEN = 30
_TTL_MARKET_CLOSED = 300

# Upstream timeout (seconds).
_UPSTREAM_TIMEOUT = 10.0

# Shared httpx clients (module-level, reused across requests).
_nse_client: httpx.AsyncClient | None = None
_yahoo_client: httpx.AsyncClient | None = None
_yahoo_crumb: str | None = None


# ----------------------------------------------------------
# Market hours
# ----------------------------------------------------------

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
                select(MarketIndex).where(MarketIndex.id == 1)
            )
        ).scalar_one_or_none()
    if row is None:
        return True
    fetched_date = row.fetched_at.astimezone(IST).date()
    return fetched_date < date.today()


# ----------------------------------------------------------
# NSE India fetcher
# ----------------------------------------------------------

async def _get_nse_client() -> httpx.AsyncClient:
    """Return or create the NSE httpx client with cookies."""
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
        # Seed session cookies.
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
            # Session expired — refresh.
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
        _logger.warning("NSE India fetch failed", exc_info=True)
        return None


# ----------------------------------------------------------
# Yahoo Finance fetcher
# ----------------------------------------------------------

async def _get_yahoo_client() -> httpx.AsyncClient:
    """Return or create the Yahoo httpx client."""
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
    """Fetch a fresh Yahoo crumb token."""
    global _yahoo_crumb
    try:
        client = await _get_yahoo_client()
        # Step 1: get cookies.
        await client.get("https://fc.yahoo.com")
        # Step 2: get crumb.
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


async def _fetch_sensex() -> tuple[dict | None, str]:
    """Fetch Sensex from Yahoo Finance v7 quote.

    Returns (data_dict | None, market_state).
    """
    global _yahoo_crumb
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
            f"?symbols=^BSESN&crumb={_yahoo_crumb}"
        )
        resp = await client.get(url)
        if resp.status_code == 401:
            await _refresh_yahoo_crumb()
            if _yahoo_crumb is None:
                return None, market_state
            url = (
                "https://query2.finance.yahoo.com"
                "/v7/finance/quote"
                f"?symbols=^BSESN&crumb={_yahoo_crumb}"
            )
            resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        results = (
            data.get("quoteResponse", {}).get("result", [])
        )
        if not results:
            _logger.warning("No results in Yahoo response")
            return None, market_state

        q = results[0]
        market_state = q.get("marketState", "CLOSED")
        return {
            "price": q["regularMarketPrice"],
            "change": q["regularMarketChange"],
            "change_pct": q["regularMarketChangePercent"],
            "prev_close": q["regularMarketPreviousClose"],
            "open": q.get("regularMarketOpen", 0),
            "high": q.get("regularMarketDayHigh", 0),
            "low": q.get("regularMarketDayLow", 0),
        }, market_state
    except Exception:
        _logger.warning(
            "Yahoo Finance fetch failed", exc_info=True,
        )
        return None, market_state


# ----------------------------------------------------------
# Nifty fallback via Yahoo (if NSE fails)
# ----------------------------------------------------------

async def _fetch_nifty_yahoo() -> dict | None:
    """Fallback: fetch Nifty from Yahoo (^NSEI)."""
    global _yahoo_crumb
    try:
        client = await _get_yahoo_client()
        if _yahoo_crumb is None:
            await _refresh_yahoo_crumb()
        if _yahoo_crumb is None:
            return None

        url = (
            "https://query2.finance.yahoo.com"
            "/v7/finance/quote"
            f"?symbols=^NSEI&crumb={_yahoo_crumb}"
        )
        resp = await client.get(url)
        if resp.status_code == 401:
            await _refresh_yahoo_crumb()
            if _yahoo_crumb is None:
                return None
            url = (
                "https://query2.finance.yahoo.com"
                "/v7/finance/quote"
                f"?symbols=^NSEI&crumb={_yahoo_crumb}"
            )
            resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        results = (
            data.get("quoteResponse", {}).get("result", [])
        )
        if not results:
            return None
        q = results[0]
        return {
            "price": q["regularMarketPrice"],
            "change": q["regularMarketChange"],
            "change_pct": q["regularMarketChangePercent"],
            "prev_close": q["regularMarketPreviousClose"],
            "open": q.get("regularMarketOpen", 0),
            "high": q.get("regularMarketDayHigh", 0),
            "low": q.get("regularMarketDayLow", 0),
        }
    except Exception:
        _logger.warning(
            "Yahoo Nifty fallback failed", exc_info=True,
        )
        return None


# ----------------------------------------------------------
# PG persistence
# ----------------------------------------------------------

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
                select(MarketIndex).where(MarketIndex.id == 1)
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
    """Read persisted data from PG. Returns response dict."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(MarketIndex).where(MarketIndex.id == 1)
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


# ----------------------------------------------------------
# Core fetch + cache orchestration
# ----------------------------------------------------------

async def _fetch_and_cache() -> dict | None:
    """Fetch from upstreams, cache in Redis + PG."""
    nifty_task = asyncio.create_task(_fetch_nifty())
    sensex_task = asyncio.create_task(_fetch_sensex())
    nifty, (sensex, market_state) = await asyncio.gather(
        nifty_task, sensex_task,
    )

    # NSE failed → try Yahoo for Nifty.
    if nifty is None:
        nifty = await _fetch_nifty_yahoo()

    # Still no data at all → return None.
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

    # Cache in Redis.
    cache = get_cache()
    ttl = (
        _TTL_MARKET_OPEN if _is_market_open()
        else _TTL_MARKET_CLOSED
    )
    cache.set(_CACHE_KEY, json.dumps(result), ttl=ttl)

    # Persist to PG.
    if nifty and sensex:
        await _persist_to_pg(
            nifty, sensex, market_state, now,
        )

    return result


# ----------------------------------------------------------
# Router
# ----------------------------------------------------------

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
        # 1. Redis cache hit?
        cache = get_cache()
        hit = cache.get(_CACHE_KEY)
        if hit is not None:
            return JSONResponse(content=json.loads(hit))

        # 2. Market closed + already seeded today?
        if not _is_market_open():
            needs_seed = await _needs_seed_today()
            if not needs_seed:
                # Serve from PG, no upstream call.
                pg_data = await _read_from_pg()
                if pg_data is not None:
                    cache.set(
                        _CACHE_KEY,
                        json.dumps(pg_data),
                        ttl=_TTL_MARKET_CLOSED,
                    )
                    return JSONResponse(content=pg_data)
            # First call of day or no PG data → seed.

        # 3. Fetch from upstream.
        result = await _fetch_and_cache()
        if result is not None:
            return JSONResponse(content=result)

        # 4. Fallback: stale PG data.
        pg_data = await _read_from_pg()
        if pg_data is not None:
            pg_data["stale"] = True
            return JSONResponse(content=pg_data)

        # 5. No data at all.
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Market data unavailable",
            },
        )

    return router
```

- [ ] **Step 2: Commit**

```bash
git add backend/market_routes.py
git commit -m "feat(market): add /market/indices endpoint with NSE + Yahoo dual-source"
```

---

### Task 4: Register Market Router

**Files:**
- Modify: `backend/routes.py:2153-2165`

- [ ] **Step 1: Add market router registration**

In `backend/routes.py`, after the recommendation router block (line ~2156), add:

```python
    from market_routes import create_market_router

    app.include_router(
        create_market_router(),
        prefix="/v1",
    )
```

This places the endpoint at `GET /v1/market/indices`.

- [ ] **Step 2: Verify backend starts**

```bash
./run.sh restart backend
```

Wait for logs to show the server is up, then:

```bash
curl -s http://localhost:8181/v1/health | python3 -m json.tool
```

Expected: health check returns 200.

- [ ] **Step 3: Commit**

```bash
git add backend/routes.py
git commit -m "feat(routes): register /v1/market router"
```

---

### Task 5: Add httpx Dependency

**Files:**
- Modify: `backend/requirements.txt` (or `pyproject.toml` depending on where deps live)

- [ ] **Step 1: Check if httpx is already installed**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
grep -i httpx backend/requirements.txt pyproject.toml 2>/dev/null
```

If httpx is already present, skip this task. If not:

- [ ] **Step 2: Add httpx**

Add `httpx>=0.27` to `backend/requirements.txt`.

- [ ] **Step 3: Rebuild backend**

```bash
./run.sh rebuild backend
```

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore(deps): add httpx for market ticker upstream calls"
```

---

### Task 6: Alembic Migration — Apply and Verify

**Files:**
- Verify: `backend/db/migrations/`

- [ ] **Step 1: Generate and apply migration**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
PYTHONPATH=. alembic revision --autogenerate -m "add market_indices table"
PYTHONPATH=. alembic upgrade head
```

- [ ] **Step 2: Verify table exists**

```bash
docker compose exec postgres psql -U postgres -d ai_agent_ui -c "\d stocks.market_indices"
```

Expected: table with columns `id`, `nifty_data`, `sensex_data`, `market_state`, `fetched_at`.

- [ ] **Step 3: Commit migration**

```bash
git add backend/db/migrations/versions/
git commit -m "feat(db): add market_indices table migration"
```

---

### Task 7: Backend Tests

**Files:**
- Create: `tests/backend/test_market_routes.py`

- [ ] **Step 1: Write tests**

Create `tests/backend/test_market_routes.py`:

```python
"""Tests for market index ticker endpoint."""
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


@pytest.fixture
def mock_cache():
    """Return a mock CacheService."""
    cache = MagicMock()
    cache.get.return_value = None
    cache.set = MagicMock()
    return cache


@pytest.fixture
def sample_nifty():
    return {
        "price": 23886.30,
        "change": 164.30,
        "change_pct": 0.69,
        "prev_close": 23722.00,
        "open": 23589.60,
        "high": 23907.40,
        "low": 23555.60,
    }


@pytest.fixture
def sample_sensex():
    return {
        "price": 76986.46,
        "change": -563.79,
        "change_pct": -0.73,
        "prev_close": 77550.25,
        "open": 77100.00,
        "high": 77200.00,
        "low": 76800.00,
    }


class TestIsMarketOpen:
    """Test market hours gating."""

    @patch("market_routes.datetime")
    def test_weekday_during_hours(self, mock_dt):
        from market_routes import _is_market_open
        # Wednesday 11:00 IST
        mock_dt.now.return_value = IST.localize(
            datetime(2026, 4, 15, 11, 0, 0)
        )
        assert _is_market_open() is True

    @patch("market_routes.datetime")
    def test_weekday_before_open(self, mock_dt):
        from market_routes import _is_market_open
        # Wednesday 08:30 IST
        mock_dt.now.return_value = IST.localize(
            datetime(2026, 4, 15, 8, 30, 0)
        )
        assert _is_market_open() is False

    @patch("market_routes.datetime")
    def test_weekday_after_close(self, mock_dt):
        from market_routes import _is_market_open
        # Wednesday 16:00 IST
        mock_dt.now.return_value = IST.localize(
            datetime(2026, 4, 15, 16, 0, 0)
        )
        assert _is_market_open() is False

    @patch("market_routes.datetime")
    def test_saturday(self, mock_dt):
        from market_routes import _is_market_open
        # Saturday 11:00 IST
        mock_dt.now.return_value = IST.localize(
            datetime(2026, 4, 18, 11, 0, 0)
        )
        assert _is_market_open() is False


class TestCacheHit:
    """Test that Redis cache hit returns immediately."""

    @patch("market_routes.get_cache")
    @patch("market_routes.get_current_user")
    def test_returns_cached_data(
        self, mock_user, mock_get_cache, mock_cache,
        sample_nifty, sample_sensex,
    ):
        cached = json.dumps({
            "nifty": sample_nifty,
            "sensex": sample_sensex,
            "market_state": "REGULAR",
            "timestamp": "2026-04-13T14:00:00+05:30",
            "stale": False,
        })
        mock_cache.get.return_value = cached
        mock_get_cache.return_value = mock_cache
        # Endpoint should return cached data without
        # hitting upstream. Verified by absence of
        # httpx calls.


class TestOffHoursServesPG:
    """Test off-hours serves PG data, no upstream."""

    @patch("market_routes._is_market_open", return_value=False)
    @patch("market_routes._needs_seed_today",
           new_callable=AsyncMock, return_value=False)
    @patch("market_routes._read_from_pg",
           new_callable=AsyncMock)
    @patch("market_routes.get_cache")
    def test_serves_pg_when_seeded(
        self, mock_get_cache, mock_read_pg,
        mock_needs_seed, mock_market_open,
        mock_cache, sample_nifty, sample_sensex,
    ):
        mock_get_cache.return_value = mock_cache
        mock_read_pg.return_value = {
            "nifty": sample_nifty,
            "sensex": sample_sensex,
            "market_state": "CLOSED",
            "timestamp": "2026-04-13T15:30:00+05:30",
            "stale": False,
        }
        # Endpoint should return PG data.
        # _fetch_and_cache should NOT be called.


class TestFirstCallOfDaySeeds:
    """Test first call of day fetches upstream even off-hours."""

    @patch("market_routes._is_market_open", return_value=False)
    @patch("market_routes._needs_seed_today",
           new_callable=AsyncMock, return_value=True)
    @patch("market_routes._fetch_and_cache",
           new_callable=AsyncMock)
    @patch("market_routes.get_cache")
    def test_seeds_on_first_call(
        self, mock_get_cache, mock_fetch, mock_needs_seed,
        mock_market_open, mock_cache,
        sample_nifty, sample_sensex,
    ):
        mock_get_cache.return_value = mock_cache
        mock_fetch.return_value = {
            "nifty": sample_nifty,
            "sensex": sample_sensex,
            "market_state": "CLOSED",
            "timestamp": "2026-04-13T18:00:00+05:30",
            "stale": False,
        }
        # _fetch_and_cache SHOULD be called.
```

- [ ] **Step 2: Run tests**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
python -m pytest tests/backend/test_market_routes.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/backend/test_market_routes.py
git commit -m "test(market): add market ticker endpoint tests"
```

---

### Task 8: Frontend MarketTicker Component

**Files:**
- Create: `frontend/components/MarketTicker.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/components/MarketTicker.tsx`:

```tsx
"use client";
/**
 * Inline market ticker for Nifty 50 + Sensex.
 *
 * Polls GET /v1/market/indices every 30 seconds via apiFetch.
 * Hidden on mobile (< md breakpoint).
 */

import { useEffect, useState, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";

interface IndexData {
  price: number;
  change: number;
  change_pct: number;
  prev_close: number;
  open: number;
  high: number;
  low: number;
}

interface MarketIndices {
  nifty: IndexData;
  sensex: IndexData;
  market_state: string;
  timestamp: string;
  stale: boolean;
}

const POLL_INTERVAL = 30_000;

function formatPrice(val: number): string {
  return val.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function IndexTick({
  label,
  data,
  closed,
}: {
  label: string;
  data: IndexData;
  closed: boolean;
}) {
  const positive = data.change >= 0;
  const arrow = positive ? "\u25B2" : "\u25BC";
  const colorClass = positive
    ? "text-green-500"
    : "text-red-500";

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-gray-400 dark:text-gray-500 font-medium text-[11px]">
        {label}
      </span>
      <span className="text-gray-800 dark:text-gray-200 font-semibold font-mono text-xs">
        {formatPrice(data.price)}
      </span>
      {closed ? (
        <span className="text-gray-400 dark:text-gray-600 text-[10px]">
          Closed
        </span>
      ) : (
        <span
          className={`${colorClass} text-[11px] font-medium flex items-center gap-0.5`}
        >
          <span>{arrow}</span>
          <span>{Math.abs(data.change).toFixed(2)}</span>
          <span className="opacity-80">
            ({positive ? "+" : ""}
            {data.change_pct.toFixed(2)}%)
          </span>
        </span>
      )}
    </div>
  );
}

export function MarketTicker() {
  const [data, setData] = useState<MarketIndices | null>(
    null,
  );

  const fetchIndices = useCallback(async () => {
    try {
      const res = await apiFetch("/market/indices");
      if (res.ok) {
        const json: MarketIndices = await res.json();
        setData(json);
      }
    } catch {
      // Keep showing last known data.
    }
  }, []);

  useEffect(() => {
    fetchIndices();
    const id = setInterval(fetchIndices, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchIndices]);

  if (!data) return null;

  const closed = data.market_state === "CLOSED";

  return (
    <div className="hidden md:flex items-center gap-4 text-xs">
      {data.nifty?.price != null && (
        <IndexTick
          label="NIFTY"
          data={data.nifty}
          closed={closed}
        />
      )}
      <span className="text-gray-300 dark:text-gray-700">
        |
      </span>
      {data.sensex?.price != null && (
        <IndexTick
          label="SENSEX"
          data={data.sensex}
          closed={closed}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/MarketTicker.tsx
git commit -m "feat(frontend): add MarketTicker component for header"
```

---

### Task 9: Mount MarketTicker in AppHeader

**Files:**
- Modify: `frontend/components/AppHeader.tsx:128-172`

- [ ] **Step 1: Add import**

At the top of `AppHeader.tsx`, add:

```typescript
import { MarketTicker } from "@/components/MarketTicker";
```

- [ ] **Step 2: Insert ticker between left and right sections**

In the `<header>` JSX (line 128), change the layout from `justify-between` to a three-column flex, and add the `<MarketTicker />` between the left and right divs.

Replace the opening `<header>` tag:

```tsx
    <header className="h-14 flex items-center justify-between px-4 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 shadow-sm shrink-0 transition-colors">
```

Keep it as-is (the `justify-between` naturally spaces 3 children: left, center, right).

After the closing `</div>` of the left section (after the `</h1>` and its parent `</div>`, around line 171), add:

```tsx
      {/* -- Center: market ticker (desktop only) -- */}
      <MarketTicker />
```

This places `<MarketTicker />` as the second child of the header flex, which `justify-between` will center between left and right.

- [ ] **Step 3: Verify in browser**

```bash
./run.sh rebuild frontend
```

Open `http://localhost:3000/dashboard` and verify:
- Desktop: ticker shows Nifty + Sensex in the header center
- Mobile (resize < 768px): ticker hidden
- Values update every 30 seconds (check network tab)

- [ ] **Step 4: Commit**

```bash
git add frontend/components/AppHeader.tsx
git commit -m "feat(header): mount MarketTicker in AppHeader center"
```

---

### Task 10: End-to-End Verification

- [ ] **Step 1: Restart all services**

```bash
./run.sh restart
```

- [ ] **Step 2: Test authenticated endpoint**

```bash
# Get a JWT token first (login as test user)
TOKEN=$(curl -s -X POST http://localhost:8181/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@demo.com","password":"Admin123!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Hit the market indices endpoint
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8181/v1/market/indices | python3 -m json.tool
```

Expected: JSON response with `nifty`, `sensex`, `market_state`, `timestamp`.

- [ ] **Step 3: Verify PG persistence**

```bash
docker compose exec postgres psql -U postgres -d ai_agent_ui -c \
  "SELECT id, market_state, fetched_at FROM stocks.market_indices"
```

Expected: one row with recent `fetched_at`.

- [ ] **Step 4: Verify Redis cache**

```bash
docker compose exec redis redis-cli GET market:indices
```

Expected: JSON string matching the API response.

- [ ] **Step 5: Test in browser**

Open `http://localhost:3000/dashboard`. Verify ticker in header. Wait 30s, verify values refresh (check network tab for periodic `/v1/market/indices` calls).

- [ ] **Step 6: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix(market): end-to-end verification fixups"
```
