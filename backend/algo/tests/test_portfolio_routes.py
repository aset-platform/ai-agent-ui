"""Tests for /v1/algo/portfolio/positions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.algo.routes.portfolio import (
    AlgoPositionRow,
    AlgoPositionsResponse,
    _days_held,
)


_IST = ZoneInfo("Asia/Kolkata")


def test_algo_position_row_minimal_valid():
    row = AlgoPositionRow(
        tradingsymbol="INFY",
        internal_ticker="INFY.NS",
        product="MIS",
        quantity=50,
        avg_price=Decimal("1500.00"),
        last_price=Decimal("1572.50"),
        pnl_inr=Decimal("3625.00"),
        pnl_pct=Decimal("4.83"),
        strategy_id=uuid4(),
        strategy_name="RSI(2) v3",
        entry_ts=datetime.now(timezone.utc),
        days_held=0,
    )
    assert row.t1_pending is False
    assert row.product == "MIS"


def test_days_held_returns_zero_for_today_ist():
    # An entry_ts in today's IST date (e.g. now) → 0 days held.
    today_ist_10am_as_utc = (
        datetime.now(_IST)
        .replace(hour=10, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )
    assert _days_held(today_ist_10am_as_utc) == 0


def test_days_held_returns_three_for_three_ist_days_ago():
    # 3 IST calendar days ago (any time of day) → 3.
    ts = (
        (datetime.now(_IST) - timedelta(days=3))
        .replace(hour=10, minute=0)
        .astimezone(timezone.utc)
    )
    assert _days_held(ts) == 3


def test_days_held_zero_when_entry_ts_none():
    assert _days_held(None) == 0


def test_response_roundtrip():
    row = AlgoPositionRow(
        tradingsymbol="TCS",
        internal_ticker="TCS.NS",
        product="CNC",
        quantity=20,
        avg_price=Decimal("3450.50"),
        last_price=Decimal("3401.20"),
        pnl_inr=Decimal("-986.00"),
        pnl_pct=Decimal("-1.43"),
        strategy_id=uuid4(),
        strategy_name="Mean Rev MIS",
        entry_ts=None,
        days_held=0,
    )
    resp = AlgoPositionsResponse(
        positions=[row],
        as_of=datetime.now(timezone.utc),
        market_open=False,
    )
    assert len(resp.positions) == 1
    assert resp.market_open is False
