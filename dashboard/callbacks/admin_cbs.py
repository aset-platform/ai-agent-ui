"""Admin user-management Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks for fetching, rendering, filtering, and paginating the
users table and audit log table on the ``/admin/users`` page.

Example::

    from dashboard.callbacks.admin_cbs import register
    register(app)
"""

from __future__ import annotations

import logging
import math

from dash import Input, Output, State, html, no_update

from dashboard.callbacks.auth_utils import (
    _api_call,
    _resolve_token,
)
from dashboard.callbacks.sort_helpers import (
    register_sort_callback,
)
from dashboard.callbacks.table_builders import (
    _build_audit_table,
    _build_users_table,
)

# Module-level logger; mutable but required at module
# scope for callback closures.
_logger = logging.getLogger(__name__)


def register(app) -> None:
    """Register admin user-management callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        Output("users-pagination", "active_page"),
        Input("users-search", "value"),
        Input("users-page-size", "value"),
        Input("users-sort-store", "data"),
        prevent_initial_call=True,
    )
    def reset_users_page_on_filter(search, page_size, _sort):
        """Reset users pagination to page 1 on filter or sort change.

        Args:
            search: Current search input value.
            page_size: New page size value.
            _sort: Sort state (triggers reset).

        Returns:
            Integer ``1`` to reset the active page.
        """
        return 1

    @app.callback(
        Output("audit-pagination", "active_page"),
        Input("audit-search", "value"),
        Input("audit-page-size", "value"),
        Input("audit-sort-store", "data"),
        prevent_initial_call=True,
    )
    def reset_audit_page_on_filter(search, page_size, _sort):
        """Reset audit pagination to page 1 on filter or sort change.

        Args:
            search: Current search input value.
            page_size: New page size value.
            _sort: Sort state (triggers reset).

        Returns:
            Integer ``1`` to reset the active page.
        """
        return 1

    @app.callback(
        Output("users-store", "data"),
        Input("url", "pathname"),
        Input("users-refresh-store", "data"),
        State("auth-token-store", "data"),
        State("url", "search"),
        prevent_initial_call=False,
    )
    def load_users_table(
        pathname: str | None,
        _refresh: int | None,
        stored_token: str | None,
        url_search: str | None,
    ):
        """Fetch all users from the backend API and store the raw list.

        Fires on page navigation (to detect /admin/users) and whenever
        ``users-refresh-store`` is incremented by a save or toggle action.
        Rendering is handled by ``render_users_page``.

        Args:
            pathname: Current URL path.
            _refresh: Refresh counter from ``users-refresh-store``.
            stored_token: JWT from ``auth-token-store``.
            url_search: URL query string for token fallback.

        Returns:
            List of user dicts, or an empty list on error.
        """
        if pathname != "/admin/users":
            return no_update

        token = _resolve_token(stored_token, url_search)
        resp = _api_call("get", "/users", token)
        if resp is None or not resp.ok:
            return []

        return resp.json()

    @app.callback(
        Output("users-table-container", "children"),
        Output("users-pagination", "max_value"),
        Output("users-count-text", "children"),
        Input("users-store", "data"),
        Input("users-pagination", "active_page"),
        Input("users-search", "value"),
        Input("users-page-size", "value"),
        Input("users-sort-store", "data"),
    )
    def render_users_page(
        users_data,
        active_page,
        search_term,
        page_size,
        sort_state=None,
    ):
        """Filter, sort, slice, and render one page of the users table.

        Args:
            users_data: Full list of user dicts.
            active_page: Current pagination page (1-indexed).
            search_term: Debounced search text.
            page_size: Rows per page as string.
            sort_state: Column sort state dict.

        Returns:
            Tuple of (table, max_value, count text).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        page_size_int = int(page_size or 10)
        users = users_data or []

        # Apply search filter
        q = (search_term or "").strip().lower()
        if q:
            users = [
                u
                for u in users
                if q in (u.get("full_name") or "").lower()
                or q in (u.get("email") or "").lower()
                or q in (u.get("role") or "").lower()
            ]

        total = len(users)
        if total == 0:
            msg = (
                "No matching users found." if q else "No user accounts found."
            )
            return (
                html.P(msg, className="text-muted"),
                1,
                "",
            )
        max_pages = max(1, math.ceil(total / page_size_int))
        page = min(active_page or 1, max_pages)
        start = (page - 1) * page_size_int
        count_txt = (
            f"Showing {start + 1}\u2013"
            f"{min(start + page_size_int, total)}"
            f" of {total} users"
        )
        return (
            _build_users_table(
                users[start : start + page_size_int],
                sort_state,
            ),
            max_pages,
            count_txt,
        )

    @app.callback(
        Output("audit-data-store", "data"),
        Input("admin-tabs", "active_tab"),
        Input("url", "pathname"),
        State("auth-token-store", "data"),
        State("url", "search"),
        prevent_initial_call=False,
    )
    def load_audit_log(
        active_tab: str | None,
        pathname: str | None,
        stored_token: str | None,
        url_search: str | None,
    ):
        """Fetch the audit log from the backend API.

        Store the raw event list.

        Fires when the admin page is visited or when the user switches to
        the Audit Log tab.  Rendering is handled by ``render_audit_page``.

        Args:
            active_tab: ID of the currently selected tab.
            pathname: Current URL path.
            stored_token: JWT from ``auth-token-store``.
            url_search: URL query string for token fallback.

        Returns:
            List of audit event dicts, or an empty list on error or wrong tab.
        """
        if pathname != "/admin/users" or active_tab != "audit-tab":
            return no_update

        token = _resolve_token(stored_token, url_search)
        resp = _api_call("get", "/admin/audit-log", token)
        if resp is None or not resp.ok:
            return []

        return resp.json().get("events", [])

    @app.callback(
        Output("audit-log-container", "children"),
        Output("audit-pagination", "max_value"),
        Output("audit-count-text", "children"),
        Input("audit-data-store", "data"),
        Input("audit-pagination", "active_page"),
        Input("audit-search", "value"),
        Input("audit-page-size", "value"),
        Input("audit-sort-store", "data"),
    )
    def render_audit_page(
        audit_data,
        active_page,
        search_term,
        page_size,
        sort_state=None,
    ):
        """Filter, sort, slice, and render one page of the audit log.

        Args:
            audit_data: Full list of audit event dicts.
            active_page: Current pagination page (1-indexed).
            search_term: Debounced search text.
            page_size: Rows per page as string.
            sort_state: Column sort state dict.

        Returns:
            Tuple of (table, max_value, count text).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        page_size_int = int(page_size or 10)
        events = audit_data or []

        # Apply search filter
        q = (search_term or "").strip().lower()
        if q:
            events = [
                e
                for e in events
                if q in (e.get("event_type") or "").lower()
                or q in (e.get("actor_user_id") or "").lower()
                or q in (e.get("target_user_id") or "").lower()
                or q in (e.get("metadata") or "").lower()
            ]

        total = len(events)
        if total == 0:
            msg = (
                "No matching events found." if q else "No audit events found."
            )
            return (
                html.P(msg, className="text-muted"),
                1,
                "",
            )
        max_pages = max(1, math.ceil(total / page_size_int))
        page = min(active_page or 1, max_pages)
        start = (page - 1) * page_size_int
        count_txt = (
            f"Showing {start + 1}\u2013"
            f"{min(start + page_size_int, total)}"
            f" of {total} events"
        )
        return (
            _build_audit_table(
                events[start : start + page_size_int],
                sort_state,
            ),
            max_pages,
            count_txt,
        )

    # ── Sort-header callbacks ─────────────────────────────
    register_sort_callback(app, "users")
    register_sort_callback(app, "audit")
