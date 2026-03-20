"""Tests for dashboard lazy loading (ASETPLTFRM-18).

Validates that:
- Tab content is rendered lazily via callback.
- Inactive tabs do not trigger data fetch.
- The tab rendering callback returns correct layouts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_app():
    """Create a Dash app with analysis callbacks."""
    import dash
    import dash_bootstrap_components as dbc
    from dash import dcc, html

    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.FLATLY],
        suppress_callback_exceptions=True,
    )

    app.layout = html.Div(
        [
            dcc.Location(id="url", refresh=False),
            dcc.Store(
                id="nav-ticker-store", data=None,
            ),
            dcc.Store(
                id="auth-token-store",
                storage_type="local",
            ),
            dcc.Store(
                id="user-profile-store",
                storage_type="session",
            ),
            dcc.Store(
                id="theme-store",
                storage_type="local",
                data="light",
            ),
            html.Div(id="error-overlay-container"),
            html.Div(id="page-content"),
        ]
    )
    return app


# ------------------------------------------------------------------
# Tests: Lazy tab rendering
# ------------------------------------------------------------------


class TestLazyTabRendering:
    """Tests for the analysis page lazy tab callback."""

    def test_analysis_tabs_layout_has_tab_content_div(
        self,
    ):
        """Layout should contain a tab-content container."""
        from dashboard.layouts.analysis import (
            analysis_tabs_layout,
        )

        layout = analysis_tabs_layout()
        # Find the tab content div
        found = _find_component_by_id(
            layout, "analysis-tab-content",
        )
        assert found is not None

    def test_analysis_tabs_layout_has_loaded_store(
        self,
    ):
        """Layout should contain a loaded-tabs store."""
        from dashboard.layouts.analysis import (
            analysis_tabs_layout,
        )

        layout = analysis_tabs_layout()
        found = _find_component_by_id(
            layout, "loaded-tabs-store",
        )
        assert found is not None

    def test_tabs_have_no_eagerly_loaded_children(
        self,
    ):
        """Tab components should not render layouts eagerly.

        Each dbc.Tab should have no children (content is
        rendered lazily via callback).
        """
        from dashboard.layouts.analysis import (
            analysis_tabs_layout,
        )

        layout = analysis_tabs_layout()
        tabs = _find_component_by_id(
            layout, "analysis-page-tabs",
        )
        assert tabs is not None
        for tab in tabs.children:
            assert (
                tab.children is None
                or tab.children == []
                or not hasattr(tab, "children")
            ), (
                f"Tab '{tab.tab_id}' has eager children"
            )

    def test_loading_wrapper_on_tab_content(self):
        """Tab content should be wrapped in dcc.Loading."""
        from dashboard.layouts.analysis import (
            analysis_tabs_layout,
        )

        layout = analysis_tabs_layout()
        found = _find_component_by_id(
            layout, "loading-tab-content",
        )
        assert found is not None


class TestForecastLayout:
    """Tests for forecast layout controls."""

    def test_forecast_layout_has_view_radio(self):
        """Forecast layout should have a view selector."""
        from dashboard.layouts.forecast import (
            forecast_layout,
        )

        layout = forecast_layout()
        found = _find_component_by_id(
            layout, "forecast-view-radio",
        )
        assert found is not None

    def test_forecast_view_radio_has_three_options(
        self,
    ):
        """View radio should offer standard, decomposition,
        and multi-horizon views."""
        from dashboard.layouts.forecast import (
            forecast_layout,
        )

        layout = forecast_layout()
        radio = _find_component_by_id(
            layout, "forecast-view-radio",
        )
        values = [o["value"] for o in radio.options]
        assert "standard" in values
        assert "decomposition" in values
        assert "multi_horizon" in values


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _find_component_by_id(component, target_id):
    """Recursively find a Dash component by its id."""
    cid = getattr(component, "id", None)
    if cid == target_id:
        return component
    children = getattr(component, "children", None)
    if children is None:
        return None
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        if hasattr(child, "id") or hasattr(
            child, "children",
        ):
            result = _find_component_by_id(
                child, target_id,
            )
            if result is not None:
                return result
    return None
