"""Prophet model preparation, training, and forecast generation.

Functions
---------
- :func:`_prepare_data_for_prophet` — convert OHLCV to ds/y format.
- :func:`_train_prophet_model` — fit a Prophet model with holidays.
- :func:`_generate_forecast` — produce future-only forecast rows.
"""

import logging

import pandas as pd
import tools._forecast_shared as _sh
from prophet import Prophet

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
    # Use Adj Close only when >50 % of values are non-NaN;
    # Indian stocks via yfinance often store adj_close as
    # almost entirely NaN, which would leave too few rows
    # for Prophet after dropna.
    if "Adj Close" in df.columns and df["Adj Close"].notna().mean() > 0.5:
        price_col = "Adj Close"
    else:
        price_col = "Close"
    prophet_df = pd.DataFrame(
        {
            "ds": df.index.normalize(),
            "y": df[price_col].values,
        }
    )
    prophet_df = prophet_df.dropna(subset=["y"])
    prophet_df = prophet_df.sort_values("ds").reset_index(drop=True)
    prophet_df["ds"] = pd.to_datetime(prophet_df["ds"]).dt.tz_localize(None)
    _logger.debug("Prophet data prepared: %d rows", len(prophet_df))
    return prophet_df


def _train_prophet_model(
    prophet_df: pd.DataFrame,
    ticker: str = "",
    regressors: pd.DataFrame | None = None,
) -> Prophet:
    """Fit a Prophet model on the prepared price data.

    Enables yearly and weekly seasonality, disables daily
    seasonality, and adds market-specific holidays.
    Optional external regressors (VIX, index return) are
    added when provided.

    Args:
        prophet_df: DataFrame in Prophet format (``ds``,
            ``y``) as returned by
            :func:`_prepare_data_for_prophet`.
        ticker: Stock ticker — used for market-specific
            holidays and logging.
        regressors: Optional DataFrame aligned to
            ``prophet_df["ds"]`` with named columns to
            add as Prophet regressors (e.g. ``vix``,
            ``index_return``).

    Returns:
        A fitted :class:`~prophet.Prophet` model instance.
    """
    year_start = int(prophet_df["ds"].dt.year.min())
    year_end = int(prophet_df["ds"].dt.year.max()) + 2
    hols = _sh._build_holidays_df(
        range(year_start, year_end),
        ticker=ticker,
    )

    # Merge earnings dates as holidays (±2 day window).
    earnings_hols = _sh._fetch_earnings_holidays(ticker)
    if not earnings_hols.empty:
        hols = pd.concat(
            [hols, earnings_hols],
            ignore_index=True,
        )

    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        holidays=hols if not hols.empty else None,
        interval_width=0.80,
    )

    # Add external regressors (VIX, index return, etc.)
    train_df = prophet_df.copy()
    if regressors is not None and not regressors.empty:
        for col in regressors.columns:
            if col == "ds":
                continue
            model.add_regressor(col)
            train_df = train_df.merge(
                regressors[["ds", col]],
                on="ds",
                how="left",
            )
            train_df[col] = train_df[col].ffill().bfill()
        _logger.info(
            "Added regressors: %s",
            list(regressors.columns.drop("ds", errors="ignore")),
        )

    model.fit(train_df)
    _logger.info(
        "Prophet model fitted on %d rows (%s)",
        len(train_df),
        ticker or "unknown",
    )
    return model, train_df


def _generate_forecast(
    model: Prophet,
    prophet_df: pd.DataFrame,
    months: int,
    regressors: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Generate a price forecast for a given number of months.

    Args:
        model: A fitted :class:`~prophet.Prophet` model.
        prophet_df: The training data in Prophet format.
        months: Number of months to forecast.
        regressors: Optional regressor DataFrame. For future
            dates, values are forward-filled from the last
            known observation.

    Returns:
        DataFrame of **future-only** rows with columns ``ds``,
        ``yhat``, ``yhat_lower``, ``yhat_upper``.
    """
    periods = int(months * 30)
    future = model.make_future_dataframe(
        periods=periods,
        freq="D",
    )

    # Merge regressors into future dataframe.
    if regressors is not None and not regressors.empty:
        for col in regressors.columns:
            if col == "ds":
                continue
            future = future.merge(
                regressors[["ds", col]],
                on="ds",
                how="left",
            )
            # Forward-fill known values into future dates.
            future[col] = future[col].ffill().bfill()

    forecast = model.predict(future)

    last_date = prophet_df["ds"].max()
    future_mask = forecast["ds"] > last_date
    result = forecast.loc[
        future_mask, ["ds", "yhat", "yhat_lower", "yhat_upper"]
    ].copy()
    result = result.reset_index(drop=True)
    _logger.debug("Forecast generated: %d future rows", len(result))
    return result
