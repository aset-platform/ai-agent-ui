"""LLM Observability tab layout for the admin page.

Provides :func:`observability_layout` which renders tier status
cards with gauge-style utilisation bars, a cascade event log,
and an auto-refresh interval.
"""

from dash import dcc, html


def observability_layout() -> html.Div:
    """Build the LLM Observability tab content.

    Contains three sections:

    1. Per-model token budget status cards (TPM/RPM gauges).
    2. Cascade statistics summary.
    3. Recent cascade event log table.

    Data is fetched via a 10-second ``dcc.Interval``.

    Returns:
        :class:`~dash.html.Div` with the observability
        dashboard.
    """
    return html.Div(
        className="mt-3",
        children=[
            # Silent background refresh every 60 seconds.
            dcc.Interval(
                id="obs-interval",
                interval=60_000,
                n_intervals=0,
            ),
            # Hidden stores for metrics data.
            dcc.Store(id="obs-metrics-store", data=None),
            dcc.Store(
                id="obs-prev-metrics-store",
                data=None,
            ),
            # ── Summary row ────────────────────────
            html.Div(
                id="obs-summary-row",
                className="mb-3",
            ),
            # ── Tier status cards ──────────────────
            html.H5(
                "Model Budget Status",
                className="text-muted mb-3",
            ),
            html.Div(id="obs-tier-cards"),
            # ── Cascade event log ──────────────────
            html.H5(
                "Recent Cascade Events",
                className="text-muted mb-3 mt-4",
            ),
            html.Div(id="obs-cascade-table"),
        ],
    )
