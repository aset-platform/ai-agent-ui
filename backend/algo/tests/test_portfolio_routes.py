"""Tests for /v1/algo/portfolio/positions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.algo.routes.portfolio import (
    AlgoPositionRow,
    AlgoPositionsResponse,
    _days_held,
    _get_algo_positions_impl,
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


def _kite_position_row(
    symbol: str, qty: int, avg: float, ltp: float,
) -> dict:
    return {
        "tradingsymbol": symbol,
        "quantity": qty,
        "average_price": avg,
        "last_price": ltp,
        "pnl": qty * (ltp - avg),
        "product": "MIS",
    }


def _kite_holding_row(
    symbol: str, qty: int, t1_qty: int, avg: float, ltp: float,
) -> dict:
    return {
        "tradingsymbol": symbol,
        "quantity": qty,
        "t1_quantity": t1_qty,
        "average_price": avg,
        "last_price": ltp,
        "product": "CNC",
    }


def _attr(
    sid: str, name: str, entry_ts_utc: str | None = None,
) -> dict:
    return {
        "strategy_id": sid,
        "strategy_name": name,
        "entry_ts_utc": entry_ts_utc,
        "entry_reason": None,
    }


def _fake_kite(positions_net, holdings):
    """Build a MagicMock KiteClient with ._kc.positions /
    ._kc.holdings that the impl will call via to_thread."""
    kc_inner = MagicMock()
    kc_inner.positions = MagicMock(
        return_value={"net": positions_net},
    )
    kc_inner.holdings = MagicMock(return_value=holdings)
    outer = MagicMock()
    outer._kc = kc_inner
    return outer


@pytest.mark.asyncio
async def test_returns_empty_when_no_kite_positions(
    monkeypatch,
):
    fake_kite = _fake_kite([], [])
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: False,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    assert out.positions == []


@pytest.mark.asyncio
async def test_filters_out_unattributed_positions(
    monkeypatch,
):
    net = [_kite_position_row("INFY", 10, 1500, 1520)]
    fake_kite = _fake_kite(net, [])
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: False,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    assert out.positions == []


@pytest.mark.asyncio
async def test_merges_mis_and_cnc_into_one_response(
    monkeypatch,
):
    net = [_kite_position_row("INFY", 10, 1500, 1520)]
    hold = [_kite_holding_row("TCS", 5, 0, 3400, 3450)]
    fake_kite = _fake_kite(net, hold)
    sid_mis = str(uuid4())
    sid_cnc = str(uuid4())
    attr = {
        "INFY.NS": _attr(sid_mis, "RSI(2) v3"),
        "TCS.NS": _attr(sid_cnc, "Bollinger"),
    }
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(return_value=attr),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: True,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    syms = {(r.tradingsymbol, r.product) for r in out.positions}
    assert syms == {("INFY", "MIS"), ("TCS", "CNC")}
    assert out.market_open is True


@pytest.mark.asyncio
async def test_t1_pending_flagged_on_cnc_settling(
    monkeypatch,
):
    """holdings row with quantity=0 + t1_quantity=10 →
    t1_pending=True, qty=10."""
    hold = [_kite_holding_row("HDFC", 0, 10, 1700, 1720)]
    fake_kite = _fake_kite([], hold)
    sid = str(uuid4())
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(
            return_value={
                "HDFC.NS": _attr(sid, "Bollinger"),
            },
        ),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: False,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    assert len(out.positions) == 1
    assert out.positions[0].t1_pending is True
    assert out.positions[0].quantity == 10


@pytest.mark.asyncio
async def test_sorted_by_pnl_inr_desc(monkeypatch):
    net = [
        _kite_position_row("A", 10, 100, 110),  # +100 pnl
        _kite_position_row("B", 10, 100, 90),   # -100 pnl
    ]
    fake_kite = _fake_kite(net, [])
    sid_a = str(uuid4())
    sid_b = str(uuid4())
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(
            return_value={
                "A.NS": _attr(sid_a, "S1"),
                "B.NS": _attr(sid_b, "S2"),
            },
        ),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: False,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    syms = [r.tradingsymbol for r in out.positions]
    assert syms == ["A", "B"]


@pytest.mark.asyncio
async def test_cache_hit_short_circuits(monkeypatch):
    """A populated Redis cache entry skips the Kite calls."""
    cached = AlgoPositionsResponse(
        positions=[],
        as_of=datetime.now(timezone.utc),
        market_open=False,
    )
    fake_cache = MagicMock()
    fake_cache.get = MagicMock(
        return_value=cached.model_dump_json(),
    )
    fake_cache.set = MagicMock()
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: fake_cache,
    )
    fake_kite_builder = AsyncMock(
        side_effect=AssertionError("should not be called"),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        fake_kite_builder,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    assert out.positions == []
    fake_kite_builder.assert_not_called()
