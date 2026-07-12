"""Entry Strength Score (ESS) — pullback-health scoring for Watchlist
Stocks, orthogonal to the existing Quality Score."""

from __future__ import annotations


def close_location_value(
    open_: float, high: float, low: float, close: float
) -> float:
    rng = high - low
    if rng <= 0:
        return 0.5
    return (close - low) / rng


def lower_wick_ratio(
    open_: float, high: float, low: float, close: float
) -> float:
    rng = high - low
    if rng <= 0:
        return 0.0
    body_low = min(open_, close)
    return (body_low - low) / rng


def selling_absorption_score(
    open_: float, high: float, low: float, close: float
) -> float:
    clv = close_location_value(open_, high, low, close)
    wick = lower_wick_ratio(open_, high, low, close)
    return round((0.6 * clv + 0.4 * wick) * 100, 4)
