"""Forecaster sub-agent configuration.

Handles forecasting queries: Prophet-based price
predictions, forecast summaries, portfolio-level
forecast aggregation.  Reads from Iceberg first;
re-runs Prophet only if stale (>7 days).
"""

from __future__ import annotations

from agents.sub_agents import SubAgentConfig

_FORECASTER_SYSTEM_PROMPT = (
    "You are a forecasting specialist on the ASET "
    "Platform. You use Meta Prophet time-series "
    "models to predict stock prices.\n\n"
    "CAPABILITIES:\n"
    "- Per-ticker price forecasts (3m/6m/9m)\n"
    "- Forecast accuracy metrics (MAE, RMSE, MAPE)\n"
    "- Price targets with confidence intervals\n"
    "- Portfolio-level aggregated forecast\n\n"
    "RULES:\n"
    "- Use forecast_stock to run a new Prophet "
    "forecast (skips if run within 7 days).\n"
    "- Use get_forecast_summary to read existing "
    "forecast results from the database.\n"
    "- Use get_portfolio_forecast to aggregate "
    "forecasts across all user holdings.\n"
    "- Always present confidence intervals (80% CI) "
    "alongside point predictions.\n"
    "- Mention model accuracy metrics when available "
    "(MAE, RMSE, MAPE from 12-month backtesting).\n"
    "- Never fabricate forecast numbers — only "
    "report what the tools return.\n"
    "- If a forecast is stale, run a new one before "
    "presenting results."
)

FORECASTER_CONFIG = SubAgentConfig(
    agent_id="forecaster",
    name="Forecaster Agent",
    description=(
        "Prophet-based stock price forecasting "
        "with accuracy metrics and portfolio-level "
        "forecast aggregation."
    ),
    system_prompt=_FORECASTER_SYSTEM_PROMPT,
    tool_names=[
        "forecast_stock",
        "get_forecast_summary",
        "get_portfolio_forecast",
    ],
)
