"""Vol-target sizing tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.algo.sizing.vol_target import vol_target_qty


def test_canonical_example() -> None:
    """Per spec: target=1.5%, nav=100k, price=1000, vol=30%, n=10
    → per_pos_vol = 1.5 / sqrt(10) = 0.4743%
    → notional = 0.004743 * 100000 / 0.30 = 1581
    → qty = floor(1581 / 1000) = 1
    """
    qty = vol_target_qty(
        target_portfolio_vol_pct=Decimal("1.5"),
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        stock_realized_vol_annual=Decimal("0.30"),
        n_positions_target=10,
    )
    assert qty == 1


def test_inverse_vol_scaling() -> None:
    """Higher vol → smaller qty for same target."""
    common = dict(
        target_portfolio_vol_pct=Decimal("2.0"),
        nav=Decimal("1000000"),
        stock_price=Decimal("100"),
        n_positions_target=10,
    )
    low = vol_target_qty(
        **common, stock_realized_vol_annual=Decimal("0.15"),
    )
    high = vol_target_qty(
        **common, stock_realized_vol_annual=Decimal("0.45"),
    )
    assert low > high
    assert low == pytest.approx(high * 3, abs=2)


def test_zero_vol_returns_zero() -> None:
    qty = vol_target_qty(
        target_portfolio_vol_pct=Decimal("1.0"),
        nav=Decimal("100000"),
        stock_price=Decimal("100"),
        stock_realized_vol_annual=Decimal("0"),
        n_positions_target=5,
    )
    assert qty == 0


def test_nan_vol_returns_zero() -> None:
    qty = vol_target_qty(
        target_portfolio_vol_pct=Decimal("1.0"),
        nav=Decimal("100000"),
        stock_price=Decimal("100"),
        stock_realized_vol_annual=Decimal("NaN"),
        n_positions_target=5,
    )
    assert qty == 0


def test_zero_price_returns_zero() -> None:
    qty = vol_target_qty(
        target_portfolio_vol_pct=Decimal("1.0"),
        nav=Decimal("100000"),
        stock_price=Decimal("0"),
        stock_realized_vol_annual=Decimal("0.30"),
        n_positions_target=5,
    )
    assert qty == 0
