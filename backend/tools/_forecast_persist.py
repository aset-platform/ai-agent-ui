"""Forecast persistence helpers — parquet file writing.

Functions
---------
- :func:`_save_forecast` — save future forecast rows to a parquet file.
"""

import logging

import pandas as pd
import tools._forecast_shared as _sh

# Module-level logger; cannot be moved into a class as this module exposes
# plain functions rather than a class hierarchy.
_logger = logging.getLogger(__name__)


def _save_forecast(forecast_df: pd.DataFrame, ticker: str, months: int) -> str:
    """Save forecast results to a parquet file.

    Args:
        forecast_df: Future-only forecast DataFrame with columns ``ds``,
            ``yhat``, ``yhat_lower``, ``yhat_upper``.
        ticker: Stock ticker symbol (already uppercased).
        months: Forecast horizon in months (used in the filename).

    Returns:
        Absolute path to the saved parquet file as a string.
    """
    _sh._DATA_FORECASTS.mkdir(parents=True, exist_ok=True)
    out_path = _sh._DATA_FORECASTS / f"{ticker}_{months}m_forecast.parquet"
    forecast_df.to_parquet(str(out_path), engine="pyarrow", index=False)
    _logger.info("Forecast saved: %s", out_path)
    return str(out_path)
