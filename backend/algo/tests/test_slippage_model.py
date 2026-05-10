"""Slippage model tests (REGIME-7).

Per research §10: ``max(5, 50 * order_value / ADTV) bps``. Min
floor of 5bps applies even when ADTV is missing/zero/NaN so we
never raise on ingestion gaps.
"""
from __future__ import annotations

from decimal import Decimal

from backend.algo.backtest.sim_broker import estimate_slippage_bps


def test_min_5bps_when_order_tiny() -> None:
    bps = estimate_slippage_bps(
        order_value_inr=Decimal("1000"),
        ticker_adtv_inr=Decimal("1000000000"),  # 100cr ADTV
    )
    assert bps == Decimal("5")


def test_scales_with_order_value() -> None:
    """Order = 1% of ADTV → 50 * 0.01 = 0.5bps → still floored at 5.
    Order = 100% of ADTV → 50 * 1 = 50bps."""
    bps_low = estimate_slippage_bps(
        order_value_inr=Decimal("1000000"),       # 10L
        ticker_adtv_inr=Decimal("100000000"),     # 10cr
    )
    assert bps_low == Decimal("5")
    bps_high = estimate_slippage_bps(
        order_value_inr=Decimal("100000000"),     # 10cr
        ticker_adtv_inr=Decimal("100000000"),     # 10cr
    )
    assert bps_high == Decimal("50")


def test_zero_adtv_returns_minimum() -> None:
    bps = estimate_slippage_bps(
        order_value_inr=Decimal("100000"),
        ticker_adtv_inr=Decimal("0"),
    )
    # Formula would div/zero — fall back to min 5bps.
    assert bps == Decimal("5")


def test_nan_adtv_returns_minimum() -> None:
    bps = estimate_slippage_bps(
        order_value_inr=Decimal("100000"),
        ticker_adtv_inr=Decimal("NaN"),
    )
    assert bps == Decimal("5")


def test_negative_adtv_returns_minimum() -> None:
    bps = estimate_slippage_bps(
        order_value_inr=Decimal("100000"),
        ticker_adtv_inr=Decimal("-1"),
    )
    assert bps == Decimal("5")
