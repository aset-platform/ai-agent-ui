"""HTML table builders for the AI Stock Analysis Dashboard admin callbacks.

Provides helpers that build Bootstrap table components for displaying user
records and audit log events in the admin section of the dashboard.

Example::

    from dashboard.callbacks.table_builders import _build_users_table, _build_audit_table
"""

import json
import logging
from typing import Any, Dict, List

import dash_bootstrap_components as dbc
from dash import html

# Module-level logger; prefixed with underscore to indicate internal use
_logger = logging.getLogger(__name__)


def _build_users_table(users: List[Dict[str, Any]]) -> Any:
    """Render a Bootstrap table of user records with action buttons.

    Each row displays name, email, role, status, timestamps, and two
    action buttons: Edit (opens the edit modal) and Deactivate/Reactivate
    (calls the API directly).

    Args:
        users: List of user dicts as returned by ``GET /users``.

    Returns:
        A :class:`~dash_bootstrap_components.Table`, or a plain
        :class:`~dash.html.P` element when *users* is empty.
    """
    if not users:
        return html.P("No user accounts found.", className="text-muted")

    header = html.Thead(
        html.Tr(
            [
                html.Th("Name"),
                html.Th("Email"),
                html.Th("Role"),
                html.Th("Status"),
                html.Th("Created"),
                html.Th("Last Login"),
                html.Th("Actions", className="text-end"),
            ]
        )
    )

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
                        color="danger"
                        if user.get("role") == "superuser"
                        else "primary",
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
                html.Td(created, style={"fontSize": "0.8rem", "color": "#6b7280"}),
                html.Td(last_login, style={"fontSize": "0.8rem", "color": "#6b7280"}),
                html.Td(
                    [
                        dbc.Button(
                            "Edit",
                            id={"type": "edit-user-btn", "index": user["user_id"]},
                            size="sm",
                            color="outline-primary",
                            className="me-1 py-0 px-2",
                            style={"fontSize": "0.75rem"},
                        ),
                        dbc.Button(
                            "Deactivate" if is_active else "Reactivate",
                            id={"type": "toggle-user-btn", "index": user["user_id"]},
                            size="sm",
                            color="outline-danger" if is_active else "outline-success",
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


def _build_audit_table(events: List[Dict[str, Any]]) -> Any:
    """Render a Bootstrap table of audit log events, newest-first.

    Args:
        events: List of audit event dicts from ``GET /admin/audit-log``.

    Returns:
        A :class:`~dash_bootstrap_components.Table`, or a plain
        :class:`~dash.html.P` when *events* is empty.
    """
    if not events:
        return html.P("No audit events found.", className="text-muted")

    header = html.Thead(
        html.Tr(
            [
                html.Th("When"),
                html.Th("Event"),
                html.Th("Actor"),
                html.Th("Target"),
                html.Th("Details"),
            ]
        )
    )

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
                        style={"fontSize": "0.78rem", "fontFamily": "monospace"},
                    ),
                    html.Td(
                        (ev.get("target_user_id") or "—")[:8] + "…",
                        style={"fontSize": "0.78rem", "fontFamily": "monospace"},
                    ),
                    html.Td(
                        metadata, style={"fontSize": "0.78rem", "color": "#6b7280"}
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
