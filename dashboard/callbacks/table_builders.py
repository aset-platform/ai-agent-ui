"""HTML table builders for the AI Stock Analysis Dashboard admin callbacks.

Provides helpers that build Bootstrap table components for displaying user
records and audit log events in the admin section of the dashboard.

Example::

    from dashboard.callbacks.table_builders import (
        _build_users_table, _build_audit_table,
    )
"""

import json
import logging
from typing import Any, Dict, List

import dash_bootstrap_components as dbc
from dash import html

from dashboard.callbacks.sort_helpers import (
    apply_sort_list,
    build_sortable_thead,
)

# Module-level logger; prefixed with underscore to indicate internal use
_logger = logging.getLogger(__name__)


_USERS_COL_DEFS = [
    {"key": "full_name", "label": "Name"},
    {"key": "email", "label": "Email"},
    {"key": "role", "label": "Role"},
    {"key": "is_active", "label": "Status"},
    {"key": "created_at", "label": "Created"},
    {"key": "last_login_at", "label": "Last Login"},
    {"key": "_actions", "label": "Actions"},
]


def _build_users_table(
    users: List[Dict[str, Any]],
    sort_state: Dict[str, Any] | None = None,
) -> Any:
    """Render a Bootstrap table of user records with action buttons.

    Args:
        users: List of user dicts as returned by ``GET /users``.
        sort_state: Column sort state dict (optional).

    Returns:
        A :class:`~dash_bootstrap_components.Table`, or a plain
        :class:`~dash.html.P` element when *users* is empty.
    """
    sort_state = sort_state or {
        "col": None,
        "dir": "none",
    }
    if not users:
        return html.P(
            "No user accounts found.",
            className="text-muted",
        )

    # Sort (skip _actions pseudo-column)
    users = apply_sort_list(users, sort_state)

    header = build_sortable_thead(_USERS_COL_DEFS, "users", sort_state)

    rows = []
    for user in users:
        is_active = user.get("is_active", True)
        created = (user.get("created_at") or "")[:10] or "—"
        last_login = (user.get("last_login_at") or "")[:10] or "—"

        row = html.Tr(
            [
                html.Td(user.get("full_name", "—")),
                html.Td(
                    user.get("email", "—"),
                    style={"fontSize": "0.85rem"},
                ),
                html.Td(
                    dbc.Badge(
                        user.get("role", "—"),
                        color=(
                            "danger"
                            if user.get("role") == "superuser"
                            else "primary"
                        ),
                        className="fw-normal",
                    )
                ),
                html.Td(
                    dbc.Badge(
                        "Active" if is_active else "Inactive",
                        color="success" if is_active else "secondary",
                        className="fw-normal",
                    )
                ),
                html.Td(
                    created, style={"fontSize": "0.8rem", "color": "#6b7280"}
                ),
                html.Td(
                    last_login,
                    style={"fontSize": "0.8rem", "color": "#6b7280"},
                ),
                html.Td(
                    [
                        dbc.Button(
                            "Edit",
                            id={
                                "type": "edit-user-btn",
                                "index": user["user_id"],
                            },
                            size="sm",
                            color="outline-primary",
                            className="me-1 py-0 px-2",
                            style={"fontSize": "0.75rem"},
                        ),
                        dbc.Button(
                            "Reset Pwd",
                            id={
                                "type": "reset-pw-btn",
                                "index": user["user_id"],
                            },
                            size="sm",
                            color="outline-warning",
                            className="me-1 py-0 px-2",
                            style={"fontSize": "0.75rem"},
                        ),
                        dbc.Button(
                            ("Deactivate" if is_active else "Reactivate"),
                            id={
                                "type": "toggle-user-btn",
                                "index": user["user_id"],
                            },
                            size="sm",
                            color=(
                                "outline-danger"
                                if is_active
                                else "outline-success"
                            ),
                            className="py-0 px-2",
                            style={"fontSize": "0.75rem"},
                        ),
                    ],
                    className="text-end",
                ),
            ]
        )
        rows.append(row)

    return dbc.Table(
        [header, html.Tbody(rows)],
        bordered=True,
        hover=True,
        responsive=True,
        className="table table-sm align-middle",
    )


_AUDIT_COL_DEFS = [
    {"key": "event_timestamp", "label": "When"},
    {"key": "event_type", "label": "Event"},
    {"key": "actor_user_id", "label": "Actor"},
    {"key": "target_user_id", "label": "Target"},
    {"key": "metadata", "label": "Details"},
]


def _build_audit_table(
    events: List[Dict[str, Any]],
    sort_state: Dict[str, Any] | None = None,
) -> Any:
    """Render a Bootstrap table of audit log events.

    Args:
        events: List of audit event dicts.
        sort_state: Column sort state dict (optional).

    Returns:
        A :class:`~dash_bootstrap_components.Table`, or a plain
        :class:`~dash.html.P` when *events* is empty.
    """
    sort_state = sort_state or {
        "col": None,
        "dir": "none",
    }
    if not events:
        return html.P(
            "No audit events found.",
            className="text-muted",
        )

    # Sort
    events = apply_sort_list(events, sort_state)

    header = build_sortable_thead(_AUDIT_COL_DEFS, "audit", sort_state)

    rows = []
    for ev in events:
        ts = (ev.get("event_timestamp") or "")[:19].replace("T", " ") or "—"
        metadata = ev.get("metadata") or ""
        if metadata and metadata.startswith("{"):
            try:
                meta_dict = json.loads(metadata)
                metadata = ", ".join(f"{k}: {v}" for k, v in meta_dict.items())
            except Exception:
                pass

        rows.append(
            html.Tr(
                [
                    html.Td(
                        ts,
                        style={
                            "fontSize": "0.78rem",
                            "color": "#6b7280",
                            "whiteSpace": "nowrap",
                        },
                    ),
                    html.Td(
                        dbc.Badge(
                            ev.get("event_type", "—"),
                            color="info",
                            className="fw-normal",
                            style={"fontSize": "0.72rem"},
                        )
                    ),
                    html.Td(
                        (ev.get("actor_user_id") or "—")[:8] + "…",
                        style={
                            "fontSize": "0.78rem",
                            "fontFamily": "monospace",
                        },
                    ),
                    html.Td(
                        (ev.get("target_user_id") or "—")[:8] + "…",
                        style={
                            "fontSize": "0.78rem",
                            "fontFamily": "monospace",
                        },
                    ),
                    html.Td(
                        metadata,
                        style={"fontSize": "0.78rem", "color": "#6b7280"},
                    ),
                ]
            )
        )

    return dbc.Table(
        [header, html.Tbody(rows)],
        bordered=True,
        hover=True,
        responsive=True,
        className="table table-sm",
    )
