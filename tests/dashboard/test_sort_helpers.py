"""Tests for dashboard.callbacks.sort_helpers module."""

import dash_bootstrap_components as dbc
import pandas as pd
import pytest
from dash import html

from dashboard.callbacks.sort_helpers import (
    RSI_TOOLTIP,
    MACD_TOOLTIP,
    apply_sort,
    apply_sort_list,
    build_sortable_thead,
    label_with_tooltip,
    next_sort_state,
    sharpe_label_with_tooltip,
)

# ── next_sort_state ────────────────────────────────────────


class TestNextSortState:
    """Verify the three-step cycle: none -> asc -> desc -> none."""

    def test_new_column_starts_asc(self):
        """Clicking a new column resets direction to asc."""
        state = {"col": None, "dir": "none"}
        result = next_sort_state(state, "ticker")
        assert result == {"col": "ticker", "dir": "asc"}

    def test_cycle_asc_to_desc(self):
        """Second click on same column goes asc -> desc."""
        state = {"col": "ticker", "dir": "asc"}
        result = next_sort_state(state, "ticker")
        assert result == {"col": "ticker", "dir": "desc"}

    def test_cycle_desc_to_none(self):
        """Third click resets to unsorted."""
        state = {"col": "ticker", "dir": "desc"}
        result = next_sort_state(state, "ticker")
        assert result == {"col": None, "dir": "none"}

    def test_switch_column_resets_to_asc(self):
        """Clicking a different column resets to asc."""
        state = {"col": "ticker", "dir": "desc"}
        result = next_sort_state(state, "price")
        assert result == {"col": "price", "dir": "asc"}


# ── apply_sort (DataFrame) ────────────────────────────────


class TestApplySort:
    """Verify DataFrame sorting by sort_state."""

    @pytest.fixture()
    def sample_df(self):
        """Return a small test DataFrame."""
        return pd.DataFrame(
            {
                "ticker": ["AAPL", "GOOG", "MSFT"],
                "price": [150.0, 100.0, 200.0],
            }
        )

    def test_sort_asc(self, sample_df):
        """Ascending sort by price."""
        state = {"col": "price", "dir": "asc"}
        result = apply_sort(sample_df, state)
        assert list(result["price"]) == [
            100.0,
            150.0,
            200.0,
        ]

    def test_sort_desc(self, sample_df):
        """Descending sort by price."""
        state = {"col": "price", "dir": "desc"}
        result = apply_sort(sample_df, state)
        assert list(result["price"]) == [
            200.0,
            150.0,
            100.0,
        ]

    def test_sort_none_is_noop(self, sample_df):
        """No-op when direction is none."""
        state = {"col": None, "dir": "none"}
        result = apply_sort(sample_df, state)
        assert list(result["ticker"]) == [
            "AAPL",
            "GOOG",
            "MSFT",
        ]

    def test_sort_missing_col_is_noop(self, sample_df):
        """No-op when column doesn't exist."""
        state = {"col": "nonexistent", "dir": "asc"}
        result = apply_sort(sample_df, state)
        assert len(result) == 3


# ── apply_sort_list ────────────────────────────────────────


class TestApplySortList:
    """Verify list-of-dicts sorting."""

    def test_sort_list_asc(self):
        """Ascending sort on list of dicts."""
        items = [
            {"name": "Charlie"},
            {"name": "Alice"},
            {"name": "Bob"},
        ]
        state = {"col": "name", "dir": "asc"}
        result = apply_sort_list(items, state)
        assert [r["name"] for r in result] == [
            "Alice",
            "Bob",
            "Charlie",
        ]

    def test_sort_list_desc(self):
        """Descending sort on list of dicts."""
        items = [
            {"name": "Alice"},
            {"name": "Charlie"},
            {"name": "Bob"},
        ]
        state = {"col": "name", "dir": "desc"}
        result = apply_sort_list(items, state)
        assert [r["name"] for r in result] == [
            "Charlie",
            "Bob",
            "Alice",
        ]

    def test_sort_list_none_is_noop(self):
        """No-op when direction is none."""
        items = [{"x": 3}, {"x": 1}, {"x": 2}]
        state = {"col": None, "dir": "none"}
        result = apply_sort_list(items, state)
        assert [r["x"] for r in result] == [3, 1, 2]


# ── build_sortable_thead ───────────────────────────────────


class TestBuildSortableThead:
    """Verify thead structure and button IDs."""

    def test_structure(self):
        """Thead has correct number of th elements."""
        cols = [
            {"key": "ticker", "label": "Ticker"},
            {"key": "price", "label": "Price"},
        ]
        state = {"col": None, "dir": "none"}
        thead = build_sortable_thead(cols, "test", state)
        # thead.children is the Tr
        tr = thead.children
        assert len(tr.children) == 2

    def test_button_ids(self):
        """Buttons have pattern-matching IDs."""
        cols = [
            {"key": "ticker", "label": "Ticker"},
            {"key": "price", "label": "Price"},
        ]
        state = {"col": None, "dir": "none"}
        thead = build_sortable_thead(cols, "tbl", state)
        tr = thead.children
        btn0 = tr.children[0].children  # Button
        btn1 = tr.children[1].children
        assert btn0.id == {
            "type": "sort-tbl",
            "col": "ticker",
        }
        assert btn1.id == {
            "type": "sort-tbl",
            "col": "price",
        }

    def test_active_arrow(self):
        """Active column shows sort-active class."""
        cols = [
            {"key": "ticker", "label": "Ticker"},
            {"key": "price", "label": "Price"},
        ]
        state = {"col": "price", "dir": "asc"}
        thead = build_sortable_thead(cols, "tbl", state)
        tr = thead.children
        btn_price = tr.children[1].children
        assert "sort-active" in btn_price.className

    def test_thead_with_tooltip(self):
        """Column with tooltip='sharpe' gets info icon."""
        cols = [
            {"key": "ticker", "label": "Ticker"},
            {
                "key": "sharpe_ratio",
                "label": "Sharpe",
                "tooltip": "sharpe",
            },
        ]
        state = {"col": None, "dir": "none"}
        thead = build_sortable_thead(cols, "tbl", state)
        tr = thead.children
        btn = tr.children[1].children  # Button
        # Button children: [Span(label), Span(icon),
        #   Tooltip, Span(arrow)]
        icon_spans = [
            c
            for c in btn.children
            if isinstance(c, html.Span)
            and getattr(c, "className", "") == "col-info-icon"
        ]
        assert len(icon_spans) == 1

    def test_thead_without_tooltip(self):
        """Column without tooltip key has no icon."""
        cols = [
            {"key": "ticker", "label": "Ticker"},
        ]
        state = {"col": None, "dir": "none"}
        thead = build_sortable_thead(cols, "tbl", state)
        tr = thead.children
        btn = tr.children[0].children
        icon_spans = [
            c
            for c in btn.children
            if isinstance(c, html.Span)
            and getattr(c, "className", "") == "col-info-icon"
        ]
        assert len(icon_spans) == 0


# ── sharpe_label_with_tooltip ─────────────────────────────


class TestSharpeLabelWithTooltip:
    """Verify sharpe tooltip helper structure."""

    def test_returns_three_elements(self):
        """Helper returns [Span, Span(icon), Tooltip]."""
        result = sharpe_label_with_tooltip("Sharpe", "uid")
        assert len(result) == 3
        assert isinstance(result[0], html.Span)
        assert isinstance(result[1], html.Span)
        assert isinstance(result[2], dbc.Tooltip)
        assert result[1].className == "col-info-icon"
        assert result[2].target == "uid"


# ── label_with_tooltip (generic) ────────────────────────────


class TestLabelWithTooltip:
    """Verify generic tooltip helper for RSI/MACD/Sharpe."""

    def test_rsi_tooltip_text(self):
        """RSI key produces RSI tooltip text."""
        result = label_with_tooltip("RSI", "rsi-id", "rsi")
        assert len(result) == 3
        assert isinstance(result[2], dbc.Tooltip)
        assert result[2].children == RSI_TOOLTIP
        assert result[2].target == "rsi-id"

    def test_macd_tooltip_text(self):
        """MACD key produces MACD tooltip text."""
        result = label_with_tooltip("MACD", "m-id", "macd")
        assert result[2].children == MACD_TOOLTIP

    def test_unknown_key_gives_empty_text(self):
        """Unknown tooltip key produces empty text."""
        result = label_with_tooltip("X", "x-id", "unknown")
        assert result[2].children == ""


class TestBuildSortableTheadRsiMacd:
    """Verify RSI/MACD tooltip columns in thead."""

    def test_rsi_tooltip_in_thead(self):
        """Column with tooltip='rsi' gets info icon."""
        cols = [
            {"key": "rsi_14", "label": "RSI", "tooltip": "rsi"},
        ]
        state = {"col": None, "dir": "none"}
        thead = build_sortable_thead(cols, "t", state)
        btn = thead.children.children[0].children
        icons = [
            c
            for c in btn.children
            if isinstance(c, html.Span)
            and getattr(c, "className", "") == "col-info-icon"
        ]
        assert len(icons) == 1

    def test_macd_tooltip_in_thead(self):
        """Column with tooltip='macd' gets info icon."""
        cols = [
            {
                "key": "macd_signal_text",
                "label": "MACD",
                "tooltip": "macd",
            },
        ]
        state = {"col": None, "dir": "none"}
        thead = build_sortable_thead(cols, "t", state)
        btn = thead.children.children[0].children
        icons = [
            c
            for c in btn.children
            if isinstance(c, html.Span)
            and getattr(c, "className", "") == "col-info-icon"
        ]
        assert len(icons) == 1

    def test_no_duplicate_ids_for_same_tooltip_key(self):
        """Two columns with tooltip='rsi' get unique IDs."""
        cols = [
            {
                "key": "rsi_14",
                "label": "RSI (14)",
                "tooltip": "rsi",
            },
            {
                "key": "rsi_signal",
                "label": "RSI Signal",
                "tooltip": "rsi",
            },
        ]
        state = {"col": None, "dir": "none"}
        thead = build_sortable_thead(cols, "scr", state)
        tr = thead.children
        ids = []
        for th in tr.children:
            btn = th.children
            for c in btn.children:
                if (
                    isinstance(c, html.Span)
                    and getattr(c, "className", "")
                    == "col-info-icon"
                ):
                    ids.append(c.id)
        assert len(ids) == 2
        assert ids[0] != ids[1]
        assert ids[0] == "scr-rsi_14-tip"
        assert ids[1] == "scr-rsi_signal-tip"
