"""Admin modal and password-change Dash callbacks.

Callbacks for the AI Stock Analysis Dashboard.

Registers callbacks for the Add/Edit user modal, user create/update/toggle
actions, and the Change Password modal accessible from the NAVBAR.

Example::

    from dashboard.callbacks.admin_cbs2 import register
    register(app)
"""

import base64
import logging
import os
from typing import Any, Dict, List, Optional

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, html, no_update

from dashboard.callbacks.auth_utils import (
    _api_call,
    _resolve_token,
    _validate_token,
)
from dashboard.callbacks.utils import _check_input_safety, _is_valid_email

# Module-level logger — kept at module scope as required by the logging API.
_logger = logging.getLogger(__name__)

# Module-level configuration constant — prefixed with _
# to signal non-public use.
_BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8181")


def _upload_avatar_for_user(
    contents: str,
    filename: Optional[str],
    user_id: str,
    token: str,
) -> None:
    """Upload a base64-encoded avatar image for *user_id* via the backend API.

    Decodes the ``data:[type];base64,<data>`` string produced by
    :class:`~dash.dcc.Upload`, then ``POST``s it to
    ``/auth/upload-avatar?user_id=<user_id>`` as a multipart file.
    Failures are logged as warnings and silently swallowed so they never
    interrupt the save flow.

    Args:
        contents: Base64 data-URL string from ``dcc.Upload``.
        filename: Original filename of the uploaded image (for extension).
        user_id: UUID of the target user.
        token: JWT access token for the authenticated admin.
    """
    try:
        import requests as _req

        header, b64data = contents.split(";base64,", 1)
        content_type = header.split("data:")[-1]
        file_bytes = base64.b64decode(b64data)
        fname = filename or "avatar.png"
        url = _BACKEND_URL + "/auth/upload-avatar"
        params = {"user_id": user_id}
        headers = {"Authorization": "Bearer " + token}
        _req.post(
            url,
            params=params,
            files={"file": (fname, file_bytes, content_type)},
            headers=headers,
            timeout=15,
        )
    except Exception as exc:
        _logger.warning("Avatar upload failed for user %s: %s", user_id, exc)


def register(app) -> None:
    """Register admin modal and password-change callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        Output("user-modal", "is_open"),
        Output("user-modal-title", "children"),
        Output("modal-full-name", "value"),
        Output("modal-email", "value"),
        Output("modal-role", "value"),
        Output("modal-is-active", "value"),
        Output("modal-password-row", "style"),
        Output("modal-active-row", "style"),
        Output("user-modal-store", "data"),
        Output("modal-error", "children"),
        Output("user-permissions-section", "style"),
        Output("user-permissions-checklist", "value"),
        Output("admin-user-avatar-upload", "contents"),
        Output("admin-user-avatar-preview", "children"),
        Input("add-user-btn", "n_clicks"),
        Input({"type": "edit-user-btn", "index": ALL}, "n_clicks"),
        Input("modal-cancel-btn", "n_clicks"),
        State("users-store", "data"),
        prevent_initial_call=True,
    )
    def toggle_user_modal(
        add_clicks: Optional[int],
        edit_clicks_list: List[Optional[int]],
        cancel_clicks: Optional[int],
        users_data: Optional[List[Dict[str, Any]]],
    ):
        """Open the Add / Edit user modal or close it on Cancel.

        Triggered by the "Add User" button, any per-row "Edit" button, or the
        Cancel button.  Pattern-matching collects all "Edit" button clicks into
        a single callback.

        Args:
            add_clicks: ``n_clicks`` from the "Add User" button.
            edit_clicks_list: List of ``n_clicks`` from all Edit buttons.
            cancel_clicks: ``n_clicks`` from the Cancel button.
            users_data: Cached list of user dicts from ``users-store``.

        Returns:
            Tuple of modal state, title, field values, visibility styles,
            store data, error message, permissions section style, and
            permissions checklist value.
        """
        triggered = ctx.triggered_id
        triggered_value = ctx.triggered[0]["value"] if ctx.triggered else None

        # ── Cancel ────────────────────────────────────────────────────────
        if triggered == "modal-cancel-btn":
            return (
                False,
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
                "",
                no_update,
                no_update,
                None,
                [],  # clear upload; clear preview
            )

        # ── Add user ──────────────────────────────────────────────────────
        if triggered == "add-user-btn":
            return (
                True,
                "Add User",
                "",
                "",
                "general",
                ["active"],
                {},  # show password row
                {"display": "none"},  # hide is-active toggle
                {"mode": "add", "user": None},
                "",
                {"display": "none"},
                [],  # hide permissions; empty checklist
                None,
                [],  # clear upload; no preview for new users
            )

        # ── Edit user ─────────────────────────────────────────────────────
        if (
            isinstance(triggered, dict)
            and triggered.get("type") == "edit-user-btn"
        ):
            # triggered_value is None when Dash fires due to DOM injection
            # (pattern-match re-fires when Edit buttons
            # are added to the layout)
            # rather than an actual user click.  Skip in that case.
            if not triggered_value:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,  # upload / preview: leave as-is
                )
            user_id = triggered["index"]
            user = next(
                (u for u in (users_data or []) if u.get("user_id") == user_id),
                None,
            )
            if user is None:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    "User data not found — try refreshing.",
                    no_update,
                    no_update,
                    None,
                    [],  # clear stale upload; clear preview
                )
            # Show permissions section only for non-superuser roles.
            perms: Dict[str, bool] = user.get("page_permissions") or {}
            if user.get("role") == "superuser":
                perms_style: Dict[str, str] = {"display": "none"}
                perms_value: List[str] = []
            else:
                perms_style = {}
                perms_value = [k for k, v in perms.items() if v]
            # Build current avatar preview.
            avatar_url = user.get("avatar_url") or user.get(
                "profile_picture_url"
            )
            if avatar_url:
                full_url = (
                    _BACKEND_URL + avatar_url
                    if avatar_url.startswith("/")
                    else avatar_url
                )
                avatar_preview = html.Img(
                    src=full_url,
                    style={
                        "width": "56px",
                        "height": "56px",
                        "borderRadius": "50%",
                        "objectFit": "cover",
                        "objectPosition": "top",
                        "border": "1px solid #dee2e6",
                    },
                )
            else:
                avatar_preview = []
            return (
                True,
                "Edit User — " + user.get("email", ""),
                user.get("full_name", ""),
                user.get("email", ""),
                user.get("role", "general"),
                ["active"] if user.get("is_active", True) else [],
                {"display": "none"},  # hide password row for edits
                {},  # show is-active toggle
                {"mode": "edit", "user": user},
                "",
                perms_style,
                perms_value,
                None,
                avatar_preview,  # clear stale upload; show current avatar
            )

        return (
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )

    @app.callback(
        Output("user-modal", "is_open", allow_duplicate=True),
        Output("users-refresh-store", "data"),
        Output("modal-error", "children", allow_duplicate=True),
        Output("users-action-status", "children"),
        Input("modal-save-btn", "n_clicks"),
        State("user-modal-store", "data"),
        State("modal-full-name", "value"),
        State("modal-email", "value"),
        State("modal-password", "value"),
        State("modal-role", "value"),
        State("modal-is-active", "value"),
        State("user-permissions-checklist", "value"),
        State("admin-user-avatar-upload", "contents"),
        State("admin-user-avatar-upload", "filename"),
        State("users-refresh-store", "data"),
        State("auth-token-store", "data"),
        State("url", "search"),
        prevent_initial_call=True,
    )
    def save_user(
        n_clicks: Optional[int],
        modal_data: Optional[Dict[str, Any]],
        full_name: Optional[str],
        email: Optional[str],
        password: Optional[str],
        role: Optional[str],
        is_active_list: Optional[List[str]],
        perms_list: Optional[List[str]],
        avatar_contents: Optional[str],
        avatar_filename: Optional[str],
        refresh_n: Optional[int],
        stored_token: Optional[str],
        url_search: Optional[str],
    ):
        """Create or update a user via the backend API.

        Validates required fields locally, then calls ``POST /users`` (add
        mode) or ``PATCH /users/{user_id}`` (edit mode).  On success, if an
        avatar was provided it is uploaded via ``POST /auth/upload-avatar``.
        The modal is then closed and the user table is refreshed.

        Args:
            n_clicks: Save button click count.
            modal_data: Dict with ``mode`` (``"add"``/``"edit"``) and
                ``user`` (the original user dict for edits).
            full_name: Form value.
            email: Form value.
            password: Form value (only used in add mode).
            role: Selected role value.
            is_active_list: Checklist value — ``["active"]`` or ``[]``.
            perms_list: Page-permissions checklist value (edit mode only).
            avatar_contents: Base64-encoded image from ``dcc.Upload``.
            avatar_filename: Original filename of the uploaded image.
            refresh_n: Current refresh counter (incremented on success).
            stored_token: JWT from ``auth-token-store``.
            url_search: URL query string for token fallback.

        Returns:
            Tuple of (modal open, new refresh count, error text, status alert).
        """
        if not n_clicks:
            return no_update, no_update, no_update, no_update

        # Validate required fields
        if not (full_name and full_name.strip()):
            return True, no_update, "Full name is required.", no_update
        if not (email and email.strip()):
            return True, no_update, "Email is required.", no_update
        if not _is_valid_email(email.strip()):
            return True, no_update, "Enter a valid email address.", no_update

        # XSS / injection safety checks
        err = _check_input_safety(full_name.strip(), "Full name")
        if err:
            return True, no_update, err, no_update
        err = _check_input_safety(email.strip(), "Email", max_len=254)
        if err:
            return True, no_update, err, no_update

        mode = (modal_data or {}).get("mode", "add")
        token = _resolve_token(stored_token, url_search)
        saved_user_id: Optional[str] = None

        if mode == "add":
            if not (password and password.strip()):
                return (
                    True,
                    no_update,
                    "Password is required for new users.",
                    no_update,
                )
            payload: Dict[str, Any] = {
                "full_name": full_name.strip(),
                "email": email.strip(),
                "password": password,
                "role": role or "general",
            }
            resp = _api_call("post", "/users", token, json_body=payload)
            if resp is not None and resp.ok:
                try:
                    saved_user_id = resp.json().get("user_id")
                except Exception:
                    pass
        else:
            user = (modal_data or {}).get("user") or {}
            user_id = user.get("user_id", "")
            if not user_id:
                return True, no_update, "Cannot determine user ID.", no_update
            saved_user_id = user_id
            updates: Dict[str, Any] = {
                "full_name": full_name.strip(),
                "email": email.strip(),
                "role": role or "general",
                "is_active": "active" in (is_active_list or []),
            }
            # Include page_permissions for non-superuser roles.
            if role != "superuser":
                perms_checked = perms_list or []
                updates["page_permissions"] = {
                    "insights": "insights" in perms_checked,
                    "admin": "admin" in perms_checked,
                }
            resp = _api_call(
                "patch", "/users/" + user_id, token, json_body=updates
            )

        if resp is None:
            return True, no_update, "Could not reach backend.", no_update
        if resp.status_code == 400:
            detail = resp.json().get("detail", "Bad request")
            return True, no_update, str(detail), no_update
        if resp.status_code == 409:
            return True, no_update, "Email already in use.", no_update
        if not resp.ok:
            return (
                True,
                no_update,
                "Error " + str(resp.status_code) + ".",
                no_update,
            )

        # Upload avatar if one was provided.
        if avatar_contents and saved_user_id and token:
            _upload_avatar_for_user(
                avatar_contents, avatar_filename, saved_user_id, token
            )

        verb = "created" if mode == "add" else "updated"
        alert = dbc.Alert(
            "User " + verb + " successfully.",
            color="success",
            dismissable=True,
            duration=4000,
            className="py-2",
        )
        new_refresh = (refresh_n or 0) + 1
        return False, new_refresh, "", alert

    @app.callback(
        Output("users-refresh-store", "data", allow_duplicate=True),
        Output("users-action-status", "children", allow_duplicate=True),
        Input({"type": "toggle-user-btn", "index": ALL}, "n_clicks"),
        State("users-store", "data"),
        State("users-refresh-store", "data"),
        State("auth-token-store", "data"),
        State("url", "search"),
        prevent_initial_call=True,
    )
    def toggle_user_activation(
        n_clicks_list: List[Optional[int]],
        users_data: Optional[List[Dict[str, Any]]],
        refresh_n: Optional[int],
        stored_token: Optional[str],
        url_search: Optional[str],
    ):
        """Deactivate or reactivate a user with a single button click.

        For active users calls ``DELETE /users/{id}`` (soft-delete).
        For inactive users calls ``PATCH /users/{id}`` with
        ``is_active: true``.

        Args:
            n_clicks_list: All toggle-user-btn click counts (pattern-match).
            users_data: Cached user list from ``users-store``.
            refresh_n: Current refresh counter.
            stored_token: JWT from ``auth-token-store``.
            url_search: URL query string for token fallback.

        Returns:
            Tuple of (new refresh count, status alert).
        """
        triggered = ctx.triggered_id
        if (
            not isinstance(triggered, dict)
            or triggered.get("type") != "toggle-user-btn"
        ):
            return no_update, no_update

        # Ignore if all clicks are None (initial render)
        if not any(n_clicks_list):
            return no_update, no_update

        user_id = triggered["index"]
        user = next(
            (u for u in (users_data or []) if u.get("user_id") == user_id),
            None,
        )
        if user is None:
            return no_update, dbc.Alert(
                "User not found — refresh the page.",
                color="warning",
                dismissable=True,
                duration=4000,
                className="py-2",
            )

        token = _resolve_token(stored_token, url_search)
        is_active = user.get("is_active", True)

        if is_active:
            resp = _api_call("delete", "/users/" + user_id, token)
            action = "deactivated"
        else:
            resp = _api_call(
                "patch",
                "/users/" + user_id,
                token,
                json_body={"is_active": True},
            )
            action = "reactivated"

        if resp is None or not resp.ok:
            err = "" if resp is None else " (" + str(resp.status_code) + ")"
            return no_update, dbc.Alert(
                "Action failed" + err + ".",
                color="danger",
                dismissable=True,
                duration=4000,
                className="py-2",
            )

        alert = dbc.Alert(
            "User " + action + " successfully.",
            color="success" if action == "reactivated" else "warning",
            dismissable=True,
            duration=4000,
            className="py-2",
        )
        return (refresh_n or 0) + 1, alert

    @app.callback(
        Output("change-password-modal", "is_open"),
        Output("change-pw-error", "children"),
        Input("change-pw-save-btn", "n_clicks"),
        State("change-pw-new", "value"),
        State("change-pw-confirm", "value"),
        State("auth-token-store", "data"),
        State("url", "search"),
        prevent_initial_call=True,
    )
    def save_new_password(
        n_clicks: Optional[int],
        new_pw: Optional[str],
        confirm_pw: Optional[str],
        stored_token: Optional[str],
        url_search: Optional[str],
    ):
        """Apply a new password via the password-reset flow.

        Validates locally (non-empty, min 8 chars, one digit, passwords match),
        then calls ``POST /auth/password-reset/request`` to get a reset token
        and ``POST /auth/password-reset/confirm`` to apply it.

        Args:
            n_clicks: Save button click count.
            new_pw: New password value.
            confirm_pw: Confirmation password value.
            stored_token: JWT from ``auth-token-store``.
            url_search: URL query string for token fallback.

        Returns:
            Tuple of (modal open, error message).
        """
        if not n_clicks:
            return no_update, no_update

        if not (new_pw and new_pw.strip()):
            return True, "New password is required."
        if new_pw != confirm_pw:
            return True, "Passwords do not match."
        if len(new_pw) < 8:
            return True, "Password must be at least 8 characters."
        if not any(c.isdigit() for c in new_pw):
            return True, "Password must contain at least one digit."

        token = _resolve_token(stored_token, url_search)
        payload = _validate_token(token)
        if payload is None:
            return True, "Session expired — please sign in again."

        email = payload.get("email", "")

        # Step 1: request reset token
        resp1 = _api_call(
            "post",
            "/auth/password-reset/request",
            token,
            json_body={"email": email},
        )
        if resp1 is None or not resp1.ok:
            detail = "" if resp1 is None else resp1.json().get("detail", "")
            return (
                True,
                "Request failed: " + (detail or "backend unreachable") + ".",
            )

        reset_token = resp1.json().get("reset_token", "")
        if not reset_token:
            return True, "No reset token returned by server."

        # Step 2: confirm with new password
        resp2 = _api_call(
            "post",
            "/auth/password-reset/confirm",
            token,
            json_body={"reset_token": reset_token, "new_password": new_pw},
        )
        if resp2 is None or not resp2.ok:
            detail = "" if resp2 is None else resp2.json().get("detail", "")
            return (
                True,
                "Confirm failed: " + (detail or "backend unreachable") + ".",
            )

        return False, ""
