"""Plotly Dash web dashboard for the AI Stock Analysis project.

This package provides a four-page interactive dashboard (Home, Analysis,
Forecast, Compare) built with Plotly Dash and dash-bootstrap-components.
It reads data directly from local parquet files written by the backend
stock-data tools — no HTTP calls to the FastAPI backend are required.

Modules:
    app: Entry point — bootstraps the Dash app and registers callbacks.
    layouts: Page-layout factory functions and the global navigation bar.
    callbacks: All interactive callback definitions.
"""
