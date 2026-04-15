"""Prophet model preparation, training, and forecast generation.

Functions
---------
- :func:`_prepare_data_for_prophet` — convert OHLCV to ds/y format.
- :func:`_train_prophet_model` — fit a Prophet model with holidays.
- :func:`_generate_forecast` — produce future-only forecast rows.
"""

import logging

import numpy as np
import pandas as pd
import tools._forecast_shared as _sh
from prophet import Prophet
from tools._forecast_regime import (
    build_prophet_config,
    compute_logistic_bounds,
    get_regime_config,
)

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
    regime: str = "moderate",
) -> tuple:
    """Fit a Prophet model on the prepared price data.

    Enables yearly and weekly seasonality, disables daily
    seasonality, and adds market-specific holidays.
    Optional external regressors (VIX, index return) are
    added when provided.

    The ``regime`` parameter controls Prophet's changepoint
    sensitivity, growth type, and y-transform:

    * ``"stable"``   — linear growth, no transform.
    * ``"moderate"`` — linear growth, log(y) transform.
    * ``"volatile"`` — logistic growth, log(y) transform.

    Regime metadata is stored on the returned model as
    ``model._regime_transform`` (``"none"`` or ``"log"``) and
    ``model._regime_growth`` (``"linear"`` or ``"logistic"``),
    so that :func:`_generate_forecast` can apply the correct
    inverse transform.

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
        regime: Volatility regime — one of ``"stable"``,
            ``"moderate"``, ``"volatile"``.  Defaults to
            ``"moderate"`` for backward compatibility.

    Returns:
        A tuple ``(model, train_df)`` where *model* is a
        fitted :class:`~prophet.Prophet` instance and
        *train_df* is the (possibly transformed) training
        DataFrame.
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

    # --- Regime-adaptive Prophet configuration ---
    rcfg = get_regime_config(
        {"stable": 15.0, "moderate": 45.0, "volatile": 75.0}.get(regime, 45.0)
    )

    prophet_kwargs = build_prophet_config(regime)
    prophet_kwargs["holidays"] = hols if not hols.empty else None
    prophet_kwargs["interval_width"] = 0.80
    prophet_kwargs["yearly_seasonality"] = True
    prophet_kwargs["weekly_seasonality"] = True
    prophet_kwargs["daily_seasonality"] = False

    _logger.info(
        "Regime=%s growth=%s transform=%s cps=%.2f cr=%.2f (%s)",
        rcfg.regime,
        rcfg.growth,
        rcfg.transform,
        rcfg.changepoint_prior_scale,
        rcfg.changepoint_range,
        ticker or "unknown",
    )

    # Keep original prices for logistic bounds before any transform.
    original_df = prophet_df.copy()

    # Apply log transform for moderate/volatile regimes.
    if rcfg.transform == "log":
        prophet_df = prophet_df.copy()
        prophet_df["y"] = np.log(prophet_df["y"].clip(lower=0.01))

    # Logistic growth bounds — set on DataFrame (not Prophet kwargs).
    if rcfg.growth == "logistic":
        # Compute bounds from original (non-log) price series.
        # compute_logistic_bounds expects high/low columns;
        # we use price y as a proxy for both.
        proxy_df = pd.DataFrame(
            {
                "high": original_df["y"],
                "low": original_df["y"],
            }
        )
        raw_cap, raw_floor = compute_logistic_bounds(proxy_df)
        if rcfg.transform == "log":
            cap = np.log(max(raw_cap, 0.01))
            floor = np.log(max(raw_floor, 0.01))
        else:
            cap, floor = raw_cap, raw_floor
        prophet_df = prophet_df.copy()
        prophet_df["cap"] = cap
        prophet_df["floor"] = floor

    model = Prophet(**prophet_kwargs)

    # Store regime metadata for _generate_forecast to use.
    model._regime_transform = rcfg.transform
    model._regime_growth = rcfg.growth

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
    regime: str = "moderate",
) -> pd.DataFrame:
    """Generate a price forecast for a given number of months.

    When the model was trained with a log-transform (moderate or
    volatile regimes), ``yhat``/``yhat_lower``/``yhat_upper`` are
    inverse-transformed via ``exp()`` after prediction.  For
    logistic growth, ``cap``/``floor`` columns are set on the
    future DataFrame before calling ``model.predict()``.

    If the model object carries ``_regime_transform`` /
    ``_regime_growth`` attributes (set by
    :func:`_train_prophet_model`), those take precedence over
    the *regime* parameter.

    Args:
        model: A fitted :class:`~prophet.Prophet` model.
        prophet_df: The training data in Prophet format.
            Used to determine the last historical date and, for
            logistic regimes, to derive cap/floor values.
        months: Number of months to forecast.
        regressors: Optional regressor DataFrame. For future
            dates, values are forward-filled from the last
            known observation.
        regime: Volatility regime — one of ``"stable"``,
            ``"moderate"``, ``"volatile"``.  Ignored when
            the model already carries regime metadata.
            Defaults to ``"moderate"`` for backward
            compatibility.

    Returns:
        DataFrame of **future-only** rows with columns ``ds``,
        ``yhat``, ``yhat_lower``, ``yhat_upper``.
    """
    # Prefer metadata baked into the model by _train_prophet_model.
    transform = getattr(model, "_regime_transform", None)
    growth = getattr(model, "_regime_growth", None)
    if transform is None or growth is None:
        # Fallback: derive from regime param.
        rcfg = get_regime_config(
            {"stable": 15.0, "moderate": 45.0, "volatile": 75.0}.get(
                regime, 45.0
            )
        )
        transform = rcfg.transform
        growth = rcfg.growth

    periods = int(months * 30)
    future = model.make_future_dataframe(
        periods=periods,
        freq="D",
    )

    # For logistic growth: set cap/floor on future DataFrame.
    if growth == "logistic":
        # Re-derive bounds from the original (non-log) training y.
        # prophet_df["y"] may be log-transformed; we need raw prices.
        # Use exp() to recover if transform was applied, else use as-is.
        if transform == "log":
            raw_y = np.exp(prophet_df["y"])
        else:
            raw_y = prophet_df["y"]
        proxy_df = pd.DataFrame({"high": raw_y, "low": raw_y})
        raw_cap, raw_floor = compute_logistic_bounds(proxy_df)
        if transform == "log":
            future["cap"] = np.log(max(raw_cap, 0.01))
            future["floor"] = np.log(max(raw_floor, 0.01))
        else:
            future["cap"] = raw_cap
            future["floor"] = raw_floor

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

    # Reverse log-transform: exp(yhat) → price space.
    # Cap log-space values at 20 (~exp(20) ≈ 485M) to prevent
    # float overflow on extreme Prophet extrapolations.
    if transform == "log":
        for col in ("yhat", "yhat_lower", "yhat_upper"):
            if col in result.columns:
                result[col] = np.exp(
                    result[col].clip(upper=20.0)
                )

    # Clamp negative predictions — stock prices cannot go below zero.
    # For log-transform regimes this is mathematically impossible
    # (exp is always positive), but we keep the clamp as a safety net
    # for linear/stable regimes with sharp recent declines.
    for col in ("yhat", "yhat_lower", "yhat_upper"):
        if col in result.columns:
            result[col] = result[col].clip(lower=0.01)

    _logger.debug("Forecast generated: %d future rows", len(result))
    return result
