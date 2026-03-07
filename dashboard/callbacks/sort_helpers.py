"""Reusable sortable-column-header utilities for Dash tables.

Provides helpers to build clickable ``<thead>`` headers with sort
arrows, apply sorting to DataFrames or list-of-dicts, and register
pattern-matching callbacks that wire everything together.

Example::

    from dashboard.callbacks.sort_helpers import (
        build_sortable_thead,
        apply_sort,
        register_sort_callback,
    )

    cols = [{"key": "ticker", "label": "Ticker"}, ...]
    thead = build_sortable_thead(cols, "screener", sort_state)
    df = apply_sort(df, sort_state)
    register_sort_callback(app, "screener")
"""

import logging
from typing import Any, Dict, List

import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Input, Output, State, callback_context, html

_logger = logging.getLogger(__name__)

# Sort direction cycle: none -> asc -> desc -> none
_CYCLE = {"none": "asc", "asc": "desc", "desc": "none"}

SHARPE_TOOLTIP = (
    "Sharpe Ratio measures risk-adjusted return: "
    "(annualised return \u2212 risk-free rate) \u00f7 "
    "annualised volatility. Higher is better; "
    ">1 is good, >2 is very good."
)

RSI_TOOLTIP = (
    "RSI (Relative Strength Index) is a momentum"
    " oscillator (0\u2013100). > 70 = overbought"
    " (potential sell), < 30 = oversold"
    " (potential buy)."
)

MACD_TOOLTIP = (
    "MACD (Moving Average Convergence Divergence)"
    " tracks trend momentum. Bullish when MACD"
    " crosses above the signal line; bearish"
    " when it crosses below."
)

_TOOLTIP_TEXT = {
    "sharpe": SHARPE_TOOLTIP,
    "rsi": RSI_TOOLTIP,
    "macd": MACD_TOOLTIP,
}


def label_with_tooltip(
    label: str, uid: str, tooltip_key: str,
) -> List:
    """Return label span, info icon, and Bootstrap tooltip.

    Args:
        label: Column header text (e.g. ``"Sharpe"``).
        uid: Unique DOM id for the tooltip target.
        tooltip_key: Key into ``_TOOLTIP_TEXT``
            (``"sharpe"``, ``"rsi"``, ``"macd"``).

    Returns:
        List of ``[Span, Span(icon), dbc.Tooltip]``.
    """
    text = _TOOLTIP_TEXT.get(tooltip_key, "")
    return [
        html.Span(label),
        html.Span(
            "\u2139",
            id=uid,
            className="col-info-icon",
        ),
        dbc.Tooltip(
            text,
            target=uid,
            placement="top",
        ),
    ]


def sharpe_label_with_tooltip(label: str, uid: str) -> List:
    """Return label with Sharpe tooltip (compat wrapper).

    Args:
        label: Column header text.
        uid: Unique DOM id for the tooltip target.

    Returns:
        List of ``[Span, Span(icon), dbc.Tooltip]``.
    """
    return label_with_tooltip(label, uid, "sharpe")


def next_sort_state(
    current: Dict[str, Any], clicked_col: str
) -> Dict[str, Any]:
    """Cycle sort direction for *clicked_col*.

    If the user clicks a **new** column the direction resets to
    ``"asc"``.  Clicking the same column cycles through
    ``none -> asc -> desc -> none``.

    Args:
        current: ``{"col": <str|None>, "dir": <str>}``
        clicked_col: Column key that was clicked.

    Returns:
        New sort state dict.
    """
    if current.get("col") != clicked_col:
        return {"col": clicked_col, "dir": "asc"}
    next_dir = _CYCLE.get(current.get("dir", "none"), "asc")
    if next_dir == "none":
        return {"col": None, "dir": "none"}
    return {"col": clicked_col, "dir": next_dir}


def apply_sort(df: pd.DataFrame, sort_state: Dict[str, Any]) -> pd.DataFrame:
    """Sort *df* according to *sort_state*; no-op when unsorted.

    Args:
        df: DataFrame to sort.
        sort_state: ``{"col": <str|None>, "dir": <str>}``.

    Returns:
        Sorted (or unchanged) DataFrame, index reset.
    """
    col = sort_state.get("col")
    direction = sort_state.get("dir", "none")
    if not col or direction == "none":
        return df
    if col not in df.columns:
        return df
    ascending = direction == "asc"
    return df.sort_values(
        col, ascending=ascending, na_position="last"
    ).reset_index(drop=True)


def apply_sort_list(
    items: List[Dict[str, Any]],
    sort_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Sort a list of dicts by *sort_state*; no-op when unsorted.

    Args:
        items: List of row dicts.
        sort_state: ``{"col": <str|None>, "dir": <str>}``.

    Returns:
        Sorted (or unchanged) list.
    """
    col = sort_state.get("col")
    direction = sort_state.get("dir", "none")
    if not col or direction == "none":
        return items
    reverse = direction == "desc"

    def _key(row):
        val = row.get(col)
        if val is None:
            return (1, "")
        return (0, val)

    try:
        return sorted(items, key=_key, reverse=reverse)
    except TypeError:
        return items


def build_sortable_thead(
    columns: List[Dict[str, str]],
    table_id: str,
    sort_state: Dict[str, Any],
) -> html.Thead:
    """Build a ``<thead>`` with clickable sort buttons in each ``<th>``.

    Each header cell contains an ``html.Button`` whose ID uses Dash
    pattern-matching: ``{"type": "sort-{table_id}", "col": key}``.

    Args:
        columns: ``[{"key": "ticker", "label": "Ticker"}, ...]``
        table_id: Identifier prefix (e.g. ``"screener"``).
        sort_state: Current ``{"col": ..., "dir": ...}``.

    Returns:
        ``html.Thead`` component.
    """
    active_col = sort_state.get("col")
    active_dir = sort_state.get("dir", "none")

    cells = []
    for col_def in columns:
        key = col_def["key"]
        label = col_def["label"]
        is_active = key == active_col and active_dir != "none"

        if is_active:
            arrow = "\u25b2" if active_dir == "asc" else "\u25bc"
        else:
            arrow = "\u21c5"

        arrow_cls = "sort-arrow"
        if is_active:
            arrow_cls += " sort-active"

        btn_cls = "sort-header-btn"
        if is_active:
            btn_cls += " sort-active"

        # Build label children — with optional tooltip
        tip_key = col_def.get("tooltip")
        if tip_key and tip_key in _TOOLTIP_TEXT:
            tip_id = f"{table_id}-{key}-tip"
            label_children = label_with_tooltip(
                label, tip_id, tip_key,
            )
        else:
            label_children = [html.Span(label)]

        btn = html.Button(
            label_children + [html.Span(arrow, className=arrow_cls)],
            id={
                "type": f"sort-{table_id}",
                "col": key,
            },
            className=btn_cls,
            n_clicks=0,
        )
        cells.append(html.Th(btn))

    return html.Thead(html.Tr(cells))


def register_sort_callback(app, table_id: str) -> None:
    """Register a pattern-matching callback that updates the sort store.

    Listens to clicks on ``{"type": "sort-{table_id}", "col": ALL}``
    buttons and writes the new state to ``{table_id}-sort-store``.

    Args:
        app: The Dash application instance.
        table_id: Table identifier (e.g. ``"screener"``).
    """

    @app.callback(
        Output(f"{table_id}-sort-store", "data"),
        Input(
            {"type": f"sort-{table_id}", "col": ALL},
            "n_clicks",
        ),
        State(f"{table_id}-sort-store", "data"),
        prevent_initial_call=True,
    )
    def _update_sort(n_clicks_list, current_state):
        """Update sort state on header click."""
        triggered = callback_context.triggered
        if not triggered:
            return current_state or {
                "col": None,
                "dir": "none",
            }
        # Find which button was clicked
        prop_id = triggered[0]["prop_id"]
        # prop_id looks like:
        # '{"col":"ticker","type":"sort-screener"}.n_clicks'
        import json as _json  # noqa: PLC0415

        id_str = prop_id.rsplit(".", 1)[0]
        try:
            id_dict = _json.loads(id_str)
            clicked_col = id_dict["col"]
        except (ValueError, KeyError):
            return current_state or {
                "col": None,
                "dir": "none",
            }

        return next_sort_state(
            current_state or {"col": None, "dir": "none"},
            clicked_col,
        )
