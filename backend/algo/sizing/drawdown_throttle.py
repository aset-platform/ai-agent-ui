"""Drawdown throttle multiplier ladder + peak-NAV helper.

Per spec §3.4 + research §5:
    DD <  5%  → 1.00× (full size)
    DD < 10%  → 0.75×
    DD < 15%  → 0.50×
    DD < 20%  → 0.25×
    DD ≥ 20%  → 0.00× (halt new entries)
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal


def dd_multiplier(dd_from_peak_pct: Decimal) -> Decimal:
    """Lookup the size multiplier given current DD percent."""
    if dd_from_peak_pct < Decimal("5"):
        return Decimal("1.0")
    if dd_from_peak_pct < Decimal("10"):
        return Decimal("0.75")
    if dd_from_peak_pct < Decimal("15"):
        return Decimal("0.5")
    if dd_from_peak_pct < Decimal("20"):
        return Decimal("0.25")
    return Decimal("0")


def compute_dd_pct(
    equity_curve: list[tuple[date, Decimal]],
) -> Decimal:
    """Current DD from running peak, expressed as a percent."""
    if not equity_curve:
        return Decimal("0")
    sorted_curve = sorted(equity_curve, key=lambda x: x[0])
    peak = sorted_curve[0][1]
    current = sorted_curve[-1][1]
    for _, equity in sorted_curve:
        if equity > peak:
            peak = equity
    if peak <= 0:
        return Decimal("0")
    if current >= peak:
        return Decimal("0")
    return (peak - current) / peak * Decimal("100")
