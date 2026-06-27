"""Tests for date/Decimal parsing helpers — Task 3.2 gap closure.

Covers:
1. ``caps_repo._parse_iso_date`` — valid Z suffix, tz-aware, None,
   garbage input.
2. ``CapsRepo.get_filled_buys_from_previous_runs`` — fill_price is
   ``Decimal``, fill_date parsed from filled_at / submitted_at
   fallback / neither.
3. ``runtime._parse_fill_date`` — malformed input → None, valid
   filled_at, filled_at=None + valid submitted_at fallback.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.caps_repo import CapsRepo, _parse_iso_date
from backend.algo.live.runtime import _parse_fill_date


# ── 1. _parse_iso_date (caps_repo) ───────────────────────────────────


class TestParseIsoDate:
    def test_z_suffix_returns_correct_date(self):
        raw = "2024-03-15T10:30:00Z"
        result = _parse_iso_date(raw)
        assert result == date(2024, 3, 15)

    def test_tz_aware_iso_normalises_to_utc_date(self):
        # +05:30 IST; 05:00 IST = 23:30 UTC on 2024-03-14
        raw = "2024-03-15T05:00:00+05:30"
        result = _parse_iso_date(raw)
        assert result == date(2024, 3, 14)

    def test_tz_aware_iso_utc_plus_zero(self):
        raw = "2024-06-10T08:15:00+00:00"
        result = _parse_iso_date(raw)
        assert result == date(2024, 6, 10)

    def test_none_returns_none(self):
        assert _parse_iso_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_iso_date("") is None

    def test_garbage_string_returns_none_no_crash(self):
        assert _parse_iso_date("not-a-date") is None

    def test_non_string_type_returns_none(self):
        assert _parse_iso_date(12345) is None
        assert _parse_iso_date(3.14) is None


# ── 2. get_filled_buys_from_previous_runs (caps_repo) ────────────────


_UID = uuid4()
_SID = uuid4()
_CID = uuid4()


def _mock_session(rows):
    """Return a context-manager that yields a session executing rows."""
    execute_result = MagicMock()
    execute_result.all.return_value = rows

    inner_session = MagicMock()
    inner_session.execute = AsyncMock(return_value=execute_result)

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=inner_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return session_cm


def _make_rows(*run_entries: list[dict]):
    """One DB row per positional argument (each is a list of
    in-flight dicts). row[0] holds the JSON-encoded payload."""
    rows = []
    for entries in run_entries:
        payload = json.dumps(entries)

        def _getitem(self, i, _p=payload):
            return _p if i == 0 else None

        row = MagicMock()
        row.__getitem__ = _getitem
        rows.append(row)
    return rows


def _run_get_filled(rows):
    import asyncio

    repo = CapsRepo()
    session_cm = _mock_session(rows)
    with patch(
        "backend.algo.live.caps_repo.disposable_pg_session",
        return_value=session_cm,
    ):
        return asyncio.run(
            repo.get_filled_buys_from_previous_runs(_UID, _SID, _CID)
        )


class TestGetFilledBuysFromPreviousRuns:
    def test_fill_price_is_decimal(self):
        rows = _make_rows(
            [
                {
                    "status": "filled",
                    "side": "BUY",
                    "symbol": "INFY",
                    "fill_price": 1500.75,
                    "filled_qty": 2,
                    "filled_at": "2024-05-01T10:00:00Z",
                }
            ]
        )
        result = _run_get_filled(rows)
        assert "INFY.NS" in result
        assert isinstance(result["INFY.NS"]["fill_price"], Decimal)
        assert result["INFY.NS"]["fill_price"] == Decimal("1500.75")

    def test_fill_date_from_filled_at(self):
        rows = _make_rows(
            [
                {
                    "status": "filled",
                    "side": "BUY",
                    "symbol": "RELIANCE",
                    "fill_price": 2800.0,
                    "filled_qty": 1,
                    "filled_at": "2024-04-10T06:00:00Z",
                }
            ]
        )
        result = _run_get_filled(rows)
        assert result["RELIANCE.NS"]["fill_date"] == date(2024, 4, 10)

    def test_fill_date_fallback_to_submitted_at(self):
        rows = _make_rows(
            [
                {
                    "status": "filled",
                    "side": "BUY",
                    "symbol": "TCS",
                    "fill_price": 3500.0,
                    "filled_qty": 1,
                    "submitted_at": "2024-03-20T05:30:00+00:00",
                }
            ]
        )
        result = _run_get_filled(rows)
        assert result["TCS.NS"]["fill_date"] == date(2024, 3, 20)

    def test_fill_date_none_when_no_timestamps(self):
        rows = _make_rows(
            [
                {
                    "status": "filled",
                    "side": "BUY",
                    "symbol": "WIPRO",
                    "fill_price": 450.0,
                    "filled_qty": 3,
                }
            ]
        )
        result = _run_get_filled(rows)
        assert result["WIPRO.NS"]["fill_date"] is None

    def test_skips_non_filled_and_non_buy(self):
        rows = _make_rows(
            [
                {
                    "status": "submitted",
                    "side": "BUY",
                    "symbol": "HDFC",
                    "fill_price": 1000.0,
                    "filled_qty": 1,
                },
                {
                    "status": "filled",
                    "side": "SELL",
                    "symbol": "HDFC",
                    "fill_price": 1100.0,
                    "filled_qty": 1,
                },
            ]
        )
        result = _run_get_filled(rows)
        assert result == {}

    def test_most_recent_run_wins_for_duplicate_ticker(self):
        rows = _make_rows(
            # Newer run first (ORDER BY started_at DESC)
            [
                {
                    "status": "filled",
                    "side": "BUY",
                    "symbol": "INFY",
                    "fill_price": 1600.0,
                    "filled_qty": 2,
                    "filled_at": "2024-06-01T09:00:00Z",
                }
            ],
            # Older run
            [
                {
                    "status": "filled",
                    "side": "BUY",
                    "symbol": "INFY",
                    "fill_price": 1400.0,
                    "filled_qty": 3,
                    "filled_at": "2024-05-01T09:00:00Z",
                }
            ],
        )
        result = _run_get_filled(rows)
        assert result["INFY.NS"]["fill_price"] == Decimal("1600.0")


# ── 3. _parse_fill_date (runtime) ────────────────────────────────────


class TestParseFillDate:
    def test_malformed_string_returns_none(self):
        result = _parse_fill_date("not-a-date", None)
        assert result is None

    def test_none_only_returns_none(self):
        result = _parse_fill_date(None, None)
        assert result is None

    def test_valid_filled_at_z_suffix(self):
        result = _parse_fill_date("2024-07-04T12:00:00Z", None)
        assert result == date(2024, 7, 4)

    def test_filled_at_none_falls_back_to_submitted_at(self):
        result = _parse_fill_date(None, "2024-08-15T08:30:00+00:00")
        assert result == date(2024, 8, 15)

    def test_first_valid_wins_over_second(self):
        result = _parse_fill_date(
            "2024-07-04T12:00:00Z",
            "2024-08-15T08:30:00+00:00",
        )
        assert result == date(2024, 7, 4)

    def test_skips_malformed_tries_next(self):
        result = _parse_fill_date("bad", "2024-09-01T00:00:00Z")
        assert result == date(2024, 9, 1)

    def test_date_object_returned_directly(self):
        d = date(2024, 1, 1)
        result = _parse_fill_date(d)
        assert result == d

    def test_datetime_object_uses_date(self):
        dt = datetime(2024, 3, 10, 15, 30, tzinfo=timezone.utc)
        result = _parse_fill_date(dt)
        assert result == date(2024, 3, 10)
