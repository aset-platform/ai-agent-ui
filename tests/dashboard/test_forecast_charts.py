"""Tests for forecast chart types (ASETPLTFRM-19).

Validates that:
- Confidence band traces are present in standard view.
- Multi-horizon overlay shows 3m/6m/9m traces.
- Decomposition subplots include trend and seasonality.
- Empty forecast returns a message figure.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from dashboard.callbacks.chart_builders2 import (
    _build_decomposition_fig,
    _build_forecast_fig,
    _build_multi_horizon_fig,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_prophet_df(n_days: int = 100) -> pd.DataFrame:
    """Create a mock prophet-format historical DataFrame.

    Args:
        n_days: Number of historical days.

    Returns:
        DataFrame with ``ds`` and ``y`` columns.
    """
    dates = pd.date_range(
        end=date.today(), periods=n_days, freq="B",
    )
    return pd.DataFrame(
        {
            "ds": dates,
            "y": range(100, 100 + n_days),
        }
    )


def _make_forecast_df(
    months: int = 9,
) -> pd.DataFrame:
    """Create a mock forecast DataFrame.

    Args:
        months: Forecast horizon in months.

    Returns:
        DataFrame with ``ds``, ``yhat``,
        ``yhat_lower``, ``yhat_upper`` columns.
    """
    start = pd.Timestamp(date.today())
    dates = pd.date_range(
        start=start,
        periods=months * 21,
        freq="B",
    )
    n = len(dates)
    base = list(range(200, 200 + n))
    return pd.DataFrame(
        {
            "ds": dates,
            "yhat": base,
            "yhat_lower": [v - 10 for v in base],
            "yhat_upper": [v + 10 for v in base],
        }
    )


def _make_summary() -> dict:
    """Create a mock forecast summary."""
    today = date.today()
    return {
        "ticker": "AAPL",
        "current_price": 199.0,
        "targets": {
            "3m": {
                "date": str(
                    today + timedelta(days=63),
                ),
                "price": 210.0,
                "pct_change": 5.5,
                "lower": 200.0,
                "upper": 220.0,
            },
            "6m": {
                "date": str(
                    today + timedelta(days=126),
                ),
                "price": 225.0,
                "pct_change": 13.1,
                "lower": 210.0,
                "upper": 240.0,
            },
            "9m": {
                "date": str(
                    today + timedelta(days=189),
                ),
                "price": 240.0,
                "pct_change": 20.6,
                "lower": 220.0,
                "upper": 260.0,
            },
        },
        "sentiment": "Bullish",
    }


# ------------------------------------------------------------------
# Tests: Standard forecast chart
# ------------------------------------------------------------------


class TestStandardForecastChart:
    """Tests for the standard forecast figure."""

    def test_confidence_band_traces_present(self):
        """Figure should have confidence interval traces."""
        prophet_df = _make_prophet_df()
        forecast_df = _make_forecast_df()
        summary = _make_summary()

        fig = _build_forecast_fig(
            prophet_df,
            forecast_df,
            "AAPL",
            199.0,
            summary,
        )

        trace_names = [t.name for t in fig.data]
        assert "80% Confidence Interval" in trace_names
        assert "Historical Price" in trace_names
        assert "Forecast" in trace_names

    def test_standard_chart_has_annotations(self):
        """Figure should have price-target annotations."""
        prophet_df = _make_prophet_df()
        forecast_df = _make_forecast_df()
        summary = _make_summary()

        fig = _build_forecast_fig(
            prophet_df,
            forecast_df,
            "AAPL",
            199.0,
            summary,
        )

        anno_texts = [
            a.text for a in fig.layout.annotations
        ]
        has_target = any(
            "3m:" in t or "6m:" in t or "9m:" in t
            for t in anno_texts
        )
        assert has_target


# ------------------------------------------------------------------
# Tests: Multi-horizon overlay
# ------------------------------------------------------------------


class TestMultiHorizonChart:
    """Tests for the multi-horizon overlay figure."""

    def test_multi_horizon_has_all_traces(self):
        """All three horizon forecast lines should appear."""
        prophet_df = _make_prophet_df()
        forecasts = {
            "3m": _make_forecast_df(3),
            "6m": _make_forecast_df(6),
            "9m": _make_forecast_df(9),
        }

        fig = _build_multi_horizon_fig(
            prophet_df, forecasts, "AAPL", 199.0,
        )

        trace_names = [t.name for t in fig.data]
        assert "3m Forecast" in trace_names
        assert "6m Forecast" in trace_names
        assert "9m Forecast" in trace_names
        assert "Historical Price" in trace_names

    def test_multi_horizon_missing_one_horizon(self):
        """Chart should still render with partial data."""
        prophet_df = _make_prophet_df()
        forecasts = {
            "3m": _make_forecast_df(3),
            "9m": _make_forecast_df(9),
        }

        fig = _build_multi_horizon_fig(
            prophet_df, forecasts, "AAPL", 199.0,
        )

        trace_names = [t.name for t in fig.data]
        assert "3m Forecast" in trace_names
        assert "9m Forecast" in trace_names
        assert "6m Forecast" not in trace_names


# ------------------------------------------------------------------
# Tests: Decomposition chart
# ------------------------------------------------------------------


class TestDecompositionChart:
    """Tests for the trend decomposition figure."""

    def test_decomposition_has_subplots(self):
        """Figure should have two subplot rows."""
        prophet_df = _make_prophet_df(200)
        forecast_df = _make_forecast_df()

        fig = _build_decomposition_fig(
            prophet_df, forecast_df, "AAPL",
        )

        # Subplots create xaxis and xaxis2
        assert hasattr(fig.layout, "xaxis")
        assert hasattr(fig.layout, "xaxis2")

    def test_decomposition_has_trend_trace(self):
        """Trend subplot should have forecast trace."""
        prophet_df = _make_prophet_df(200)
        forecast_df = _make_forecast_df()

        fig = _build_decomposition_fig(
            prophet_df, forecast_df, "AAPL",
        )

        trace_names = [t.name for t in fig.data]
        assert "Trend (Forecast)" in trace_names

    def test_decomposition_has_seasonality_trace(self):
        """Seasonality subplot should have a trace."""
        prophet_df = _make_prophet_df(200)
        forecast_df = _make_forecast_df()

        fig = _build_decomposition_fig(
            prophet_df, forecast_df, "AAPL",
        )

        trace_names = [t.name for t in fig.data]
        assert "Seasonality" in trace_names


# ------------------------------------------------------------------
# Tests: Empty state
# ------------------------------------------------------------------


class TestEmptyForecastState:
    """Tests for graceful empty forecast handling."""

    def test_empty_forecast_returns_message(self):
        """No forecast data should return message figure."""
        from dashboard.callbacks.chart_builders import (
            _empty_fig,
        )

        fig = _empty_fig("No forecast available")
        # Should be a valid figure (not None)
        assert fig is not None
        assert hasattr(fig, "data")
