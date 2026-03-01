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
from datetime import date

from langchain_core.tools import tool

import tools._forecast_shared as _sh
from tools._forecast_model import _prepare_data_for_prophet, _train_prophet_model, _generate_forecast
from tools._forecast_accuracy import _calculate_forecast_accuracy, _generate_forecast_summary
from tools._forecast_persist import _save_forecast
from tools._forecast_chart import _create_forecast_chart

# Module-level logger — must remain module-level for LangChain @tool compatibility
_logger = logging.getLogger(__name__)

# Re-export so tests can still monkeypatch via forecasting_tool._get_repo
_get_repo = _sh._get_repo


@tool
def forecast_stock(ticker: str, months: int = 9) -> str:
    """Forecast the stock price using Meta Prophet and generate a chart.

    Loads locally stored OHLCV data, trains a Prophet model with yearly
    and weekly seasonality and US market holidays, generates a price
    forecast for the requested horizon, evaluates accuracy via 12-month
    in-sample backtesting, and saves both the forecast (parquet) and an
    interactive Plotly chart.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``. Data must already be
            fetched via :func:`fetch_stock_data` before calling this tool.
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
    ticker = ticker.upper().strip()
    months = max(1, int(months))
    _logger.info("forecast_stock | ticker=%s | months=%d", ticker, months)
    sym = _sh._currency_symbol(_sh._load_currency(ticker))

    cached = _sh._load_cache(ticker, f"forecast_{months}m")
    if cached:
        _logger.info("Returning cached forecast for %s (%dm)", ticker, months)
        return cached

    try:
        df = _sh._load_parquet(ticker)
        if df is None:
            return (
                f"No local data found for '{ticker}'. "
                "Please run fetch_stock_data first."
            )

        prophet_df = _prepare_data_for_prophet(df)
        current_price = float(prophet_df["y"].iloc[-1])

        _logger.info("Training Prophet model for %s...", ticker)
        model = _train_prophet_model(prophet_df)

        forecast_df = _generate_forecast(model, prophet_df, months)
        accuracy = _calculate_forecast_accuracy(model, prophet_df)
        summary = _generate_forecast_summary(forecast_df, current_price, ticker, months)

        forecast_path = _save_forecast(forecast_df, ticker, months)
        chart_path = _create_forecast_chart(
            model, forecast_df, prophet_df, ticker, current_price, summary
        )

        try:
            repo = _sh._get_repo()
            if repo is not None:
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
                if "error" not in accuracy:
                    _run_dict["mae"] = accuracy.get("MAE")
                    _run_dict["rmse"] = accuracy.get("RMSE")
                    _run_dict["mape"] = accuracy.get("MAPE_pct")
                repo.insert_forecast_run(ticker, months, _run_dict)
                repo.insert_forecast_series(ticker, months, _run_date, forecast_df)
        except Exception as _e:
            _logger.warning("Iceberg forecast write failed for %s: %s", ticker, _e)

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

        if "error" in accuracy:
            acc_line = f"  Accuracy        : {accuracy['error']}"
        else:
            acc_line = (
                f"  MAE             : {sym}{accuracy['MAE']}\n"
                f"  RMSE            : {sym}{accuracy['RMSE']}\n"
                f"  MAPE            : {accuracy['MAPE_pct']:.1f}%"
            )

        report = (
            f"=== PRICE FORECAST: {ticker} ({months}-month horizon) ===\n\n"
            f"CURRENT PRICE     : {sym}{current_price:.2f}\n\n"
            f"PRICE TARGETS\n"
            + "\n".join(target_lines)
            + f"\n\nSENTIMENT         : {sentiment_emoji}\n\n"
            f"MODEL ACCURACY (last 12 months in-sample)\n"
            f"{acc_line}\n\n"
            f"FILES\n"
            f"  Forecast data   : {forecast_path}\n"
            f"  Chart           : {chart_path}\n"
        )

        _sh._save_cache(ticker, f"forecast_{months}m", report)
        _logger.info("forecast_stock complete for %s", ticker)
        return report

    except Exception as e:
        _logger.error("forecast_stock failed for %s: %s", ticker, e, exc_info=True)
        return f"Error forecasting '{ticker}': {e}"