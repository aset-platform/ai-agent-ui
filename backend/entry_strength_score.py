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


_SMA50_PROXIMITY_POINTS: list[tuple[float, float]] = [
    (0.0, 60.0), (-2.0, 95.0), (-3.0, 100.0),
    (-5.0, 90.0), (-7.0, 70.0), (-10.0, 40.0),
]


def _piecewise_closeness(
    points: list[tuple[float, float]], value: float
) -> float:
    pts = sorted(points, key=lambda p: p[0])
    if value <= pts[0][0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
    elif value >= pts[-1][0]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
    else:
        x0 = y0 = x1 = y1 = None
        for i in range(len(pts) - 1):
            if pts[i][0] <= value <= pts[i + 1][0]:
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                break
    slope = (y1 - y0) / (x1 - x0)
    result = y0 + slope * (value - x0)
    return round(max(0.0, min(100.0, result)), 4)


def sma50_proximity_score(
    dist_sma50_pct: float | None,
) -> float | None:
    if dist_sma50_pct is None:
        return None
    return _piecewise_closeness(_SMA50_PROXIMITY_POINTS, dist_sma50_pct)


def check_hard_gates(
    close: float,
    sma200: float | None,
    dist_sma50_pct: float | None,
) -> tuple[bool, str | None]:
    if sma200 is not None and close < sma200:
        return False, "price_below_sma200"
    if dist_sma50_pct is not None and dist_sma50_pct < -10.0:
        return False, "sma50_extended_beyond_10pct"
    return True, None
