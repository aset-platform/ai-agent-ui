"""Tier 1 and Tier 2 feature computation for Prophet regressors.

Tier 1: slow-moving fundamentals derived from existing Iceberg data
(analysis_summary, piotroski_scores, quarterly_results).

Tier 2: market microstructure features computed from OHLCV + sector
index DataFrames.

Public API
----------
compute_tier1_features  — fundamental / regime features
compute_tier2_features  — volume, OBV, calendar, proximity features
build_future_features   — propagate last-known features to future dates
get_sector_index_mapping — sector → index ticker lookup

Private helpers
---------------
_safe_growth            — capped growth ratio
_days_to_expiry         — days until NSE monthly expiry (last Thursday)
_days_to_nearest_earnings — distance to nearest earnings date
"""

import logging
import math
from calendar import monthrange
from datetime import date, timedelta

import numpy as np
import pandas as pd

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_SECTOR_MAP_INDIA: dict[str, str] = {
    "Financial Services": "^NSEBANK",
    "Information Technology": "^CNXIT",
    "Pharmaceutical": "^CNXPHARMA",
    "Fast Moving Consumer Goods": "^CNXFMCG",
    "Automobile and Auto Components": "^CNXAUTO",
}

_SECTOR_MAP_US: dict[str, str] = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
}

# Slow-moving feature keys held constant in future regressor frame.
_SLOW_FEATURES = [
    "volatility_regime",
    "trend_strength",
    "sr_position",
    "piotroski",
    "revenue_growth",
    "eps_growth",
    "sector_relative_strength",
    "earnings_proximity",
]

# Transient feature keys zeroed out in future regressor frame.
_TRANSIENT_FEATURES = ["volume_anomaly", "obv_trend"]

# Calendar feature keys recomputed per future date.
_CALENDAR_FEATURES = [
    "day_of_week",
    "month_of_year",
    "expiry_proximity",
]


def _safe_growth(
    curr: float | None,
    prev: float | None,
) -> float:
    """Return growth ratio ``(curr - prev) / |prev|`` capped to ±1.0.

    Returns 0.0 when either value is None, NaN, or *prev* is zero.
    """
    try:
        if curr is None or prev is None:
            return 0.0
        c = float(curr)
        p = float(prev)
        if math.isnan(c) or math.isnan(p):
            return 0.0
        if p == 0.0:
            return 0.0
        ratio = (c - p) / abs(p)
        return float(max(-1.0, min(1.0, ratio)))
    except Exception:
        return 0.0


def _last_thursday_of_month(dt: date) -> date:
    """Return the last Thursday of *dt*'s calendar month."""
    _, last_day_num = monthrange(dt.year, dt.month)
    last_day = date(dt.year, dt.month, last_day_num)
    # Thursday == weekday 3
    offset = (last_day.weekday() - 3) % 7
    return last_day - timedelta(days=offset)


def _days_to_expiry(dt: date) -> float:
    """Days from *dt* to last Thursday of the same month.

    Returns 0.0 if *dt* is the last Thursday itself.
    Normalised by dividing by 30.0 and capped at 1.0.
    """
    expiry = _last_thursday_of_month(dt)
    days = (expiry - dt).days
    if days < 0:
        # dt is past this month's expiry — treat as 0
        days = 0
    return min(days / 30.0, 1.0)


def _days_to_nearest_earnings(
    dt: date,
    dates: list,
) -> float | None:
    """Return the minimum absolute days between *dt* and any date in
    *dates*.

    Returns ``None`` when *dates* is empty or None.  Dates are
    converted to :class:`datetime.date` if they are not already.
    """
    if not dates:
        return None
    min_days: float | None = None
    for d in dates:
        try:
            if hasattr(d, "date"):
                d = d.date()
            elif not isinstance(d, date):
                d = pd.Timestamp(d).date()
            diff = abs((d - dt).days)
            if min_days is None or diff < min_days:
                min_days = float(diff)
        except Exception:
            continue
    return min_days


# ---------------------------------------------------------------------------
# Tier 1 — fundamental features
# ---------------------------------------------------------------------------

def compute_tier1_features(
    analysis_row: dict | None,
    piotroski_row: dict | None,
    quarterly_rows: list[dict],
    current_price: float,
) -> dict[str, float]:
    """Compute Tier 1 features from existing Iceberg data.

    Parameters
    ----------
    analysis_row:
        Row from ``analysis_summary`` with keys:
        ``annualized_volatility``, ``bull_phase_pct``,
        ``bear_phase_pct``, ``support_level``,
        ``resistance_level``.
    piotroski_row:
        Row from ``piotroski_scores`` with key ``f_score``.
    quarterly_rows:
        List of rows from ``quarterly_results`` with keys
        ``total_revenue`` and ``diluted_eps``, sorted most
        recent first.  Needs ≥ 2 rows for growth features.
    current_price:
        Latest close price of the ticker.

    Returns
    -------
    dict[str, float]
        Feature map with keys: ``volatility_regime``,
        ``trend_strength``, ``sr_position``, ``piotroski``,
        ``revenue_growth``, ``eps_growth``.  Missing inputs
        default to 0.0.
    """
    out: dict[str, float] = {
        "volatility_regime": 0.0,
        "trend_strength": 0.0,
        "sr_position": 0.0,
        "piotroski": 0.0,
        "revenue_growth": 0.0,
        "eps_growth": 0.0,
    }

    # --- Analysis summary features ---
    if analysis_row:
        try:
            vol = analysis_row.get("annualized_volatility_pct")
            if vol is not None and not math.isnan(float(vol)):
                out["volatility_regime"] = float(vol) / 100.0

            bull = analysis_row.get("bull_phase_pct")
            bear = analysis_row.get("bear_phase_pct")
            if (
                bull is not None
                and bear is not None
                and not math.isnan(float(bull))
                and not math.isnan(float(bear))
            ):
                out["trend_strength"] = (
                    float(bull) - float(bear)
                ) / 100.0

            sup = analysis_row.get("support_levels")
            res = analysis_row.get("resistance_levels")
            if (
                sup is not None
                and res is not None
                and not math.isnan(float(sup))
                and not math.isnan(float(res))
            ):
                span = float(res) - float(sup)
                if span > 0.0:
                    raw = (current_price - float(sup)) / span
                    out["sr_position"] = float(
                        max(0.0, min(1.0, raw))
                    )
        except Exception as exc:
            _logger.warning(
                "tier1: analysis_row parse error: %s", exc
            )

    # --- Piotroski ---
    if piotroski_row:
        try:
            score = piotroski_row.get("f_score")
            if score is not None and not math.isnan(float(score)):
                out["piotroski"] = float(score) / 9.0
        except Exception as exc:
            _logger.warning(
                "tier1: piotroski_row parse error: %s", exc
            )

    # --- Quarterly growth ---
    if quarterly_rows and len(quarterly_rows) >= 2:
        try:
            curr_q = quarterly_rows[0]
            prev_q = quarterly_rows[1]
            out["revenue_growth"] = _safe_growth(
                curr_q.get("total_revenue"),
                prev_q.get("total_revenue"),
            )
            out["eps_growth"] = _safe_growth(
                curr_q.get("diluted_eps"),
                prev_q.get("diluted_eps"),
            )
        except Exception as exc:
            _logger.warning(
                "tier1: quarterly_rows parse error: %s", exc
            )

    return out


# ---------------------------------------------------------------------------
# Tier 2 — market microstructure features
# ---------------------------------------------------------------------------

def _compute_obv(df: pd.DataFrame) -> pd.Series:
    """Compute On-Balance Volume from a sorted OHLCV DataFrame."""
    close = df["close"].values
    volume = df["volume"].values
    n = len(close)
    obv = np.zeros(n, dtype=float)
    for i in range(1, n):
        if close[i] > close[i - 1]:
            obv[i] = obv[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            obv[i] = obv[i - 1] - volume[i]
        else:
            obv[i] = obv[i - 1]
    return pd.Series(obv, index=df.index)


def compute_tier2_features(
    ohlcv_df: pd.DataFrame,
    sector_index_df: pd.DataFrame | None,
    earnings_dates: list | None,
) -> dict[str, float]:
    """Compute Tier 2 market microstructure features.

    Parameters
    ----------
    ohlcv_df:
        DataFrame with columns ``date``, ``close``, ``volume``.
        Must have at least 21 rows for volume SMA-20.
    sector_index_df:
        DataFrame with columns ``date``, ``close`` for the
        sector index, or ``None`` to skip relative strength.
    earnings_dates:
        List of date-like objects for upcoming/recent earnings,
        or ``None``.

    Returns
    -------
    dict[str, float]
        Feature map with keys: ``volume_anomaly``, ``obv_trend``,
        ``sector_relative_strength``, ``day_of_week``,
        ``month_of_year``, ``expiry_proximity``,
        ``earnings_proximity``.
    """
    out: dict[str, float] = {
        "volume_anomaly": 0.0,
        "obv_trend": 0.0,
        "sector_relative_strength": 0.0,
        "day_of_week": 0.0,
        "month_of_year": 0.0,
        "expiry_proximity": 0.0,
        "earnings_proximity": 0.5,
    }

    if ohlcv_df is None or len(ohlcv_df) < 2:
        return out

    df = ohlcv_df.copy()

    # Normalise column names: yfinance uses Title-case with
    # DatetimeIndex; Iceberg/DuckDB uses lower-case with a
    # ``date`` column.  Unify to lower-case + ``date`` column.
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    if "date" not in df.columns and df.index.name in (
        "date", "Date",
    ):
        df = df.reset_index()
        df.rename(
            columns={df.columns[0]: "date"}, inplace=True,
        )
    elif "date" not in df.columns:
        # Last resort: use index as date
        df = df.reset_index()
        df.rename(
            columns={df.columns[0]: "date"}, inplace=True,
        )

    df = df.sort_values("date").reset_index(drop=True)

    last_row = df.iloc[-1]

    # --- Last date for calendar features ---
    last_date_raw = last_row["date"]
    if hasattr(last_date_raw, "date"):
        last_date = last_date_raw.date()
    elif isinstance(last_date_raw, date):
        last_date = last_date_raw
    else:
        last_date = pd.Timestamp(last_date_raw).date()

    # --- Calendar features ---
    out["day_of_week"] = last_date.weekday() / 4.0
    out["month_of_year"] = last_date.month / 12.0
    out["expiry_proximity"] = _days_to_expiry(last_date)

    # --- Volume anomaly: volume / SMA20 - 1 ---
    window = 20
    if len(df) >= window + 1:
        vol_series = df["volume"].astype(float)
        sma20 = vol_series.rolling(window).mean()
        last_vol = float(vol_series.iloc[-1])
        last_sma = float(sma20.iloc[-1])
        if last_sma > 0.0 and not math.isnan(last_sma):
            out["volume_anomaly"] = (last_vol / last_sma) - 1.0

    # --- OBV trend: slope over last 20 days normalised by mean vol ---
    obv_window = 20
    if len(df) >= obv_window:
        obv = _compute_obv(df)
        obv_tail = obv.iloc[-obv_window:].values.astype(float)
        x = np.arange(obv_window, dtype=float)
        # Linear regression slope via lstsq
        A = np.column_stack([x, np.ones(obv_window)])
        result = np.linalg.lstsq(A, obv_tail, rcond=None)
        slope = float(result[0][0])
        mean_vol = float(df["volume"].astype(float).mean())
        if mean_vol > 0.0:
            out["obv_trend"] = slope / mean_vol

    # --- Sector relative strength (20-day return) ---
    if sector_index_df is not None and len(sector_index_df) >= 21:
        try:
            sdf = sector_index_df.copy()
            sdf.columns = [
                c.lower().replace(" ", "_") for c in sdf.columns
            ]
            if "date" not in sdf.columns:
                sdf = sdf.reset_index()
                sdf.rename(
                    columns={sdf.columns[0]: "date"},
                    inplace=True,
                )
            sdf = sdf.sort_values("date").reset_index(drop=True)
            ticker_ret = (
                float(df["close"].iloc[-1])
                / float(df["close"].iloc[-21])
            ) - 1.0
            sector_ret = (
                float(sdf["close"].iloc[-1])
                / float(sdf["close"].iloc[-21])
            ) - 1.0
            out["sector_relative_strength"] = ticker_ret - sector_ret
        except Exception as exc:
            _logger.warning(
                "tier2: sector_relative_strength error: %s", exc
            )
    elif sector_index_df is not None and len(sector_index_df) >= 2:
        # Fallback: use available data
        try:
            sdf = sector_index_df.copy()
            sdf.columns = [
                c.lower().replace(" ", "_") for c in sdf.columns
            ]
            if "date" not in sdf.columns:
                sdf = sdf.reset_index()
                sdf.rename(
                    columns={sdf.columns[0]: "date"},
                    inplace=True,
                )
            sdf = sdf.sort_values("date").reset_index(drop=True)
            n = min(len(df), len(sdf))
            ticker_ret = (
                float(df["close"].iloc[-1])
                / float(df["close"].iloc[-n])
            ) - 1.0
            sector_ret = (
                float(sdf["close"].iloc[-1])
                / float(sdf["close"].iloc[-n])
            ) - 1.0
            out["sector_relative_strength"] = ticker_ret - sector_ret
        except Exception as exc:
            _logger.warning(
                "tier2: sector_relative_strength fallback: %s", exc
            )

    # --- Earnings proximity ---
    if earnings_dates:
        dist = _days_to_nearest_earnings(last_date, earnings_dates)
        if dist is not None:
            out["earnings_proximity"] = min(dist / 90.0, 1.0)

    return out


# ---------------------------------------------------------------------------
# Future regressor frame
# ---------------------------------------------------------------------------

def build_future_features(
    last_known: dict[str, float],
    future_dates: list,
) -> pd.DataFrame:
    """Build a Prophet-compatible future regressor DataFrame.

    Slow-moving features are held constant at ``last_known`` values.
    Transient features (volume_anomaly, obv_trend) are zeroed.
    Calendar features are recomputed for each future date.
    ``earnings_proximity`` is held constant (slow-moving).

    Parameters
    ----------
    last_known:
        Feature dict as returned by ``compute_tier1_features`` /
        ``compute_tier2_features``.
    future_dates:
        Ordered list of future :class:`datetime.date` objects.

    Returns
    -------
    pd.DataFrame
        One row per future date, columns matching ``last_known``.
    """
    rows: list[dict[str, float]] = []
    for raw_dt in future_dates:
        if hasattr(raw_dt, "date"):
            dt = raw_dt.date()
        elif isinstance(raw_dt, date):
            dt = raw_dt
        else:
            dt = pd.Timestamp(raw_dt).date()

        row: dict[str, float] = {}
        # 1. Slow features — held constant
        for key in _SLOW_FEATURES:
            row[key] = float(last_known.get(key, 0.0))
        # 2. Transient features — zeroed
        for key in _TRANSIENT_FEATURES:
            row[key] = 0.0
        # 3. Calendar features — recomputed
        row["day_of_week"] = dt.weekday() / 4.0
        row["month_of_year"] = dt.month / 12.0
        row["expiry_proximity"] = _days_to_expiry(dt)
        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sector index mapping
# ---------------------------------------------------------------------------

def get_sector_index_mapping(market: str) -> dict[str, str]:
    """Return sector name → index ticker mapping for *market*.

    Parameters
    ----------
    market:
        ``"india"`` or ``"us"``.  Unknown markets return ``{}``.

    Returns
    -------
    dict[str, str]
        Sector name to Yahoo Finance ticker symbol.
    """
    key = market.lower().strip()
    if key == "india":
        return dict(_SECTOR_MAP_INDIA)
    if key == "us":
        return dict(_SECTOR_MAP_US)
    return {}
