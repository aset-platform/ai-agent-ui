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


_HEALTH_COLORS = {
    "healthy": "success",
    "degraded": "warning",
    "down": "danger",
    "disabled": "secondary",
}

_HEALTH_ICONS = {
    "healthy": "●",
    "degraded": "▲",
    "down": "✕",
    "disabled": "⊘",
}


def _short_model(name: str) -> str:
    """Shorten a model name for display.

    Args:
        name: Full model identifier.

    Returns:
        Short label.
    """
    # "qwen/qwen3-32b" → "qwen3-32b"
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


def _build_health_card(tier: dict) -> dbc.Col:
    """Build a single tier health status card.

    Args:
        tier: Dict with ``model``, ``status``,
            ``failures_5m``, ``successes_5m``,
            ``cascade_count``, ``latency``.

    Returns:
        A :class:`~dbc.Col` wrapping the card.
    """
    model = tier.get("model", "unknown")
    status = tier.get("status", "healthy")
    color = _HEALTH_COLORS.get(status, "secondary")
    icon = _HEALTH_ICONS.get(status, "?")
    lat = tier.get("latency", {})
    avg_ms = lat.get("avg_ms")
    p95_ms = lat.get("p95_ms")

    lat_text = "—"
    if avg_ms is not None:
        lat_text = f"avg {avg_ms}ms"
        if p95_ms is not None:
            lat_text += f" / p95 {p95_ms}ms"

    body_children = [
        html.Div(
            [
                html.Span(
                    icon,
                    className=f"text-{color} me-2",
                    style={"fontSize": "1.2rem"},
                ),
                html.Span(
                    _short_model(model),
                    className="fw-semibold",
                ),
            ],
            className="mb-2",
        ),
        dbc.Badge(
            status.upper(),
            color=color,
            className="mb-2",
        ),
        html.Div(
            [
                html.Small(
                    f"Failures (5m): " f"{tier.get('failures_5m', 0)}",
                    className="text-muted d-block",
                ),
                html.Small(
                    f"Successes (5m): " f"{tier.get('successes_5m', 0)}",
                    className="text-muted d-block",
                ),
                html.Small(
                    f"Cascades: " f"{tier.get('cascade_count', 0)}",
                    className="text-muted d-block",
                ),
                html.Small(
                    f"Latency: {lat_text}",
                    className="text-muted d-block",
                ),
            ],
        ),
    ]

    return dbc.Col(
        dbc.Card(
            dbc.CardBody(body_children),
            className="shadow-sm",
            style={
                "borderLeft": f"4px solid var(--bs-{color})",
            },
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
        Output("obs-health-store", "data"),
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
                return None, None, None
            return no_update, no_update, no_update
        data = resp.json()

        # Fetch tier health in parallel.
        health_data = None
        health_resp = _api_call(
            "get",
            "/admin/tier-health",
            token,
        )
        if health_resp and health_resp.ok:
            health_data = health_resp.json()

        # Compare without volatile timestamp field.
        def _stable(d):
            if d is None:
                return ""
            c = {k: v for k, v in d.items() if k != "timestamp"}
            return json.dumps(c, sort_keys=True)

        if _stable(data) == _stable(prev_data):
            return (
                no_update,
                no_update,
                health_data or no_update,
            )
        return data, data, health_data

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

    @app.callback(
        Output("obs-health-cards", "children"),
        Input("obs-health-store", "data"),
        prevent_initial_call=True,
    )
    def render_health_cards(data):
        """Render per-tier health status cards.

        Args:
            data: Tier health store data.

        Returns:
            Row of health cards with status badges.
        """
        if not data:
            return html.P(
                "No tier health data.",
                className="text-muted",
            )
        tiers = data.get("health", {}).get(
            "tiers",
            [],
        )
        if not tiers:
            return html.P(
                "No tiers configured.",
                className="text-muted",
            )
        summary = data.get("health", {}).get(
            "summary",
            {},
        )
        summary_row = dbc.Row(
            [
                dbc.Col(
                    dbc.Badge(
                        f"{summary.get('healthy', 0)}" " Healthy",
                        color="success",
                        className="me-2 px-3 py-2",
                    ),
                ),
                dbc.Col(
                    dbc.Badge(
                        f"{summary.get('degraded', 0)}" " Degraded",
                        color="warning",
                        className="me-2 px-3 py-2",
                    ),
                ),
                dbc.Col(
                    dbc.Badge(
                        f"{summary.get('down', 0)}" " Down",
                        color="danger",
                        className="me-2 px-3 py-2",
                    ),
                ),
            ],
            className="mb-3",
        )
        cards = [_build_health_card(t) for t in tiers]
        return html.Div(
            [summary_row, dbc.Row(cards)],
        )
