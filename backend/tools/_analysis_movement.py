"""Price movement and returns analysis helpers.

Functions
---------
- :func:`_calculate_returns` — daily, monthly, annual, cumulative return series.
- :func:`_analyse_price_movement` — bull/bear phases, drawdown, support/resistance.
"""

import logging
import math

import pandas as pd

# Module-level logger; cannot be moved into a class as these are module-level functions.
_logger = logging.getLogger(__name__)


def _calculate_returns(df: pd.DataFrame) -> dict:
    """Calculate daily, monthly, annual, and cumulative returns.

    Args:
        df: OHLCV DataFrame with a DatetimeIndex and a ``Close`` column.

    Returns:
        Dictionary with keys ``"daily"``, ``"monthly"``, ``"annual"``,
        and ``"cumulative"``, each containing a :class:`pandas.Series`.
    """
    close = df["Close"]
    daily = close.pct_change().dropna()
    monthly = close.resample("ME").last().pct_change().dropna()
    annual = close.resample("YE").last().pct_change().dropna()
    cumulative = (1 + daily).cumprod() - 1
    return {
        "daily": daily,
        "monthly": monthly,
        "annual": annual,
        "cumulative": cumulative,
    }


def _analyse_price_movement(df: pd.DataFrame) -> dict:
    """Analyse bull/bear phases, drawdown, support/resistance, volatility, Sharpe.

    Args:
        df: DataFrame with indicators already added by
            :func:`~tools._analysis_indicators._calculate_technical_indicators`.

    Returns:
        Dictionary with keys: ``bull_phase_pct``, ``bear_phase_pct``,
        ``max_drawdown_pct``, ``max_drawdown_duration_days``,
        ``support_levels``, ``resistance_levels``,
        ``annualized_volatility_pct``, ``annualized_return_pct``,
        ``sharpe_ratio``.
    """
    close = df["Close"]
    daily_returns = close.pct_change().dropna()

    mask = df["SMA_200"].notna()
    above = close[mask] > df["SMA_200"][mask]
    bull_pct = float(above.mean() * 100)
    bear_pct = 100.0 - bull_pct

    rolling_max = close.cummax()
    drawdown = (close - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min() * 100)

    in_drawdown = (drawdown < 0).astype(int)
    groups = in_drawdown * (
        in_drawdown.groupby((in_drawdown != in_drawdown.shift()).cumsum()).cumcount()
        + 1
    )
    max_dd_duration = int(groups.max())

    recent = df.tail(252)
    support_levels = sorted(recent["Low"].nsmallest(3).round(2).tolist())
    resistance_levels = sorted(
        recent["High"].nlargest(3).round(2).tolist(), reverse=True
    )

    ann_vol_pct = float(daily_returns.std() * math.sqrt(252) * 100)
    ann_return = float(daily_returns.mean() * 252)
    ann_vol_dec = daily_returns.std() * math.sqrt(252)
    sharpe = (ann_return - 0.04) / ann_vol_dec if ann_vol_dec > 0 else 0.0

    return {
        "bull_phase_pct": round(bull_pct, 1),
        "bear_phase_pct": round(bear_pct, 1),
        "max_drawdown_pct": round(max_drawdown, 2),
        "max_drawdown_duration_days": max_dd_duration,
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "annualized_volatility_pct": round(ann_vol_pct, 2),
        "annualized_return_pct": round(ann_return * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
    }
