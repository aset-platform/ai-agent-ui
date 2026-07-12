"""Entry Strength Score (ESS) — pullback-health scoring for Watchlist
Stocks, orthogonal to the existing Quality Score."""

from __future__ import annotations

import pandas as pd


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


# (absorption_band, volume_band) -> score. Illustrative starting grid —
# refine once real allowed_tickers outcome data accumulates (see spec §6.1).
_ABSORPTION_VOLUME_GRID: dict[tuple[str, str], float] = {
    ("weak", "low"): 55, ("weak", "normal"): 45,
    ("weak", "elevated"): 25, ("weak", "extreme"): 10,
    ("neutral", "low"): 65, ("neutral", "normal"): 70,
    ("neutral", "elevated"): 55, ("neutral", "extreme"): 35,
    ("strong", "low"): 70, ("strong", "normal"): 85,
    ("strong", "elevated"): 95, ("strong", "extreme"): 80,
}


def relative_volume_ratio(
    volume_series: pd.Series, window: int = 20
) -> float | None:
    if len(volume_series) < window + 1:
        return None
    today = float(volume_series.iloc[-1])
    avg = float(volume_series.iloc[-(window + 1):-1].mean())
    if avg <= 0:
        return None
    return round(today / avg, 4)


def _absorption_band(score: float) -> str:
    if score < 40:
        return "weak"
    if score <= 70:
        return "neutral"
    return "strong"


def _volume_band(ratio: float) -> str:
    if ratio < 0.8:
        return "low"
    if ratio <= 1.5:
        return "normal"
    if ratio <= 2.5:
        return "elevated"
    return "extreme"


def absorption_volume_score(
    absorption_score: float, rel_volume: float | None
) -> float | None:
    if rel_volume is None:
        return None
    key = (_absorption_band(absorption_score), _volume_band(rel_volume))
    return _ABSORPTION_VOLUME_GRID[key]
