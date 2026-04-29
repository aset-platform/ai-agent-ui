"""Tests for the cohort-bucketed performance endpoint.

Pure-Python coverage for the helpers that don't require
a live database:
- ``_bucket_label`` formatting for week / month / quarter
- ``get_recommendation_performance_buckets`` input
  validation (granularity guard, months_back clamp,
  scope coercion).

The full SQL aggregation path is exercised manually
against a real PostgreSQL during development (the helper
relies on ``date_trunc`` + ``AT TIME ZONE`` + ``CAST AS
VARCHAR`` which SQLite doesn't support). Covered by the
end-to-end smoke verified in commit body.
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.db.pg_stocks import (
    _bucket_label,
    get_recommendation_performance_buckets,
)


# ── _bucket_label ──────────────────────────────────────


class TestBucketLabel:
    def test_week_returns_iso_year_week(self):
        # 2026-04-13 is a Monday in ISO week 16
        assert (
            _bucket_label(date(2026, 4, 13), "week")
            == "2026-W16"
        )

    def test_week_pads_single_digit(self):
        # 2026-01-05 is in ISO week 02
        assert (
            _bucket_label(date(2026, 1, 5), "week")
            == "2026-W02"
        )

    def test_month_returns_short_month_year(self):
        assert (
            _bucket_label(date(2026, 4, 1), "month")
            == "Apr 2026"
        )
        assert (
            _bucket_label(date(2025, 12, 1), "month")
            == "Dec 2025"
        )

    def test_quarter_maps_month_to_quarter(self):
        # Q1: Jan-Mar
        assert (
            _bucket_label(date(2026, 1, 1), "quarter")
            == "Q1 2026"
        )
        # Q2: Apr-Jun
        assert (
            _bucket_label(date(2026, 4, 1), "quarter")
            == "Q2 2026"
        )
        # Q3: Jul-Sep
        assert (
            _bucket_label(date(2026, 7, 1), "quarter")
            == "Q3 2026"
        )
        # Q4: Oct-Dec
        assert (
            _bucket_label(date(2026, 10, 1), "quarter")
            == "Q4 2026"
        )

    def test_unknown_granularity_falls_back_to_iso(self):
        assert (
            _bucket_label(date(2026, 4, 1), "decade")
            == "2026-04-01"
        )


# ── helper input validation ────────────────────────────


class _FakeSession:
    """Stub so we can exercise input validation without
    actually executing SQL."""

    def __init__(self):
        self.executed = []

    async def execute(self, *args, **kwargs):
        # Should never be called when validation rejects.
        raise AssertionError(
            "execute() should not be called for "
            "validation failures"
        )


@pytest.mark.asyncio
async def test_invalid_granularity_raises_value_error():
    session = _FakeSession()
    with pytest.raises(ValueError, match="granularity"):
        await get_recommendation_performance_buckets(
            session,
            "00000000-0000-0000-0000-000000000000",
            granularity="hourly",
        )


@pytest.mark.asyncio
async def test_months_back_clamped_to_14_max(monkeypatch):
    """months_back > 14 must be clamped, not raise."""
    seen = {}

    class _CapSession:
        async def execute(self, _stmt, params=None):
            seen.update(params or {})

            class _R:
                def mappings(self):
                    class _M:
                        def all(self):
                            return []
                    return _M()
            return _R()

    await get_recommendation_performance_buckets(
        _CapSession(),
        "00000000-0000-0000-0000-000000000000",
        granularity="month",
        months_back=99,
    )
    assert seen.get("months_back") == 14


@pytest.mark.asyncio
async def test_months_back_clamped_to_1_min():
    seen = {}

    class _CapSession:
        async def execute(self, _stmt, params=None):
            seen.update(params or {})

            class _R:
                def mappings(self):
                    class _M:
                        def all(self):
                            return []
                    return _M()
            return _R()

    await get_recommendation_performance_buckets(
        _CapSession(),
        "00000000-0000-0000-0000-000000000000",
        granularity="month",
        months_back=0,
    )
    assert seen.get("months_back") == 1


@pytest.mark.asyncio
async def test_unknown_scope_coerced_to_none():
    seen = {}

    class _CapSession:
        async def execute(self, _stmt, params=None):
            seen.update(params or {})

            class _R:
                def mappings(self):
                    class _M:
                        def all(self):
                            return []
                    return _M()
            return _R()

    await get_recommendation_performance_buckets(
        _CapSession(),
        "00000000-0000-0000-0000-000000000000",
        granularity="month",
        scope="europe",  # invalid -> None
    )
    assert seen.get("scope") is None


@pytest.mark.asyncio
async def test_valid_scope_passes_through():
    seen = {}

    class _CapSession:
        async def execute(self, _stmt, params=None):
            seen.update(params or {})

            class _R:
                def mappings(self):
                    class _M:
                        def all(self):
                            return []
                    return _M()
            return _R()

    await get_recommendation_performance_buckets(
        _CapSession(),
        "00000000-0000-0000-0000-000000000000",
        granularity="month",
        scope="india",
    )
    assert seen.get("scope") == "india"


@pytest.mark.asyncio
async def test_empty_result_returns_zeroed_summary():
    class _EmptySession:
        async def execute(self, _stmt, params=None):
            class _R:
                def mappings(self):
                    class _M:
                        def all(self):
                            return []
                    return _M()
            return _R()

    out = await get_recommendation_performance_buckets(
        _EmptySession(),
        "00000000-0000-0000-0000-000000000000",
        granularity="month",
    )
    assert out["buckets"] == []
    assert out["summary"]["total_recs"] == 0
    assert out["summary"]["acted_on_count"] == 0
    assert out["summary"]["pending_count"] == 0
    assert out["summary"]["hit_rate_30d"] is None
