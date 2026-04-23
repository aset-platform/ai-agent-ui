"""Volatility regime classification for Prophet forecast tuning.

Classifies a ticker into one of three regimes based on annualized
volatility, then provides matching Prophet constructor config and
logistic growth bounds.

Regime table
------------
Regime    | Ann. Vol   | Growth   | Transform | cps  | cp_range
----------|------------|----------|-----------|------|----------
stable    | < 30 %     | linear   | none      | 0.01 | 0.80
moderate  | 30–60 %    | linear   | log(y)    | 0.10 | 0.85
volatile  | ≥ 60 %     | logistic | log(y)    | 0.25 | 0.90

Boundaries are inclusive on the upper regime:
  30.0 → moderate,  60.0 → volatile.

None/missing volatility defaults to "moderate".

Notes
-----
- ``build_prophet_config`` returns only Prophet constructor kwargs.
  ``cap``/``floor`` belong on the DataFrame (add them before fitting).
- ``compute_logistic_bounds`` returns (cap, floor) from raw OHLCV.
"""

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STABLE_UPPER = 30.0   # < 30 → stable
_MODERATE_UPPER = 60.0  # 30–59.99 → moderate; ≥ 60 → volatile

_REGIMES = ("stable", "moderate", "volatile")

# Technical-bias adjustment limits.
MAX_BIAS = 0.15    # ±15 % cap on total additive bias
_TAPER_DAYS = 30   # bias tapers linearly to 0 over 30 days

# Prophet constructor kwargs per regime.
_REGIME_CONFIG: dict[str, dict] = {
    "stable": {
        "growth": "linear",
        "changepoint_prior_scale": 0.01,
        "changepoint_range": 0.80,
    },
    "moderate": {
        "growth": "linear",
        "changepoint_prior_scale": 0.10,
        "changepoint_range": 0.85,
    },
    "volatile": {
        "growth": "logistic",
        "changepoint_prior_scale": 0.25,
        "changepoint_range": 0.90,
    },
}

# Look-back windows (trading days).
_TWO_YEAR_DAYS = 504
_ONE_YEAR_DAYS = 252


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegimeConfig:
    """Immutable snapshot of a regime classification result.

    Attributes
    ----------
    regime:
        One of ``"stable"``, ``"moderate"``, ``"volatile"``.
    growth:
        Prophet ``growth`` param (``"linear"`` or ``"logistic"``).
    transform:
        y-transform to apply before fitting
        (``"none"`` or ``"log"``).
    changepoint_prior_scale:
        Prophet ``changepoint_prior_scale``.
    changepoint_range:
        Prophet ``changepoint_range``.
    """

    regime: str
    growth: str
    transform: str
    changepoint_prior_scale: float
    changepoint_range: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_regime(annualized_vol: float | None) -> str:
    """Return the volatility regime for *annualized_vol*.

    Parameters
    ----------
    annualized_vol:
        Annualized volatility as a percentage (e.g. 25.0 = 25 %).
        Pass ``None`` to get the default ("moderate").

    Returns
    -------
    str
        One of ``"stable"``, ``"moderate"``, ``"volatile"``.
    """
    if annualized_vol is None:
        _logger.debug(
            "annualized_vol is None — defaulting to 'moderate'"
        )
        return "moderate"

    vol = float(annualized_vol)
    if vol < _STABLE_UPPER:
        return "stable"
    if vol < _MODERATE_UPPER:
        return "moderate"
    return "volatile"


def compute_logistic_bounds(
    ohlcv_df: pd.DataFrame,
) -> tuple[float, float]:
    """Compute logistic cap and floor from OHLCV history.

    cap   = all-time-high over the last 2 years × 1.5
    floor = 52-week (1 year) low × 0.3

    Parameters
    ----------
    ohlcv_df:
        DataFrame with at least ``high`` and ``low`` columns,
        ordered chronologically. Must contain ≥ 1 row.

    Returns
    -------
    (cap, floor) : tuple[float, float]
    """
    two_yr = ohlcv_df["high"].iloc[-_TWO_YEAR_DAYS:]
    one_yr = ohlcv_df["low"].iloc[-_ONE_YEAR_DAYS:]

    ath_2yr = float(two_yr.max())
    low_1yr = float(one_yr.min())

    cap = ath_2yr * 1.5
    floor = low_1yr * 0.3

    _logger.debug(
        "Logistic bounds: cap=%.2f (ATH %.2f × 1.5), "
        "floor=%.2f (1yr_low %.2f × 0.3)",
        cap, ath_2yr, floor, low_1yr,
    )
    return cap, floor


def build_prophet_config(regime: str) -> dict:
    """Return Prophet constructor kwargs for *regime*.

    Parameters
    ----------
    regime:
        One of ``"stable"``, ``"moderate"``, ``"volatile"``.

    Returns
    -------
    dict
        Kwargs suitable for ``Prophet(**build_prophet_config(regime))``.
        Does NOT include ``cap`` or ``floor`` — add those to the
        DataFrame before fitting.

    Raises
    ------
    ValueError
        If *regime* is not a recognised value.
    """
    if regime not in _REGIME_CONFIG:
        raise ValueError(
            f"Unknown regime {regime!r}. "
            f"Expected one of {_REGIMES}."
        )
    # Return a shallow copy so callers cannot mutate the constant.
    return dict(_REGIME_CONFIG[regime])


def get_regime_config(
    annualized_vol: float | None,
) -> RegimeConfig:
    """Classify volatility and return a full :class:`RegimeConfig`.

    Convenience wrapper around :func:`classify_regime` and
    :func:`build_prophet_config` for callers that need a single
    structured result.

    Parameters
    ----------
    annualized_vol:
        Annualized volatility in percent, or ``None``.

    Returns
    -------
    RegimeConfig
    """
    regime = classify_regime(annualized_vol)
    cfg = _REGIME_CONFIG[regime]
    transform = "none" if regime == "stable" else "log"
    return RegimeConfig(
        regime=regime,
        growth=cfg["growth"],
        transform=transform,
        changepoint_prior_scale=cfg["changepoint_prior_scale"],
        changepoint_range=cfg["changepoint_range"],
    )


def apply_technical_bias(
    forecast_df: pd.DataFrame,
    analysis_row: dict | None,
) -> tuple[pd.DataFrame, dict]:
    """Adjust a Prophet forecast for technical-analysis bias.

    Reads RSI, MACD, and volume-spike signals from *analysis_row*
    and applies a linearly-tapered multiplier to ``yhat``,
    ``yhat_lower``, and ``yhat_upper`` for each forecast row.

    Parameters
    ----------
    forecast_df:
        Prophet forecast DataFrame with at minimum the columns
        ``ds``, ``yhat``, ``yhat_lower``, ``yhat_upper``.
    analysis_row:
        Dict produced by the analysis-summary pipeline.
        Expected keys: ``rsi_14`` (float), ``macd`` (float),
        ``macd_signal_line`` (float), ``volume_spike`` (bool),
        ``price_direction`` (``"up"``/``"down"``/``"flat"``).
        Pass ``None`` to skip adjustment.

    Returns
    -------
    tuple[pd.DataFrame, dict]
        * Adjusted copy of *forecast_df* (original is not mutated).
        * Metadata dict: ``{"total_bias": float,
          "signals": list[str]}``.

    Notes
    -----
    * Individual signal biases are summed then clamped to
      ``[-MAX_BIAS, +MAX_BIAS]`` (±15 %).
    * Taper: ``taper = max(0, 1 − day_index / 30)``.
      Full effect at day 0; zero at day ≥ 30.
    * ``multiplier = 1.0 + total_bias × taper`` is applied
      element-wise via vectorised pandas operations.
    """
    _empty_meta: dict = {"total_bias": 0.0, "signals": []}

    if analysis_row is None:
        return forecast_df.copy(), _empty_meta

    total_bias = 0.0
    signals: list[str] = []

    # --- RSI signal ---
    rsi = analysis_row.get("rsi_14")
    if rsi is not None:
        if rsi > 75:
            total_bias -= 0.15
            signals.append("rsi_overbought")
        elif rsi < 25:
            total_bias += 0.15
            signals.append("rsi_oversold")

    # --- MACD signal ---
    macd = analysis_row.get("macd")
    macd_sig = analysis_row.get("macd_signal_line")
    if macd is not None and macd_sig is not None:
        if macd < macd_sig:
            total_bias -= 0.08
            signals.append("macd_bearish")
        elif macd > macd_sig:
            total_bias += 0.08
            signals.append("macd_bullish")

    # --- Volume-spike signal ---
    vol_spike = analysis_row.get("volume_spike", False)
    price_dir = analysis_row.get("price_direction", "flat")
    if vol_spike:
        if price_dir == "down":
            total_bias -= 0.05
            signals.append("vol_spike_down")
        elif price_dir == "up":
            total_bias += 0.05
            signals.append("vol_spike_up")

    # Clamp to ±MAX_BIAS
    total_bias = max(-MAX_BIAS, min(MAX_BIAS, total_bias))

    if total_bias == 0.0:
        return forecast_df.copy(), {"total_bias": 0.0, "signals": signals}

    _logger.debug(
        "apply_technical_bias: total_bias=%.4f signals=%s",
        total_bias,
        signals,
    )

    # Vectorised taper: shape (n,), full weight at index 0, zero at ≥30
    n = len(forecast_df)
    day_indices = np.arange(n, dtype=float)
    taper = np.clip(1.0 - day_indices / _TAPER_DAYS, 0.0, None)
    multiplier = 1.0 + total_bias * taper

    adj = forecast_df.copy()
    for col in ("yhat", "yhat_lower", "yhat_upper"):
        adj[col] = adj[col] * multiplier

    return adj, {"total_bias": float(total_bias), "signals": signals}
