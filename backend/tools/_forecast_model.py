"""Prophet model preparation, training, and forecast generation.

Functions
---------
- :func:`_prepare_data_for_prophet` — convert OHLCV to ds/y format.
- :func:`_train_prophet_model` — fit a Prophet model with holidays.
- :func:`_generate_forecast` — produce future-only forecast rows.
"""

import logging

import pandas as pd
from prophet import Prophet

import tools._forecast_shared as _sh

# Module-level logger for this module; kept at module scope intentionally.
_logger = logging.getLogger(__name__)


def _prepare_data_for_prophet(df: pd.DataFrame) -> pd.DataFrame:
    """Convert an OHLCV DataFrame to Prophet's ``ds`` / ``y`` format.

    Uses the ``Adj Close`` column when available and non-empty, falling
    back to ``Close``.  yfinance >= 1.2 no longer provides ``Adj Close``,
    and Iceberg may store it as all-NaN.  Any rows with NaN prices are dropped.

    Args:
        df: OHLCV DataFrame with a DatetimeIndex.

    Returns:
        DataFrame with exactly two columns: ``ds`` (datetime) and ``y``
        (adjusted close price), sorted ascending, with no NaN values.
    """
    if "Adj Close" in df.columns and df["Adj Close"].notna().any():
        price_col = "Adj Close"
    else:
        price_col = "Close"
    prophet_df = pd.DataFrame({
        "ds": df.index.normalize(),
        "y": df[price_col].values,
    })
    prophet_df = prophet_df.dropna(subset=["y"])
    prophet_df = prophet_df.sort_values("ds").reset_index(drop=True)
    prophet_df["ds"] = pd.to_datetime(prophet_df["ds"]).dt.tz_localize(None)
    _logger.debug("Prophet data prepared: %d rows", len(prophet_df))
    return prophet_df


def _train_prophet_model(prophet_df: pd.DataFrame) -> Prophet:
    """Fit a Prophet model on the prepared price data.

    Enables yearly and weekly seasonality, disables daily seasonality, and
    adds US federal holidays as special events.

    Args:
        prophet_df: DataFrame in Prophet format (``ds``, ``y``) as returned
            by :func:`_prepare_data_for_prophet`.

    Returns:
        A fitted :class:`~prophet.Prophet` model instance.
    """
    year_start = int(prophet_df["ds"].dt.year.min())
    year_end = int(prophet_df["ds"].dt.year.max()) + 2
    hols = _sh._build_holidays_df(range(year_start, year_end))

    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        holidays=hols if not hols.empty else None,
        interval_width=0.80,
    )
    model.fit(prophet_df)
    _logger.info("Prophet model fitted on %d rows", len(prophet_df))
    return model


def _generate_forecast(
    model: Prophet, prophet_df: pd.DataFrame, months: int
) -> pd.DataFrame:
    """Generate a price forecast for a given number of months ahead.

    Args:
        model: A fitted :class:`~prophet.Prophet` model.
        prophet_df: The training data in Prophet format.
        months: Number of months to forecast into the future.

    Returns:
        DataFrame of **future-only** rows with columns ``ds``,
        ``yhat``, ``yhat_lower``, ``yhat_upper``.
    """
    periods = int(months * 30)
    future = model.make_future_dataframe(periods=periods, freq="D")
    forecast = model.predict(future)

    last_date = prophet_df["ds"].max()
    future_mask = forecast["ds"] > last_date
    result = forecast.loc[future_mask, ["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    result = result.reset_index(drop=True)
    _logger.debug("Forecast generated: %d future rows", len(result))
    return result