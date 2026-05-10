"""3-stage composer: vol-target → caps → DD throttle."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.algo.sizing.composer import (
    SizingContext,
    compose_qty,
)


def _ctx(**overrides) -> SizingContext:
    base = dict(
        ticker="TEST.NS",
        bar_date=date(2026, 5, 10),
        nav=Decimal("100000"),
        cash=Decimal("100000"),
        stock_price=Decimal("1000"),
        realized_vol_annual=Decimal("0.30"),
        sector="IT",
        sector_exposure=Decimal("0"),
        equity_curve=[(date(2026, 5, 1), Decimal("100000"))],
        n_positions_target=10,
        expected_edge=None,
    )
    base.update(overrides)
    return SizingContext(**base)


def test_shares_mode_passthrough_no_throttle() -> None:
    """Legacy shares mode at low DD → no caps triggered → qty
    returned as-is (clamped only if exceeds per-position cap)."""
    ctx = _ctx()
    # 5 shares × 1000 = 5k = 5% NAV (under 12% cap)
    assert compose_qty({"shares": 5}, ctx) == 5


def test_per_position_cap_applied() -> None:
    ctx = _ctx()
    # 50 shares × 1000 = 50k = 50% NAV → cap to 12%
    assert compose_qty({"shares": 50}, ctx) == 12


def test_vol_target_mode() -> None:
    """vol_target_pct=2.0, vol=30%, nav=100k, n=10
    per_pos = 2 / sqrt(10) = 0.632%
    notional = 0.00632 * 100k / 0.30 = 2107
    qty = 2107 / 1000 = 2"""
    ctx = _ctx()
    qty = compose_qty({"vol_target_pct": 2.0}, ctx)
    assert qty == 2


def test_dd_throttle_applied() -> None:
    """Equity curve shows 10% DD → 0.5× multiplier.
    base 10 shares → throttled to 5."""
    ctx = _ctx(
        equity_curve=[
            (date(2026, 5, 1), Decimal("100000")),
            (date(2026, 5, 2), Decimal("110000")),  # peak
            (date(2026, 5, 3), Decimal("99000")),  # 10% DD
        ],
    )
    qty = compose_qty({"shares": 10}, ctx)
    assert qty == 5


def test_dd_throttle_zero_at_high_dd() -> None:
    """25% DD → halt entries entirely."""
    ctx = _ctx(
        equity_curve=[
            (date(2026, 5, 1), Decimal("100000")),
            (date(2026, 5, 2), Decimal("100000")),
            (date(2026, 5, 3), Decimal("75000")),  # 25% DD
        ],
    )
    assert compose_qty({"shares": 10}, ctx) == 0


def test_kelly_requires_expected_edge() -> None:
    """Kelly mode without expected_edge metadata returns 0 +
    logs a warning (callers treat as skip)."""
    ctx = _ctx(expected_edge=None)
    assert compose_qty({"kelly_fraction": 0.25}, ctx) == 0


def test_kelly_with_edge() -> None:
    """Kelly: f* = edge / vol^2; qty = f * frac * nav / price.
    edge=0.10, vol=0.30, frac=0.25, nav=100k, price=1000
    f* = 0.10 / 0.09 = 1.111
    capital = 1.111 * 0.25 * 100000 = 27,778
    qty = 27 (then capped per per-position 12% = 12 shares)"""
    ctx = _ctx(expected_edge=Decimal("0.10"))
    qty = compose_qty({"kelly_fraction": 0.25}, ctx)
    # After per-position cap (12% of 100k = 12k → 12 shares)
    assert qty == 12


def test_unknown_mode_returns_zero() -> None:
    ctx = _ctx()
    assert compose_qty({"chocolate": 5}, ctx) == 0
