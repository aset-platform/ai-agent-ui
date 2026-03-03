"""Dash-agnostic service modules for the AI Stock Analysis Dashboard.

Exports:
    :func:`run_full_refresh` — full stock data refresh pipeline.
    :class:`RefreshResult` — structured result from a refresh run.
"""

from dashboard.services.stock_refresh import RefreshResult, run_full_refresh

__all__ = ["run_full_refresh", "RefreshResult"]
