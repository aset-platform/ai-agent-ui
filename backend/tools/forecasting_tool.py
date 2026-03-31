"""Prophet-based price forecasting tool for the Stock Analysis Agent.

This module exposes the public :func:`forecast_stock` LangChain ``@tool``
function.  All heavy lifting is delegated to private sub-modules:

- :mod:`tools._forecast_shared` — constants, lazy Iceberg repo, cache helpers
- :mod:`tools._forecast_model` — data preparation, Prophet training, forecast
- :mod:`tools._forecast_accuracy` — MAE/RMSE/MAPE backtest, price targets
- :mod:`tools._forecast_persist` — parquet file persistence
- :mod:`tools._forecast_chart` — Plotly chart builder

**Prophet configuration:** yearly + weekly seasonality, US federal holidays,
80 % confidence interval.

Typical usage (via LangChain tool call)::

    from tools.forecasting_tool import forecast_stock

    result = forecast_stock.invoke({"ticker": "AAPL", "months": 9})
"""

import logging
from datetime import date, timedelta

import tools._forecast_shared as _sh
from langchain_core.tools import tool
from tools._forecast_accuracy import (
    _generate_forecast_summary,
)
from tools._forecast_model import (
    _generate_forecast,
    _prepare_data_for_prophet,
    _train_prophet_model,
)
from validation import validate_ticker

# Module-level logger — required for LangChain @tool
_logger = logging.getLogger(__name__)

# Re-export so tests can still monkeypatch via forecasting_tool.*
_get_repo = _sh._get_repo
_require_repo = _sh._require_repo


def _rebuild_forecast_report(
    run: dict,
    ticker: str,
    months: int,
    sym: str,
) -> str | None:
    """Rebuild a forecast report from a stored Iceberg run.

    Returns the formatted report string, or ``None`` if the
    run dict is missing required fields.
    """
    current_price = run.get("current_price_at_run")
    sentiment = run.get("sentiment")
    if current_price is None or sentiment is None:
        return None

    sentiment_emoji = {
        "Bullish": "🟢 BULLISH",
        "Bearish": "🔴 BEARISH",
        "Neutral": "🟡 NEUTRAL",
    }.get(sentiment, sentiment)

    target_lines = []
    for key in ["3m", "6m", "9m"]:
        price = run.get(f"target_{key}_price")
        pct = run.get(f"target_{key}_pct_change")
        lower = run.get(f"target_{key}_lower")
        upper = run.get(f"target_{key}_upper")
        if price is not None and pct is not None:
            sign = "+" if pct >= 0 else ""
            lo = f"{sym}{lower}" if lower else "?"
            hi = f"{sym}{upper}" if upper else "?"
            target_lines.append(
                f"  {key.upper()} Target  : "
                f"{sym}{price} ({sign}{pct:.1f}%) "
                f"[{lo} – {hi}]"
            )

    if not target_lines:
        return None

    mae = run.get("mae")
    rmse = run.get("rmse")
    mape = run.get("mape")
    if mae is not None and rmse is not None:
        acc_line = (
            f"  MAE             : {sym}{mae}\n"
            f"  RMSE            : {sym}{rmse}\n"
            f"  MAPE            : {mape:.1f}%"
        )
    else:
        acc_line = "  Accuracy        : N/A"

    rd = run.get("run_date", "")

    return (
        f"=== PRICE FORECAST: {ticker} "
        f"({months}-month horizon) ===\n"
        f"(cached from {rd})\n\n"
        f"CURRENT PRICE     : {sym}{current_price:.2f}\n\n"
        f"PRICE TARGETS\n"
        + "\n".join(target_lines)
        + f"\n\nSENTIMENT         : {sentiment_emoji}\n\n"
        f"MODEL ACCURACY (last 12 months in-sample)\n"
        f"{acc_line}\n"
    )


@tool
def forecast_stock(ticker: str, months: int = 9) -> str:
    """Forecast the stock price using Meta Prophet and generate a chart.

    **IMPORTANT**: OHLCV data must already exist in Iceberg before calling
    this tool.  Call ``fetch_stock_data`` for the ticker in a **prior step**
    and wait for it to complete.  Do NOT call both tools in the same step.

    Trains a Prophet model with yearly and weekly seasonality and US
    market holidays, generates a price forecast for the requested
    horizon, evaluates accuracy via 12-month in-sample backtesting,
    and saves both the forecast (parquet) and an interactive Plotly chart.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.
        months: Forecast horizon in months. Targets are shown at 3, 6, and
            9 months (whichever fall within the horizon). Defaults to ``9``.

    Returns:
        A formatted string report with price targets, confidence bounds,
        sentiment, model accuracy, and the chart file path. Returns an
        error string if data is unavailable or the model fails.

    Example:
        >>> result = forecast_stock.invoke({"ticker": "AAPL", "months": 9})
        >>> "AAPL" in result
        True
    """
    err = validate_ticker(ticker)
    if err:
        return f"Error: {err}"
    ticker = ticker.upper().strip()
    from tools._ticker_linker import auto_link_ticker

    auto_link_ticker(ticker)
    months = max(1, int(months))
    _logger.info(
        "forecast_stock | ticker=%s | months=%d",
        ticker,
        months,
    )
    sym = _sh._currency_symbol(_sh._load_currency(ticker))

    # 7-day cooldown: skip re-running Prophet if a forecast
    # was generated within the last 7 days.  Return the
    # existing forecast report rebuilt from Iceberg data.
    try:
        repo_check = _sh._get_repo()
        if repo_check is not None:
            latest_run = repo_check.get_latest_forecast_run(
                ticker,
                months,
            )
            if latest_run is not None:
                rd = latest_run.get("run_date")
                if rd is not None:
                    if hasattr(rd, "date"):
                        rd = rd.date()
                    cutoff = date.today() - timedelta(days=7)
                    if rd > cutoff:
                        report = _rebuild_forecast_report(
                            latest_run,
                            ticker,
                            months,
                            sym,
                        )
                        if report:
                            _logger.info(
                                "Forecast cooldown: %s %dm"
                                " — returning Iceberg "
                                "report (run %s)",
                                ticker,
                                months,
                                rd,
                            )
                            return report
    except Exception as exc:
        _logger.debug(
            "Cooldown check skipped for %s: %s",
            ticker,
            exc,
        )

    try:
        df = _sh._load_ohlcv(ticker)
        if df is None:
            return (
                f"No OHLCV data found for '{ticker}'. "
                "You MUST call fetch_stock_data for "
                "this ticker first, then call "
                "forecast_stock again in the next "
                "step."
            )

        prophet_df = _prepare_data_for_prophet(df)
        current_price = float(prophet_df["y"].iloc[-1])

        # Load regressors from Iceberg (VIX, index,
        # sentiment).  Falls back to live yfinance if
        # Iceberg tables are empty.
        regressors = _sh._load_regressors_from_iceberg(
            ticker,
            prophet_df,
        )

        _logger.info("Training Prophet model for %s...", ticker)
        model, train_df = _train_prophet_model(
            prophet_df,
            ticker=ticker,
            regressors=regressors,
        )

        forecast_df = _generate_forecast(
            model,
            prophet_df,
            months,
            regressors=regressors,
        )

        # XGBoost ensemble correction (Phase 3b).
        from config import get_settings as _gs

        if getattr(_gs(), "ensemble_enabled", False):
            from tools._forecast_ensemble import (
                ensemble_forecast,
            )

            _corrected = ensemble_forecast(
                model,
                train_df,
                prophet_df,
                forecast_df,
                ticker,
                regressors=regressors,
            )
            if _corrected is not None:
                forecast_df = _corrected

        summary = _generate_forecast_summary(
            forecast_df, current_price, ticker, months
        )

        # Read accuracy from last CV run in Iceberg
        # (background refresh computes this).
        repo = _sh._require_repo()
        _prev_run = repo.get_latest_forecast_run(
            ticker,
            months,
        )
        accuracy: dict = {}
        if _prev_run:
            _mae = _prev_run.get("mae")
            _rmse = _prev_run.get("rmse")
            _mape = _prev_run.get("mape")
            # Guard against NaN values from Iceberg
            import math

            if (
                _mae is not None
                and not math.isnan(_mae)
                and _rmse is not None
                and not math.isnan(_rmse)
            ):
                accuracy = {
                    "MAE": round(_mae, 2),
                    "RMSE": round(_rmse, 2),
                    "MAPE_pct": (
                        round(_mape, 1)
                        if _mape is not None
                        and not math.isnan(_mape)
                        else 0.0
                    ),
                }

        _run_date = date.today()
        _run_dict = {
            "run_date": _run_date,
            "sentiment": summary.get("sentiment"),
            "current_price_at_run": current_price,
        }
        for _m_key in ["3m", "6m", "9m"]:
            _t = summary.get("targets", {}).get(_m_key)
            if _t:
                _run_dict[f"target_{_m_key}_date"] = _t.get("date")
                _run_dict[f"target_{_m_key}_price"] = _t.get("price")
                _run_dict[f"target_{_m_key}_pct_change"] = _t.get("pct_change")
                _run_dict[f"target_{_m_key}_lower"] = _t.get("lower")
                _run_dict[f"target_{_m_key}_upper"] = _t.get("upper")
        # Carry forward accuracy from previous CV run.
        if accuracy:
            _run_dict["mae"] = accuracy.get("MAE")
            _run_dict["rmse"] = accuracy.get("RMSE")
            _run_dict["mape"] = accuracy.get("MAPE_pct")
        repo.insert_forecast_run(ticker, months, _run_dict)
        repo.insert_forecast_series(ticker, months, _run_date, forecast_df)

        sentiment_emoji = {
            "Bullish": "🟢 BULLISH",
            "Bearish": "🔴 BEARISH",
            "Neutral": "🟡 NEUTRAL",
        }.get(summary["sentiment"], summary["sentiment"])

        target_lines = []
        for key in ["3m", "6m", "9m"]:
            t = summary["targets"].get(key)
            if t:
                sign = "+" if t["pct_change"] >= 0 else ""
                target_lines.append(
                    f"  {key.upper()} Target  : {sym}{t['price']} "
                    f"({sign}{t['pct_change']:.1f}%) "
                    f"[{sym}{t['lower']} – {sym}{t['upper']}]"
                )

        if accuracy:
            acc_line = (
                f"  MAE             : {sym}"
                f"{accuracy['MAE']}\n"
                f"  RMSE            : {sym}"
                f"{accuracy['RMSE']}\n"
                f"  MAPE            : "
                f"{accuracy['MAPE_pct']:.1f}%"
            )
            acc_header = "MODEL ACCURACY (cross-validated)"
        else:
            acc_line = "  Pending — available after " "background refresh"
            acc_header = "MODEL ACCURACY"

        report = (
            f"=== PRICE FORECAST: {ticker} "
            f"({months}-month horizon) ===\n\n"
            f"CURRENT PRICE     : "
            f"{sym}{current_price:.2f}\n\n"
            f"PRICE TARGETS\n"
            + "\n".join(target_lines)
            + f"\n\nSENTIMENT         : "
            f"{sentiment_emoji}\n\n"
            f"{acc_header}\n"
            f"{acc_line}\n"
        )

        _logger.info("forecast_stock complete for %s", ticker)
        return report

    except Exception as e:
        _logger.error(
            "forecast_stock failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error forecasting '{ticker}': {e}"
