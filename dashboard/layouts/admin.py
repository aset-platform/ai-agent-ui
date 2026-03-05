"""Admin / User Management page layout for the AI Stock Analysis Dashboard.

Provides :func:`admin_users_layout`, which builds the admin page containing
a user management table with Add / Edit / Deactivate controls, an audit log
viewer, the user add/edit modal, and supporting hidden stores.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html


def admin_users_layout() -> html.Div:
    """Build the Admin / User Management page layout.

    Displays two tabs: a user management table with Add / Edit / Deactivate
    controls, and an audit log viewer.  Only rendered for superusers — the
    ``display_page`` callback in ``app.py`` enforces the role guard before
    calling this function.

    Returns:
        :class:`~dash.html.Div` representing the full admin page.
    """
    return html.Div(
        [
            # ── Tabs: Users | Audit Log ───────────────────────────────────────
            dbc.Tabs(
                id="admin-tabs",
                active_tab="users-tab",
                children=[
                    # ── Tab 1: Users ──────────────────────────────────────────
                    dbc.Tab(
                        label="Users",
                        tab_id="users-tab",
                        children=[
                            html.Div(
                                className="mt-3",
                                children=[
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                html.H5(
                                                    "All Accounts",
                                                    className="text-muted my-auto",
                                                ),
                                            ),
                                            dbc.Col(
                                                dbc.Button(
                                                    "+ Add User",
                                                    id="add-user-btn",
                                                    color="primary",
                                                    size="sm",
                                                    className="float-end",
                                                ),
                                                className="text-end",
                                            ),
                                        ],
                                        className="mb-3 align-items-center",
                                    ),
                                    # Status message from save/delete operations
                                    html.Div(
                                        id="users-action-status",
                                        className="mb-3",
                                    ),
                                    # Search filter
                                    dbc.Input(
                                        id="users-search",
                                        placeholder="Search by name, email or role…",
                                        debounce=True,
                                        size="sm",
                                        className="mb-3",
                                    ),
                                    dcc.Loading(
                                        id="loading-users",
                                        type="circle",
                                        color="#4f46e5",
                                        children=html.Div(
                                            id="users-table-container"
                                        ),
                                    ),
                                    # Users pagination row
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                html.Small(
                                                    id="users-count-text",
                                                    className="text-muted",
                                                ),
                                                width="auto",
                                                className="my-auto",
                                            ),
                                            dbc.Col(
                                                dbc.Pagination(
                                                    id="users-pagination",
                                                    max_value=1,
                                                    active_page=1,
                                                    fully_expanded=False,
                                                    size="sm",
                                                    className="justify-content-end mb-0",
                                                ),
                                                className="d-flex justify-content-end my-auto",
                                            ),
                                            dbc.Col(
                                                dbc.Select(
                                                    id="users-page-size",
                                                    options=[
                                                        {
                                                            "label": "10 / page",
                                                            "value": "10",
                                                        },
                                                        {
                                                            "label": "25 / page",
                                                            "value": "25",
                                                        },
                                                        {
                                                            "label": "50 / page",
                                                            "value": "50",
                                                        },
                                                        {
                                                            "label": "100 / page",
                                                            "value": "100",
                                                        },
                                                    ],
                                                    value="10",
                                                    size="sm",
                                                    style={"width": "120px"},
                                                ),
                                                width="auto",
                                                className="my-auto",
                                            ),
                                        ],
                                        className="mt-2 align-items-center",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    # ── Tab 2: Audit Log ──────────────────────────────────────
                    dbc.Tab(
                        label="Audit Log",
                        tab_id="audit-tab",
                        children=[
                            html.Div(
                                className="mt-3",
                                children=[
                                    html.H5(
                                        "Audit Log",
                                        className="text-muted mb-3",
                                    ),
                                    # Search filter
                                    dbc.Input(
                                        id="audit-search",
                                        placeholder="Search by event type, actor ID or details…",
                                        debounce=True,
                                        size="sm",
                                        className="mb-3",
                                    ),
                                    dcc.Loading(
                                        id="loading-audit",
                                        type="circle",
                                        color="#4f46e5",
                                        children=html.Div(
                                            id="audit-log-container"
                                        ),
                                    ),
                                    # Audit pagination row
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                html.Small(
                                                    id="audit-count-text",
                                                    className="text-muted",
                                                ),
                                                width="auto",
                                                className="my-auto",
                                            ),
                                            dbc.Col(
                                                dbc.Pagination(
                                                    id="audit-pagination",
                                                    max_value=1,
                                                    active_page=1,
                                                    fully_expanded=False,
                                                    size="sm",
                                                    className="justify-content-end mb-0",
                                                ),
                                                className="d-flex justify-content-end my-auto",
                                            ),
                                            dbc.Col(
                                                dbc.Select(
                                                    id="audit-page-size",
                                                    options=[
                                                        {
                                                            "label": "10 / page",
                                                            "value": "10",
                                                        },
                                                        {
                                                            "label": "25 / page",
                                                            "value": "25",
                                                        },
                                                        {
                                                            "label": "50 / page",
                                                            "value": "50",
                                                        },
                                                        {
                                                            "label": "100 / page",
                                                            "value": "100",
                                                        },
                                                    ],
                                                    value="10",
                                                    size="sm",
                                                    style={"width": "120px"},
                                                ),
                                                width="auto",
                                                className="my-auto",
                                            ),
                                        ],
                                        className="mt-2 align-items-center",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            # ── User add / edit modal ─────────────────────────────────────────
            dbc.Modal(
                id="user-modal",
                is_open=False,
                backdrop="static",
                children=[
                    dbc.ModalHeader(
                        dbc.ModalTitle(id="user-modal-title"),
                        close_button=False,
                    ),
                    dbc.ModalBody(
                        [
                            html.Div(
                                id="modal-error",
                                className="text-danger small mb-2",
                            ),
                            dbc.Row(
                                [
                                    dbc.Col(
                                        [
                                            dbc.Label("Full Name"),
                                            dbc.Input(
                                                id="modal-full-name",
                                                type="text",
                                                placeholder="Jane Doe",
                                            ),
                                        ]
                                    ),
                                ],
                                className="mb-3",
                            ),
                            dbc.Row(
                                [
                                    dbc.Col(
                                        [
                                            dbc.Label("Email"),
                                            dbc.Input(
                                                id="modal-email",
                                                type="email",
                                                placeholder="jane@example.com",
                                            ),
                                        ]
                                    ),
                                ],
                                className="mb-3",
                            ),
                            # Password row — visible only when adding a new user
                            html.Div(
                                id="modal-password-row",
                                children=[
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Password"),
                                                    dbc.Input(
                                                        id="modal-password",
                                                        type="password",
                                                        placeholder="Min 8 chars, at least one digit",
                                                    ),
                                                ]
                                            ),
                                        ],
                                        className="mb-3",
                                    ),
                                ],
                            ),
                            dbc.Row(
                                [
                                    dbc.Col(
                                        [
                                            dbc.Label("Role"),
                                            dbc.Select(
                                                id="modal-role",
                                                options=[
                                                    {
                                                        "label": "General User",
                                                        "value": "general",
                                                    },
                                                    {
                                                        "label": "Superuser",
                                                        "value": "superuser",
                                                    },
                                                ],
                                                value="general",
                                            ),
                                        ]
                                    ),
                                ],
                                className="mb-3",
                            ),
                            # Avatar upload (optional)
                            dbc.Row(
                                [
                                    dbc.Col(
                                        [
                                            dbc.Label("Avatar (optional)"),
                                            dcc.Upload(
                                                id="admin-user-avatar-upload",
                                                children=html.Div(
                                                    [
                                                        "Drag & drop or ",
                                                        html.A("select image"),
                                                    ]
                                                ),
                                                accept="image/jpeg,image/png,image/gif,image/webp",
                                                multiple=False,
                                                style={
                                                    "width": "100%",
                                                    "height": "50px",
                                                    "lineHeight": "50px",
                                                    "borderWidth": "1px",
                                                    "borderStyle": "dashed",
                                                    "borderRadius": "5px",
                                                    "textAlign": "center",
                                                    "cursor": "pointer",
                                                },
                                            ),
                                            html.Div(
                                                id="admin-user-avatar-preview",
                                                className="mt-2",
                                            ),
                                        ]
                                    ),
                                ],
                                className="mb-3",
                            ),
                            # Page permissions — visible only when editing a non-superuser
                            html.Div(
                                id="user-permissions-section",
                                children=[
                                    dbc.Label(
                                        "Page Access (for non-superuser roles)"
                                    ),
                                    dbc.Checklist(
                                        id="user-permissions-checklist",
                                        options=[
                                            {
                                                "label": "Insights",
                                                "value": "insights",
                                            },
                                            {
                                                "label": "Admin",
                                                "value": "admin",
                                            },
                                        ],
                                        value=[],
                                        inline=True,
                                    ),
                                ],
                                style={"display": "none"},
                                className="mb-3",
                            ),
                            # Active toggle — visible only when editing an existing user
                            html.Div(
                                id="modal-active-row",
                                children=[
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Checklist(
                                                        id="modal-is-active",
                                                        options=[
                                                            {
                                                                "label": "Active account",
                                                                "value": "active",
                                                            }
                                                        ],
                                                        value=["active"],
                                                        switch=True,
                                                    ),
                                                ]
                                            ),
                                        ],
                                        className="mb-2",
                                    ),
                                ],
                                style={"display": "none"},
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel",
                                id="modal-cancel-btn",
                                color="secondary",
                                outline=True,
                                size="sm",
                                className="me-2",
                            ),
                            dbc.Button(
                                "Save",
                                id="modal-save-btn",
                                color="primary",
                                size="sm",
                            ),
                        ]
                    ),
                ],
            ),
            # ── Hidden stores ─────────────────────────────────────────────────
            dcc.Store(id="users-store", data=[]),
            dcc.Store(id="user-modal-store", data=None),
            dcc.Store(id="users-refresh-store", data=0),
            dcc.Store(id="audit-data-store", data=None),
            dcc.Store(
                id="users-sort-store",
                data={"col": None, "dir": "none"},
            ),
            dcc.Store(
                id="audit-sort-store",
                data={"col": None, "dir": "none"},
            ),
        ]
    )
