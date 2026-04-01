"""Forecast tools for the Forecaster Agent.

Wraps existing forecast data in Iceberg with
read-only tools.  ``forecast_stock`` (the tool that
actually runs Prophet) lives in ``forecasting_tool.py``
and is reused as-is.
"""

from __future__ import annotations

import logging
from datetime import date

from langchain_core.tools import tool

from tools._stock_shared import _require_repo
from tools._ticker_linker import get_current_user

_logger = logging.getLogger(__name__)


@tool
def get_forecast_summary(ticker: str) -> str:
    """Get the latest forecast results for a ticker.

    Reads from Iceberg forecast_runs table — does NOT
    re-run Prophet.  Use ``forecast_stock`` to generate
    a new forecast if this returns stale data.

    Args:
        ticker: Stock ticker symbol (e.g. AAPL,
            RELIANCE.NS).

    Returns:
        Forecast targets (3m/6m/9m), confidence
        intervals, and accuracy metrics.

    Source: Iceberg forecast_runs (read-only).
    """
    repo = _require_repo()
    ticker = ticker.upper().strip()

    run = repo.get_latest_forecast_run(
        ticker, horizon_months=9,
    )
    if run is None:
        return (
            f"No forecast found for {ticker}. "
            "Run forecast_stock first."
        )

    run_date = run.get("run_date", "unknown")
    lines = [
        f"[Source: iceberg]",
        f"**Forecast Summary: {ticker}**",
        f"Last run: {run_date}\n",
    ]

    # Price targets
    lines.append(
        "| Horizon | Target | Change | "
        "Lower | Upper |"
    )
    lines.append(
        "|---------|--------|--------|"
        "-------|-------|"
    )
    for h in [3, 6, 9]:
        t_date = run.get(f"target_{h}m_date", "")
        t_price = run.get(f"target_{h}m_price")
        t_pct = run.get(f"target_{h}m_pct_change")
        t_lo = run.get(f"target_{h}m_lower")
        t_hi = run.get(f"target_{h}m_upper")
        if t_price is not None:
            lines.append(
                f"| {h}M ({t_date}) | "
                f"{t_price:.2f} | "
                f"{t_pct:+.1f}% | "
                f"{t_lo:.2f} | {t_hi:.2f} |"
            )

    # Accuracy
    mae = run.get("mae")
    rmse = run.get("rmse")
    mape = run.get("mape")
    if mae is not None:
        lines.append(
            f"\n**Model Accuracy** "
            f"(12-month backtest):"
        )
        lines.append(
            f"- MAE: {mae:.2f}"
        )
        lines.append(
            f"- RMSE: {rmse:.2f}"
        )
        lines.append(
            f"- MAPE: {mape:.1f}%"
        )

    return "\n".join(lines)


@tool
def get_portfolio_forecast(
    horizon: int = 9,
) -> str:
    """Aggregate forecasts for all portfolio holdings.

    Returns per-ticker predicted value and portfolio
    total predicted with expected return.

    Args:
        horizon: Forecast horizon in months (3, 6, 9).

    Source: Iceberg forecast_runs + portfolio.
    """
    user_id = get_current_user()
    if not user_id:
        return "No user context — cannot access portfolio."

    repo = _require_repo()
    holdings = repo.get_portfolio_holdings(user_id)

    if holdings.empty:
        return "No portfolio holdings found."

    lines = [
        f"[Source: iceberg]",
        f"**Portfolio Forecast ({horizon}M)**\n",
        "| Ticker | Qty | Current | "
        f"Predicted ({horizon}M) | Change |",
        "|--------|-----|---------|"
        "-------------|--------|",
    ]

    total_current = 0.0
    total_predicted = 0.0
    missing = []

    for _, h in holdings.iterrows():
        ticker = h["ticker"]
        qty = float(h["quantity"])

        # Current price
        ohlcv = repo.get_ohlcv(ticker)
        valid = (
            ohlcv.dropna(subset=["close"])
            if not ohlcv.empty else ohlcv
        )
        curr = (
            float(valid.iloc[-1]["close"])
            if not valid.empty else None
        )
        if curr is None:
            missing.append(ticker)
            continue

        curr_val = qty * curr
        total_current += curr_val

        # Forecast
        run = repo.get_latest_forecast_run(
            ticker, horizon_months=horizon,
        )
        if run is None:
            missing.append(ticker)
            pred_val = curr_val
            pct = 0
        else:
            key = f"target_{horizon}m_price"
            pred_price = run.get(key, curr)
            if pred_price is None:
                pred_price = curr
            pred_val = qty * pred_price
            pct = (
                (pred_price - curr) / curr * 100
                if curr else 0
            )

        total_predicted += pred_val
        lines.append(
            f"| {ticker} | {qty} | "
            f"{curr:.2f} | "
            f"{pred_val / qty:.2f} | "
            f"{pct:+.1f}% |"
        )

    total_pct = (
        (total_predicted - total_current)
        / total_current * 100
        if total_current else 0
    )
    lines.append(
        f"\n**Portfolio Total**: "
        f"Current={total_current:.2f} → "
        f"Predicted={total_predicted:.2f} "
        f"({total_pct:+.1f}%)"
    )

    if missing:
        lines.append(
            f"\n*Missing forecasts*: "
            f"{', '.join(missing)} — run "
            f"forecast_stock for these tickers."
        )

    return "\n".join(lines)
