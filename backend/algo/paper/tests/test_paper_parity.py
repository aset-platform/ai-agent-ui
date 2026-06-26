"""Task 6.1: mark-to-market equity + unrealised P&L parity test.

Paper runtime must produce the same equity/sizing behaviour as the
live runtime:

1. ``_account_snapshot`` includes open-position market value in
   ``current_equity_inr`` and populates ``daily_unrealised_pnl_inr``
   using ``_last_marks``.

2. A ticker with no mark in ``_last_marks`` contributes 0 to
   unrealised P&L (safe skip, no crash).

3. ``_size_via_composer`` passes ``cash = nav - deployed_cost`` into
   ``SizingContext``, not bare ``nav``.

We bypass PaperRuntime.__init__ (which has DB/cache calls) via
``object.__new__`` and then seed only the attributes touched by the
methods under test.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest  # noqa: F401 (imported for future parametrize use)

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill
from backend.algo.paper.runtime import PaperRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(
    initial: Decimal = Decimal("100000"),
) -> PaperRuntime:
    """Build a PaperRuntime without calling __init__.

    Seeds only the attributes referenced by ``_account_snapshot`` and
    ``_size_via_composer``.
    """
    rt = object.__new__(PaperRuntime)
    rt._user_id = uuid4()
    rt._initial = initial
    rt._positions = PositionTracker()
    rt._last_marks: dict[str, Decimal] = {}
    rt._kill_switch_active = False
    rt._factor_cache: dict[Any, Any] = {}
    return rt


def _buy_fill(ticker: str, qty: int, price: Decimal) -> Fill:
    return Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side="BUY",
        qty=qty,
        fill_price=price,
        fill_date=date(2026, 6, 24),
        fees_inr=Decimal("0"),
        fee_rates_version="test",
    )


# ---------------------------------------------------------------------------
# Test 1: snapshot includes unrealised P&L when marks are present
# ---------------------------------------------------------------------------

def test_snapshot_includes_unrealised_pnl():
    """current_equity_inr = initial + realised + unrealised."""
    rt = _make_runtime(initial=Decimal("100000"))

    # Open a 10-share position at avg_price 200.
    rt._positions.apply_fill(_buy_fill("INFY.NS", 10, Decimal("200")))

    # Mark INFY.NS at 220 -> unrealised = (220-200)*10 = 200.
    rt._last_marks["INFY.NS"] = Decimal("220")

    snap = rt._account_snapshot()

    assert snap.daily_unrealised_pnl_inr == Decimal("200")
    assert snap.current_equity_inr == Decimal("100200")  # 100k + 0 + 200


# ---------------------------------------------------------------------------
# Test 2: ticker absent from _last_marks contributes 0 (no crash)
# ---------------------------------------------------------------------------

def test_snapshot_no_mark_contributes_zero():
    """Ticker with no entry in _last_marks is skipped -- unrealised=0."""
    rt = _make_runtime(initial=Decimal("50000"))

    rt._positions.apply_fill(_buy_fill("RELIANCE.NS", 5, Decimal("2000")))
    # No entry in _last_marks for RELIANCE.NS.

    snap = rt._account_snapshot()

    assert snap.daily_unrealised_pnl_inr == Decimal("0")
    assert snap.current_equity_inr == Decimal("50000")


# ---------------------------------------------------------------------------
# Test 3: _size_via_composer passes cash = nav - deployed_cost
# ---------------------------------------------------------------------------

def test_size_via_composer_passes_cash_minus_deployed():
    """SizingContext.cash must equal nav - deployed_cost, not nav."""
    rt = _make_runtime(initial=Decimal("100000"))

    # Open 10 shares at 500 -> deployed_cost = 5000.
    rt._positions.apply_fill(_buy_fill("TCS.NS", 10, Decimal("500")))
    rt._last_marks["TCS.NS"] = Decimal("510")

    captured: list[Any] = []

    def fake_compose_qty(qty_spec, ctx):  # noqa: ANN001
        captured.append(ctx)
        return 5

    with patch(
        "backend.algo.paper.runtime.compose_qty",
        side_effect=fake_compose_qty,
    ):
        rt._size_via_composer(
            qty_spec={"type": "fixed_qty", "qty": 5},
            ticker="TCS.NS",
            bar_date_ns=1_750_000_000_000_000_000,
            last_price=Decimal("510"),
        )

    assert len(captured) == 1, "compose_qty was not called"
    ctx = captured[0]

    # nav = initial + realised = 100000 + 0 = 100000
    # deployed_cost = 10 * 500 = 5000
    # cash = nav - deployed_cost = 95000
    assert ctx.nav == Decimal("100000")
    assert ctx.cash == Decimal("95000")
