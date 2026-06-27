"""Drawdown throttle ladder + DD computation."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.algo.sizing.drawdown_throttle import (
    compute_dd_pct,
    dd_multiplier,
)


@pytest.mark.parametrize(
    "dd_pct,expected",
    [
        (Decimal("0"), Decimal("1.0")),
        (Decimal("4.99"), Decimal("1.0")),
        (Decimal("5"), Decimal("0.75")),
        (Decimal("9.99"), Decimal("0.75")),
        (Decimal("10"), Decimal("0.5")),
        (Decimal("14.99"), Decimal("0.5")),
        (Decimal("15"), Decimal("0.25")),
        (Decimal("19.99"), Decimal("0.25")),
        (Decimal("20"), Decimal("0")),
        (Decimal("25"), Decimal("0")),
    ],
)
def test_dd_ladder(dd_pct, expected) -> None:
    assert dd_multiplier(dd_pct) == expected


def test_compute_dd_at_peak_is_zero() -> None:
    curve = [
        (date(2026, 5, 1), Decimal("100000")),
        (date(2026, 5, 2), Decimal("105000")),
    ]
    assert compute_dd_pct(curve) == Decimal("0")


def test_compute_dd_below_peak() -> None:
    curve = [
        (date(2026, 5, 1), Decimal("100000")),
        (date(2026, 5, 2), Decimal("110000")),  # peak
        (date(2026, 5, 3), Decimal("99000")),
    ]
    # DD = (110k - 99k) / 110k = 10%
    assert compute_dd_pct(curve) == Decimal("10")


def test_compute_dd_empty_returns_zero() -> None:
    assert compute_dd_pct([]) == Decimal("0")


# Guard 4 — peak <= 0 halt tests
def test_compute_dd_zero_peak_returns_100() -> None:
    """A curve whose peak is zero must return 100 (not 0) so that
    dd_multiplier maps it to 0x (halt), not 1x (full size)."""
    curve = [(date(2026, 5, 1), Decimal("0"))]
    dd = compute_dd_pct(curve)
    assert dd == Decimal("100")


def test_compute_dd_negative_peak_returns_100() -> None:
    """Negative peak NAV -> 100% DD -> halt."""
    curve = [
        (date(2026, 5, 1), Decimal("-5000")),
        (date(2026, 5, 2), Decimal("-3000")),
    ]
    dd = compute_dd_pct(curve)
    assert dd == Decimal("100")


def test_zero_peak_halts_multiplier() -> None:
    """End-to-end: zero-peak curve -> dd_multiplier == 0 (halt)."""
    curve = [(date(2026, 5, 1), Decimal("0"))]
    mult = dd_multiplier(compute_dd_pct(curve))
    assert mult == Decimal("0")
