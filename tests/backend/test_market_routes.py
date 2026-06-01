"""Tests for market_routes: _is_market_open, cache, PG fallback.

Covers:
- _is_market_open() — weekday/weekend, before/after hours
- Cache hit short-circuits the handler
- Off-hours with seeded PG data serves from PG, no upstream
- First-call-of-day seeds upstream even if off-hours
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytz
import pytest

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _ist(year, month, day, hour, minute, weekday_override=None):
    """Return an IST-aware datetime for the given wall-clock time."""
    naive = datetime(year, month, day, hour, minute, 0)
    return IST.localize(naive)


def _make_cache(hit_value=None):
    """Minimal fake cache object."""
    cache = MagicMock()
    cache.get.return_value = hit_value
    cache.set.return_value = None
    return cache


# ---------------------------------------------------------------
# _is_market_open tests
# ---------------------------------------------------------------

class TestIsMarketOpen:
    """Unit tests for the pure _is_market_open() helper."""

    def _call(self, fake_now: datetime) -> bool:
        from market_routes import _is_market_open

        with patch("market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            return _is_market_open()

    def test_weekday_during_hours_is_open(self):
        """Monday 10:00 IST should be open."""
        # 2026-04-13 is a Monday
        now = _ist(2026, 4, 13, 10, 0)
        assert self._call(now) is True

    def test_weekday_at_open_boundary_is_open(self):
        """09:00 exactly is open."""
        now = _ist(2026, 4, 13, 9, 0)
        assert self._call(now) is True

    def test_weekday_at_close_boundary_is_open(self):
        """15:30 exactly is open (inclusive)."""
        now = _ist(2026, 4, 13, 15, 30)
        assert self._call(now) is True

    def test_weekday_before_open_is_closed(self):
        """08:59 IST should be closed."""
        now = _ist(2026, 4, 13, 8, 59)
        assert self._call(now) is False

    def test_weekday_after_close_is_closed(self):
        """15:31 IST should be closed."""
        now = _ist(2026, 4, 13, 15, 31)
        assert self._call(now) is False

    def test_saturday_is_closed(self):
        """Saturday 11:00 IST should be closed."""
        # 2026-04-11 is a Saturday
        now = _ist(2026, 4, 11, 11, 0)
        assert self._call(now) is False

    def test_sunday_is_closed(self):
        """Sunday 11:00 IST should be closed."""
        # 2026-04-12 is a Sunday
        now = _ist(2026, 4, 12, 11, 0)
        assert self._call(now) is False


# ---------------------------------------------------------------
# get_indices handler tests (via direct call, mocked deps)
# ---------------------------------------------------------------

_SAMPLE_PAYLOAD = {
    "nifty": {"price": 22000.0, "change": 100.0,
               "change_pct": 0.45, "prev_close": 21900.0,
               "open": 21950.0, "high": 22050.0, "low": 21880.0},
    "sensex": {"price": 72000.0, "change": 200.0,
                "change_pct": 0.28, "prev_close": 71800.0,
                "open": 71900.0, "high": 72100.0, "low": 71750.0},
    "market_state": "CLOSED",
    "timestamp": "2026-04-12T15:00:00+05:30",
    "stale": False,
}


class TestGetIndicesHandler:
    """Behaviour tests for the ``/market/indices`` endpoint logic."""

    # ------------------------------------------------------------------
    # Test 1: Cache hit returns immediately without upstream calls
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cache_hit_returns_immediately(self):
        """When Redis has a cached value, no upstream is called."""
        cached_json = json.dumps(_SAMPLE_PAYLOAD)
        fake_cache = _make_cache(hit_value=cached_json)

        with (
            patch("market_routes.get_cache", return_value=fake_cache),
            patch(
                "market_routes._fetch_and_cache",
                new_callable=AsyncMock,
            ) as mock_fetch,
        ):
            from market_routes import create_market_router
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from auth.dependencies import get_current_user
            from auth.models import UserContext

            app = FastAPI()
            app.include_router(create_market_router())

            _user = UserContext(
                user_id="u1", email="t@t.com", role="user",
            )
            app.dependency_overrides[get_current_user] = (
                lambda: _user
            )

            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get("/market/indices")

            assert resp.status_code == 200
            assert resp.json()["nifty"]["price"] == 22000.0
            mock_fetch.assert_not_called()

    # ------------------------------------------------------------------
    # Test 2: Off-hours + seeded PG data → serve from PG, no upstream
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_off_hours_seeded_serves_pg_no_upstream(self):
        """Off-hours, PG has today's row → returns PG data, skip fetch."""
        fake_cache = _make_cache(hit_value=None)  # cache miss

        with (
            patch("market_routes.get_cache", return_value=fake_cache),
            patch(
                "market_routes._is_market_open",
                return_value=False,
            ),
            patch(
                "market_routes._needs_seed_today",
                new_callable=AsyncMock,
                return_value=False,  # already seeded today
            ),
            patch(
                "market_routes._read_from_pg",
                new_callable=AsyncMock,
                return_value=_SAMPLE_PAYLOAD,
            ) as mock_pg,
            patch(
                "market_routes._fetch_and_cache",
                new_callable=AsyncMock,
            ) as mock_fetch,
        ):
            from importlib import reload
            import market_routes as mr
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from auth.dependencies import get_current_user
            from auth.models import UserContext

            app = FastAPI()
            app.include_router(mr.create_market_router())

            _user = UserContext(
                user_id="u1", email="t@t.com", role="user",
            )
            app.dependency_overrides[get_current_user] = (
                lambda: _user
            )

            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get("/market/indices")

            assert resp.status_code == 200
            body = resp.json()
            assert body["nifty"]["price"] == 22000.0
            # upstream fetch must NOT have been called
            mock_fetch.assert_not_called()
            # PG read must have been called
            mock_pg.assert_called_once()

    # ------------------------------------------------------------------
    # Test 3: First call of day off-hours → seeds upstream anyway
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_first_call_of_day_seeds_upstream_off_hours(self):
        """Off-hours but no PG row yet → _fetch_and_cache is called."""
        fake_cache = _make_cache(hit_value=None)

        with (
            patch("market_routes.get_cache", return_value=fake_cache),
            patch(
                "market_routes._is_market_open",
                return_value=False,
            ),
            patch(
                "market_routes._needs_seed_today",
                new_callable=AsyncMock,
                return_value=True,  # no row today
            ),
            patch(
                "market_routes._fetch_and_cache",
                new_callable=AsyncMock,
                return_value=_SAMPLE_PAYLOAD,
            ) as mock_fetch,
        ):
            import market_routes as mr
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from auth.dependencies import get_current_user
            from auth.models import UserContext

            app = FastAPI()
            app.include_router(mr.create_market_router())

            _user = UserContext(
                user_id="u1", email="t@t.com", role="user",
            )
            app.dependency_overrides[get_current_user] = (
                lambda: _user
            )

            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get("/market/indices")

            assert resp.status_code == 200
            body = resp.json()
            assert body["nifty"]["price"] == 22000.0
            # upstream must have been seeded
            mock_fetch.assert_called_once()

    # ------------------------------------------------------------------
    # Test 4: All upstreams fail + no PG row → 503
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_all_upstreams_fail_returns_503(self):
        """When fetch fails and PG is empty, respond 503."""
        fake_cache = _make_cache(hit_value=None)

        with (
            patch("market_routes.get_cache", return_value=fake_cache),
            patch(
                "market_routes._is_market_open",
                return_value=True,
            ),
            patch(
                "market_routes._fetch_and_cache",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "market_routes._read_from_pg",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            import market_routes as mr
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from auth.dependencies import get_current_user
            from auth.models import UserContext

            app = FastAPI()
            app.include_router(mr.create_market_router())

            _user = UserContext(
                user_id="u1", email="t@t.com", role="user",
            )
            app.dependency_overrides[get_current_user] = (
                lambda: _user
            )

            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get("/market/indices")

            assert resp.status_code == 503
            assert "unavailable" in resp.json()["detail"].lower()
