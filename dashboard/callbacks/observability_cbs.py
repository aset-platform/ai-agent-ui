"""Observability dashboard callbacks.

Fetches ``GET /admin/metrics`` every 10 seconds and renders
per-model tier status cards, cascade summary, and event log.
"""

import json
import logging
from datetime import datetime, timezone

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from dashboard.callbacks.auth_utils import (
    _api_call,
    _resolve_token,
)

_logger = logging.getLogger(__name__)


def _health_color(used: int, limit: int) -> str:
    """Return Bootstrap colour name based on utilisation.

    Args:
        used: Current usage count.
        limit: Maximum allowed.

    Returns:
        ``"success"``, ``"warning"``, or ``"danger"``.
    """
    if limit == 0:
        return "secondary"
    ratio = used / limit
    if ratio < 0.50:
        return "success"
    if ratio < 0.80:
        return "warning"
    return "danger"


def _parse_usage(val: str) -> tuple:
    """Parse ``"1234/8000"`` into ``(1234, 8000)``.

    Args:
        val: Formatted usage string.

    Returns:
        ``(used, limit)`` integers.
    """
    parts = val.split("/")
    return int(parts[0]), int(parts[1])


def _short_model(name: str) -> str:
    """Shorten a model name for display.

    Args:
        name: Full model identifier.

    Returns:
        Short label.
    """
    # "moonshotai/kimi-k2-instruct" → "kimi-k2"
    short = name.rsplit("/", 1)[-1]
    # Remove common suffixes.
    for suffix in [
        "-instruct",
        "-16e-instruct",
        "-versatile",
    ]:
        short = short.replace(suffix, "")
    return short


def _build_tier_card(model: str, data: dict) -> dbc.Col:
    """Build a single tier status card.

    Args:
        model: Full model name.
        data: Dict with ``tpm``, ``rpm``, ``tpd``, ``rpd``
            formatted as ``"used/limit"``.

    Returns:
        A :class:`~dbc.Col` wrapping the card.
    """
    tpm_u, tpm_l = _parse_usage(data.get("tpm", "0/1"))
    rpm_u, rpm_l = _parse_usage(data.get("rpm", "0/1"))

    tpm_color = _health_color(tpm_u, tpm_l)
    rpm_color = _health_color(rpm_u, rpm_l)

    tpm_pct = int(tpm_u / tpm_l * 100) if tpm_l else 0
    rpm_pct = int(rpm_u / rpm_l * 100) if rpm_l else 0

    return dbc.Col(
        dbc.Card(
            dbc.CardBody(
                [
                    html.H6(
                        _short_model(model),
                        className="fw-semibold mb-2",
                    ),
                    # TPM gauge
                    html.Small(
                        f"TPM: {tpm_u:,}/{tpm_l:,}",
                        className="text-muted",
                    ),
                    dbc.Progress(
                        value=min(tpm_pct, 100),
                        color=tpm_color,
                        className="mb-2",
                        style={"height": "6px"},
                    ),
                    # RPM gauge
                    html.Small(
                        f"RPM: {rpm_u}/{rpm_l}",
                        className="text-muted",
                    ),
                    dbc.Progress(
                        value=min(rpm_pct, 100),
                        color=rpm_color,
                        style={"height": "6px"},
                    ),
                ]
            ),
            className="shadow-sm",
        ),
        xs=12,
        sm=6,
        lg=3,
        className="mb-3",
    )


def register(app) -> None:
    """Register observability callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        Output("obs-metrics-store", "data"),
        Output("obs-prev-metrics-store", "data"),
        Input("obs-interval", "n_intervals"),
        State("auth-token-store", "data"),
        State("url", "search"),
        State("obs-prev-metrics-store", "data"),
    )
    def fetch_metrics(n, stored_token, url_search, prev_data):
        """Poll ``GET /admin/metrics`` every interval.

        Skips DOM update when metrics are unchanged
        (ignores ``timestamp`` field for comparison).

        Args:
            n: Interval tick count.
            stored_token: JWT from client store.
            url_search: URL query string.
            prev_data: Previous metrics for diff check.

        Returns:
            ``(metrics, prev)`` or ``no_update`` if
            unchanged.
        """
        token = _resolve_token(stored_token, url_search)
        resp = _api_call("get", "/admin/metrics", token)
        if resp is None or not resp.ok:
            if prev_data is None:
                return None, None
            return no_update, no_update
        data = resp.json()

        # Compare without volatile timestamp field.
        def _stable(d):
            if d is None:
                return ""
            c = {k: v for k, v in d.items() if k != "timestamp"}
            return json.dumps(c, sort_keys=True)

        if _stable(data) == _stable(prev_data):
            return no_update, no_update
        return data, data

    @app.callback(
        Output("obs-summary-row", "children"),
        Input("obs-metrics-store", "data"),
        prevent_initial_call=True,
    )
    def render_summary(data):
        """Render cascade summary badges.

        Args:
            data: Metrics store data.

        Returns:
            Summary row with stat badges.
        """
        if not data:
            return html.P(
                "No metrics available.",
                className="text-muted",
            )
        cs = data.get("cascade_stats", {})
        items = [
            (
                "Total Requests",
                cs.get("requests_total", 0),
                "primary",
            ),
            (
                "Cascades",
                cs.get("cascade_count", 0),
                "warning",
            ),
            (
                "Compressions",
                cs.get("compression_count", 0),
                "info",
            ),
        ]
        return dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Small(
                                    label,
                                    className="text-muted",
                                ),
                                html.H4(
                                    f"{val:,}",
                                    className=("mb-0 fw-bold"),
                                ),
                            ],
                            className="text-center py-2",
                        ),
                        color=color,
                        outline=True,
                        className="shadow-sm",
                    ),
                    xs=4,
                )
                for label, val, color in items
            ],
            className="mb-3",
        )

    @app.callback(
        Output("obs-tier-cards", "children"),
        Input("obs-metrics-store", "data"),
        prevent_initial_call=True,
    )
    def render_tier_cards(data):
        """Render per-model budget status cards.

        Args:
            data: Metrics store data.

        Returns:
            Row of model cards with TPM/RPM gauges.
        """
        if not data or not data.get("models"):
            return html.P(
                "No model data.",
                className="text-muted",
            )
        models = data["models"]
        cards = [_build_tier_card(m, d) for m, d in models.items()]
        return dbc.Row(cards)

    @app.callback(
        Output("obs-cascade-table", "children"),
        Input("obs-metrics-store", "data"),
        prevent_initial_call=True,
    )
    def render_cascade_log(data):
        """Render recent cascade events as a table.

        Args:
            data: Metrics store data.

        Returns:
            Bootstrap table of cascade events.
        """
        if not data:
            return html.P(
                "No cascade data.",
                className="text-muted",
            )
        log = data.get("cascade_stats", {}).get("cascade_log", [])
        if not log:
            return html.P(
                "No cascade events recorded yet.",
                className="text-muted small",
            )
        # Reverse so newest first.
        log = list(reversed(log))
        rows = []
        for ev in log[:25]:
            ts = ev.get("timestamp", 0)
            dt = datetime.fromtimestamp(
                ts,
                tz=timezone.utc,
            )
            rows.append(
                html.Tr(
                    [
                        html.Td(
                            dt.strftime(
                                "%H:%M:%S",
                            ),
                            className="small",
                        ),
                        html.Td(
                            _short_model(
                                ev.get(
                                    "from_model",
                                    "—",
                                ),
                            ),
                        ),
                        html.Td(
                            _short_model(
                                ev.get(
                                    "to_model",
                                    "",
                                ),
                            )
                            or "—",
                        ),
                        html.Td(
                            ev.get("reason", "—"),
                            className="small",
                        ),
                    ]
                )
            )
        return dbc.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Time"),
                            html.Th("From"),
                            html.Th("To"),
                            html.Th("Reason"),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            bordered=True,
            hover=True,
            responsive=True,
            size="sm",
            className="small",
        )
