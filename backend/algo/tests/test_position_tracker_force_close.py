"""Tests for ``PositionTracker.force_close_all``.

Covers the two callers in the runner:
- period-end MTM force-close
- MIS daily square-off
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill


def _buy(ticker: str, qty: int, price: str, day: date) -> Fill:
    return Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side="BUY",
        qty=qty,
        fill_price=Decimal(price),
        fill_date=day,
        fees_inr=Decimal("0"),
        fee_rates_version="t",
        fill_ts_ns=None,
    )


def test_force_close_at_higher_mark_books_profit():
    pt = PositionTracker()
    pt.apply_fill(_buy("X", 10, "100", date(2026, 5, 1)))
    closed = pt.force_close_all(
        marks={"X": Decimal("120")},
        fill_date=date(2026, 5, 12),
        exit_reason="period_end_mtm",
    )
    assert len(closed) == 1
    pos = closed[0]
    assert pos.qty == 10
    assert pos.exit_reason == "period_end_mtm"
    # (120 - 100) * 10 = 200
    assert pos.realised_pnl_inr == Decimal("200")
    assert pt.open_positions() == {}
    assert pt.total_realised_pnl_inr() == Decimal("200")


def test_force_close_at_lower_mark_books_loss():
    pt = PositionTracker()
    pt.apply_fill(_buy("X", 5, "200", date(2026, 5, 1)))
    closed = pt.force_close_all(
        marks={"X": Decimal("180")},
        fill_date=date(2026, 5, 12),
        exit_reason="mis_square_off",
    )
    assert len(closed) == 1
    assert closed[0].realised_pnl_inr == Decimal("-100")
    assert closed[0].exit_reason == "mis_square_off"


def test_force_close_skips_unmarked_tickers():
    """If a ticker has no mark we can't fairly value the exit;
    leave it open + signal the caller via the missing closed
    row."""
    pt = PositionTracker()
    pt.apply_fill(_buy("X", 10, "100", date(2026, 5, 1)))
    pt.apply_fill(_buy("Y", 10, "50", date(2026, 5, 1)))
    closed = pt.force_close_all(
        marks={"X": Decimal("110")},  # Y has no mark
        fill_date=date(2026, 5, 12),
        exit_reason="period_end_mtm",
    )
    assert {p.ticker for p in closed} == {"X"}
    assert set(pt.open_positions().keys()) == {"Y"}


def test_force_close_no_op_on_empty_book():
    pt = PositionTracker()
    closed = pt.force_close_all(
        marks={"X": Decimal("100")},
        fill_date=date(2026, 5, 12),
        exit_reason="period_end_mtm",
    )
    assert closed == []
    assert pt.total_realised_pnl_inr() == Decimal("0")


def test_force_close_preserves_open_date_on_closed_row():
    pt = PositionTracker()
    pt.apply_fill(_buy("X", 5, "100", date(2026, 5, 1)))
    closed = pt.force_close_all(
        marks={"X": Decimal("105")},
        fill_date=date(2026, 5, 12),
        exit_reason="period_end_mtm",
    )
    assert closed[0].opened_at == date(2026, 5, 1)
    assert closed[0].closed_at == date(2026, 5, 12)
