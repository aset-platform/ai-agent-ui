"""Interactive callback definitions for the AI Stock Analysis Dashboard.

All Dash callbacks are registered inside the :func:`register_callbacks`
factory, which accepts the :class:`~dash.Dash` application instance.
This pattern avoids circular imports between ``app.py`` and this module.

Data is read directly from ``data/raw/`` and ``data/forecasts/`` parquet
files.  The *Run New Analysis* button imports backend tool functions from
``backend/tools/`` (via the ``sys.path`` insertion done in ``app.py``) and
re-runs the full fetch → analysis → Prophet forecast pipeline without any
LLM involved.

Example::

    from dashboard.callbacks import register_callbacks
    register_callbacks(app)
"""

import json
import logging
import math
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import ta
from dash import ALL, Input, Output, State, ctx, html, no_update
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so backend modules are importable
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Path constants (mirror backend tool constants)
# ---------------------------------------------------------------------------

_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_FORECASTS = _PROJECT_ROOT / "data" / "forecasts"
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_REGISTRY_PATH = _DATA_METADATA / "stock_registry.json"

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UNSAFE_SQL = re.compile(
    r"(--|/\*|\*/|';\s*|\";\s*|union\s+select|drop\s+table|or\s+1\s*=\s*1|or\s+'1'\s*=\s*'1')",
    re.IGNORECASE,
)
_UNSAFE_XSS = re.compile(r"(javascript:|vbscript:|data:)", re.IGNORECASE)


def _is_valid_email(value: str) -> bool:
    """Return True if *value* looks like a valid email address.

    Args:
        value: String to check.

    Returns:
        ``True`` when the string matches a basic ``user@domain.tld`` pattern.
    """
    return bool(_EMAIL_RE.match(value))


def _check_input_safety(value: str, field: str, max_len: int = 200) -> Optional[str]:
    """Return an error string if *value* contains unsafe content, else ``None``.

    Checks performed (in order): max length, HTML characters, null bytes,
    XSS-style URI schemes, and common SQL injection sequences.

    Args:
        value: The user-supplied string to validate.
        field: Human-readable field label used in error messages.
        max_len: Maximum allowed character length (default 200).

    Returns:
        An error message string when a check fails, or ``None`` when the
        value is safe.
    """
    if len(value) > max_len:
        return f"{field} is too long (max {max_len} characters)."
    if "<" in value or ">" in value:
        return f"{field} must not contain HTML characters."
    if "\x00" in value:
        return f"{field} contains invalid characters."
    if _UNSAFE_XSS.search(value):
        return f"{field} contains unsafe content."
    if _UNSAFE_SQL.search(value):
        return f"{field} contains unsafe content."
    return None


# ---------------------------------------------------------------------------
# Market classification helper
# ---------------------------------------------------------------------------


def _get_market(ticker: str) -> str:
    """Return ``'india'`` for NSE/BSE tickers (.NS / .BO), ``'us'`` otherwise.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        ``'india'`` or ``'us'``.
    """
    return "india" if ticker.upper().endswith((".NS", ".BO")) else "us"


# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------


def _currency_symbol(code: str) -> str:
    """Return the display symbol for a 3-letter ISO currency code.

    Args:
        code: ISO 4217 currency code, e.g. ``"USD"`` or ``"INR"``.

    Returns:
        The currency symbol string, e.g. ``"$"`` or ``"₹"``.
        Falls back to the code itself for unmapped currencies.
    """
    return {
        "USD": "$", "INR": "₹", "GBP": "£", "EUR": "€",
        "JPY": "¥", "CNY": "¥", "AUD": "A$", "CAD": "CA$",
        "HKD": "HK$", "SGD": "S$",
    }.get((code or "USD").upper(), code or "$")


def _get_currency(ticker: str) -> str:
    """Return the currency symbol for *ticker* by reading its metadata JSON.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Currency symbol string such as ``"$"`` or ``"₹"``.
        Falls back to ``"$"`` if the metadata file is missing.
    """
    meta_path = _DATA_METADATA / f"{ticker.upper()}_info.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return _currency_symbol(data.get("currency", "USD") or "USD")
    except Exception:
        return "$"


# ---------------------------------------------------------------------------
# JWT authentication helpers
# ---------------------------------------------------------------------------

_FRONTEND_LOGIN_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000") + "/login"


def _validate_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT access token.

    Reads ``JWT_SECRET_KEY`` from the environment.  Returns the decoded
    payload if the token is valid and of type ``"access"``; returns ``None``
    for any failure (missing key, invalid signature, expired token, wrong
    type).

    Args:
        token: Raw JWT string, or ``None``.

    Returns:
        Decoded payload dict, or ``None`` if invalid.
    """
    if not token:
        return None
    secret = os.environ.get("JWT_SECRET_KEY", "")
    if not secret:
        logger.warning(
            "_validate_token: JWT_SECRET_KEY not set — all dashboard requests will be denied."
        )
        return None
    try:
        from jose import JWTError, jwt as _jwt

        payload = _jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            return None
        return payload
    except Exception as exc:
        logger.debug("Token validation failed: %s", exc)
        return None


def _unauth_notice() -> html.Div:
    """Return a Dash layout component shown when the user is not authenticated.

    Displays a centred card with a link back to the Next.js login page.

    Returns:
        A :class:`~dash.html.Div` containing the unauthenticated UI.
    """
    return html.Div(
        html.Div(
            [
                html.Div("🔒", style={"fontSize": "2.5rem", "marginBottom": "0.75rem"}),
                html.H5("Authentication required", className="mb-2 fw-semibold"),
                html.P(
                    "Your session has expired or you are not signed in.",
                    className="text-muted mb-3",
                    style={"fontSize": "0.9rem"},
                ),
                html.A(
                    "Sign in →",
                    href=_FRONTEND_LOGIN_URL,
                    target="_top",
                    className="btn btn-primary btn-sm px-4",
                ),
            ],
            style={
                "background": "#fff",
                "border": "1px solid #e5e7eb",
                "borderRadius": "1rem",
                "padding": "2.5rem",
                "maxWidth": "360px",
                "textAlign": "center",
            },
        ),
        style={
            "display": "flex",
            "alignItems": "center",
            "justifyContent": "center",
            "minHeight": "60vh",
        },
    )


def _admin_forbidden() -> html.Div:
    """Return a Dash layout component shown when a non-superuser visits /admin/*.

    Returns:
        A :class:`~dash.html.Div` with a 403-style message and a back link.
    """
    return html.Div(
        html.Div(
            [
                html.Div("⛔", style={"fontSize": "2.5rem", "marginBottom": "0.75rem"}),
                html.H5("Access denied", className="mb-2 fw-semibold"),
                html.P(
                    "This page requires superuser privileges.",
                    className="text-muted mb-3",
                    style={"fontSize": "0.9rem"},
                ),
                html.A(
                    "← Back to home",
                    href="/",
                    className="btn btn-outline-secondary btn-sm px-4",
                ),
            ],
            style={
                "background": "#fff",
                "border": "1px solid #e5e7eb",
                "borderRadius": "1rem",
                "padding": "2.5rem",
                "maxWidth": "360px",
                "textAlign": "center",
            },
        ),
        style={
            "display": "flex",
            "alignItems": "center",
            "justifyContent": "center",
            "minHeight": "60vh",
        },
    )


# ---------------------------------------------------------------------------
# Backend API helper
# ---------------------------------------------------------------------------

_BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8181")


def _resolve_token(
    stored_token: Optional[str],
    url_search: Optional[str],
) -> Optional[str]:
    """Return the best available JWT, preferring the URL query parameter.

    Args:
        stored_token: Token persisted in ``auth-token-store`` localStorage.
        url_search: URL query string, e.g. ``"?token=eyJ..."``.

    Returns:
        JWT string or ``None`` if neither source has a token.
    """
    token = stored_token
    if url_search:
        qs = parse_qs(url_search.lstrip("?"))
        url_token = qs.get("token", [None])[0]
        if url_token:
            token = url_token
    return token


def _api_call(
    method: str,
    path: str,
    token: Optional[str],
    json_body: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """Make an authenticated HTTP request to the FastAPI backend.

    Args:
        method: HTTP method — ``"get"``, ``"post"``, ``"patch"``,
            or ``"delete"``.
        path: URL path starting with ``"/"`` (e.g. ``"/users"``).
        token: JWT access token; ``None`` causes an immediate ``None`` return.
        json_body: Optional JSON-serialisable request body for POST/PATCH.

    Returns:
        The :mod:`requests` ``Response`` object, or ``None`` on connection
        error or missing token.
    """
    if not token:
        return None
    try:
        import requests as _req  # lazy import — avoids startup cost

        url = f"{_BACKEND_URL}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        kwargs: Dict[str, Any] = {"headers": headers, "timeout": 10}
        if json_body is not None:
            kwargs["json"] = json_body
        fn = getattr(_req, method.lower())
        return fn(url, **kwargs)
    except Exception as exc:
        logger.error("API call %s %s failed: %s", method.upper(), path, exc)
        return None


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
    import dash_bootstrap_components as _dbc
    from dash import html as _html

    if not users:
        return _html.P("No user accounts found.", className="text-muted")

    header = _html.Thead(_html.Tr([
        _html.Th("Name"),
        _html.Th("Email"),
        _html.Th("Role"),
        _html.Th("Status"),
        _html.Th("Created"),
        _html.Th("Last Login"),
        _html.Th("Actions", className="text-end"),
    ]))

    rows = []
    for user in users:
        is_active = user.get("is_active", True)
        created = (user.get("created_at") or "")[:10] or "—"
        last_login = (user.get("last_login_at") or "")[:10] or "—"

        row = _html.Tr([
            _html.Td(user.get("full_name", "—")),
            _html.Td(
                user.get("email", "—"),
                style={"fontSize": "0.85rem"},
            ),
            _html.Td(
                _dbc.Badge(
                    user.get("role", "—"),
                    color="danger" if user.get("role") == "superuser" else "primary",
                    className="fw-normal",
                )
            ),
            _html.Td(
                _dbc.Badge(
                    "Active" if is_active else "Inactive",
                    color="success" if is_active else "secondary",
                    className="fw-normal",
                )
            ),
            _html.Td(created, style={"fontSize": "0.8rem", "color": "#6b7280"}),
            _html.Td(last_login, style={"fontSize": "0.8rem", "color": "#6b7280"}),
            _html.Td(
                [
                    _dbc.Button(
                        "Edit",
                        id={"type": "edit-user-btn", "index": user["user_id"]},
                        size="sm",
                        color="outline-primary",
                        className="me-1 py-0 px-2",
                        style={"fontSize": "0.75rem"},
                    ),
                    _dbc.Button(
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
        ])
        rows.append(row)

    return _dbc.Table(
        [header, _html.Tbody(rows)],
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
    import dash_bootstrap_components as _dbc
    from dash import html as _html

    if not events:
        return _html.P("No audit events found.", className="text-muted")

    header = _html.Thead(_html.Tr([
        _html.Th("When"),
        _html.Th("Event"),
        _html.Th("Actor"),
        _html.Th("Target"),
        _html.Th("Details"),
    ]))

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

        rows.append(_html.Tr([
            _html.Td(ts, style={"fontSize": "0.78rem", "color": "#6b7280", "whiteSpace": "nowrap"}),
            _html.Td(
                _dbc.Badge(
                    ev.get("event_type", "—"),
                    color="info",
                    className="fw-normal",
                    style={"fontSize": "0.72rem"},
                )
            ),
            _html.Td(
                (ev.get("actor_user_id") or "—")[:8] + "…",
                style={"fontSize": "0.78rem", "fontFamily": "monospace"},
            ),
            _html.Td(
                (ev.get("target_user_id") or "—")[:8] + "…",
                style={"fontSize": "0.78rem", "fontFamily": "monospace"},
            ),
            _html.Td(metadata, style={"fontSize": "0.78rem", "color": "#6b7280"}),
        ]))

    return _dbc.Table(
        [header, _html.Tbody(rows)],
        bordered=True,
        hover=True,
        responsive=True,
        className="table table-sm",
    )


# ---------------------------------------------------------------------------
# Private data-loading helpers
# ---------------------------------------------------------------------------


def _load_reg_cb() -> dict:
    """Load the stock registry for use inside callbacks.

    Returns:
        Registry dict; empty dict if missing or unreadable.
    """
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with open(_REGISTRY_PATH) as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("registry load failed: %s", exc)
        return {}


def _load_raw(ticker: str) -> Optional[pd.DataFrame]:
    """Load the raw OHLCV parquet file for a ticker.

    Args:
        ticker: Uppercase ticker symbol (e.g. ``"AAPL"``).

    Returns:
        DataFrame with DatetimeIndex, or ``None`` if the file is absent.
    """
    path = _DATA_RAW / f"{ticker}_raw.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, engine="pyarrow")
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as exc:
        logger.error("Error loading %s: %s", path, exc)
        return None


def _load_forecast(ticker: str, horizon_months: int) -> Optional[pd.DataFrame]:
    """Find and load the best-matching forecast parquet for a ticker.

    Prefers an exact match for *horizon_months*; falls back to longer
    horizons (9m → 6m → 3m) so that a 9-month forecast can satisfy a
    6-month request.

    Args:
        ticker: Uppercase ticker symbol.
        horizon_months: Requested forecast horizon in months.

    Returns:
        DataFrame with ``ds``, ``yhat``, ``yhat_lower``, ``yhat_upper``
        columns, or ``None`` if no forecast file is found.
    """
    for h in [horizon_months, 9, 6, 3]:
        if h < horizon_months:
            continue
        path = _DATA_FORECASTS / f"{ticker}_{h}m_forecast.parquet"
        if path.exists():
            try:
                df = pd.read_parquet(path, engine="pyarrow")
                df["ds"] = pd.to_datetime(df["ds"])
                return df
            except Exception as exc:
                logger.error("Error loading forecast %s: %s", path, exc)
    return None


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate technical indicators and return an enriched DataFrame copy.

    Adds SMA_50, SMA_200, EMA_20, RSI_14, MACD, MACD_Signal, MACD_Hist,
    BB_Upper, BB_Middle, BB_Lower, and ATR_14 columns.

    Args:
        df: OHLCV DataFrame with ``Open``, ``High``, ``Low``, ``Close``
            columns and a DatetimeIndex.

    Returns:
        Copy of *df* with all indicator columns appended.
    """
    df = df.copy()
    close = df["Close"]
    df["SMA_50"]     = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
    df["SMA_200"]    = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()
    df["EMA_20"]     = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
    df["RSI_14"]     = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    macd             = ta.trend.MACD(close=close)
    df["MACD"]       = macd.macd()
    df["MACD_Signal"]= macd.macd_signal()
    df["MACD_Hist"]  = macd.macd_diff()
    bb               = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    df["BB_Upper"]   = bb.bollinger_hband()
    df["BB_Middle"]  = bb.bollinger_mavg()
    df["BB_Lower"]   = bb.bollinger_lband()
    df["ATR_14"]     = ta.volatility.AverageTrueRange(
        high=df["High"], low=df["Low"], close=close, window=14
    ).average_true_range()
    return df


def _empty_fig(message: str, height: int = 400) -> go.Figure:
    """Return a light-themed empty figure with a centred annotation.

    Args:
        message: Text to display in the empty chart area.
        height: Chart height in pixels.

    Returns:
        :class:`plotly.graph_objects.Figure` with the annotation.
    """
    fig = go.Figure()
    fig.add_annotation(
        text=message, xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=15, color="rgba(0,0,0,0.4)"),
    )
    fig.update_layout(
        template="plotly_white", height=height,
        paper_bgcolor="#ffffff", plot_bgcolor="#f9fafb",
        xaxis={"visible": False}, yaxis={"visible": False},
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


# ---------------------------------------------------------------------------
# Chart-building helpers
# ---------------------------------------------------------------------------


def _build_analysis_fig(
    df: pd.DataFrame,
    ticker: str,
    overlays: List[str],
) -> go.Figure:
    """Build the 3-panel interactive analysis chart.

    Panel 1 (60 %): Candlestick + optional SMA 50 / SMA 200 / Bollinger
    Bands overlays + optional Volume bars on a secondary y-axis.
    Panel 2 (20 %): RSI (14) with overbought/oversold zones.
    Panel 3 (20 %): MACD line, signal line, and histogram.

    Args:
        df: OHLCV DataFrame with indicator columns already added.
        ticker: Ticker symbol used in the chart title.
        overlays: List of active overlay keys
            (``"sma50"``, ``"sma200"``, ``"bb"``, ``"volume"``).

    Returns:
        :class:`plotly.graph_objects.Figure` for use in a :class:`dcc.Graph`.
    """
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.60, 0.20, 0.20],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}], [{"secondary_y": False}]],
        subplot_titles=(f"{ticker} — Price & Indicators", "RSI (14)", "MACD"),
    )

    # ── Panel 1: Candlestick ──────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
        name="OHLC",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
    ), row=1, col=1, secondary_y=False)

    if "sma50" in overlays and "SMA_50" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["SMA_50"], name="SMA 50",
            line=dict(color="orange", width=1.5),
        ), row=1, col=1, secondary_y=False)

    if "sma200" in overlays and "SMA_200" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["SMA_200"], name="SMA 200",
            line=dict(color="tomato", width=1.5),
        ), row=1, col=1, secondary_y=False)

    if "bb" in overlays and "BB_Upper" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_Upper"], name="BB Upper",
            line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
        ), row=1, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_Lower"], name="BB Lower",
            line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
            fill="tonexty", fillcolor="rgba(100,149,237,0.07)",
        ), row=1, col=1, secondary_y=False)

    if "volume" in overlays and "Volume" in df.columns:
        vol_colors = [
            "#26a69a" if df["Close"].iloc[i] >= df["Open"].iloc[i] else "#ef5350"
            for i in range(len(df))
        ]
        fig.add_trace(go.Bar(
            x=df.index, y=df["Volume"], name="Volume",
            marker_color=vol_colors, opacity=0.35, showlegend=True,
        ), row=1, col=1, secondary_y=True)
        fig.update_yaxes(
            title_text="Volume", secondary_y=True,
            row=1, col=1, showgrid=False, tickfont=dict(size=9),
        )

    # ── Panel 2: RSI ──────────────────────────────────────────────────────
    if "RSI_14" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["RSI_14"], name="RSI (14)",
            line=dict(color="#ab47bc", width=1.5),
        ), row=2, col=1)
        # add_hline is safe here — y=70/30 are numeric, not datetime
        fig.add_hline(y=70, line_dash="dash", line_color="tomato", line_width=1, row=2, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="#26a69a", line_width=1, row=2, col=1)
        fig.add_hrect(
            y0=70, y1=100, fillcolor="tomato", opacity=0.07,
            line_width=0, row=2, col=1,
        )
        fig.add_hrect(
            y0=0, y1=30, fillcolor="#26a69a", opacity=0.07,
            line_width=0, row=2, col=1,
        )

    # ── Panel 3: MACD ─────────────────────────────────────────────────────
    if "MACD" in df.columns and "MACD_Signal" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["MACD"], name="MACD",
            line=dict(color="#1e88e5", width=1.5),
        ), row=3, col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["MACD_Signal"], name="MACD Signal",
            line=dict(color="#e53935", width=1.5),
        ), row=3, col=1)
        if "MACD_Hist" in df.columns:
            hist_colors = [
                "#26a69a" if v >= 0 else "#ef5350"
                for v in df["MACD_Hist"].fillna(0)
            ]
            fig.add_trace(go.Bar(
                x=df.index, y=df["MACD_Hist"], name="Histogram",
                marker_color=hist_colors, showlegend=False,
            ), row=3, col=1)

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f9fafb",
        font=dict(color="#111827"),
        height=800,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=30, t=60, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(gridcolor="#e5e7eb")
    fig.update_yaxes(gridcolor="#e5e7eb", title_text="Price", row=1, col=1, secondary_y=False)
    fig.update_yaxes(gridcolor="#e5e7eb", title_text="RSI",  row=2, col=1, range=[0, 100])
    fig.update_yaxes(gridcolor="#e5e7eb", title_text="MACD", row=3, col=1)
    return fig


def _build_forecast_fig(
    prophet_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    ticker: str,
    current_price: float,
    summary: dict,
) -> go.Figure:
    """Build the interactive forecast chart.

    Shows historical price, confidence interval, forecast line, today
    marker, current-price line, and price-target annotations.

    Args:
        prophet_df: Historical data with ``ds`` (datetime) and ``y`` columns.
        forecast_df: Future-only forecast with ``ds``, ``yhat``,
            ``yhat_lower``, ``yhat_upper``.
        ticker: Ticker symbol for title and annotations.
        current_price: Most recent closing price.
        summary: Output of :func:`_generate_forecast_summary_cb`.

    Returns:
        :class:`plotly.graph_objects.Figure` for use in a :class:`dcc.Graph`.
    """
    sym = _get_currency(ticker)
    sentiment_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}.get(
        summary.get("sentiment", ""), ""
    )

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=prophet_df["ds"], y=prophet_df["y"],
        name="Historical Price",
        line=dict(color="#1e88e5", width=2),
        mode="lines",
    ))

    fig.add_trace(go.Scatter(
        x=pd.concat([forecast_df["ds"], forecast_df["ds"].iloc[::-1]]),
        y=pd.concat([forecast_df["yhat_upper"], forecast_df["yhat_lower"].iloc[::-1]]),
        fill="toself",
        fillcolor="rgba(76,175,80,0.15)",
        line=dict(color="rgba(0,0,0,0)"),
        name="80% Confidence Interval",
    ))

    fig.add_trace(go.Scatter(
        x=forecast_df["ds"], y=forecast_df["yhat"],
        name="Forecast",
        line=dict(color="#4caf50", width=2, dash="dash"),
        mode="lines",
    ))

    # Today vertical line (use add_shape — Plotly 6.x datetime workaround)
    today_ts = pd.Timestamp(date.today())
    fig.add_shape(
        type="line",
        x0=today_ts, x1=today_ts, y0=0, y1=1,
        xref="x", yref="paper",
        line=dict(color="rgba(0,0,0,0.35)", width=1.5, dash="dot"),
    )
    fig.add_annotation(
        x=today_ts, y=1.02, yref="paper",
        text="Today", showarrow=False,
        font=dict(color="rgba(0,0,0,0.6)", size=10), xanchor="left",
    )

    # Current-price horizontal line
    fig.add_shape(
        type="line",
        x0=prophet_df["ds"].min(), x1=forecast_df["ds"].max(),
        y0=current_price, y1=current_price,
        xref="x", yref="y",
        line=dict(color="rgba(0,0,0,0.2)", width=1, dash="dot"),
    )
    fig.add_annotation(
        x=forecast_df["ds"].max(), y=current_price,
        text=f"Current: {sym}{current_price:.2f}",
        showarrow=False,
        font=dict(color="rgba(0,0,0,0.5)", size=10),
        xanchor="right", yanchor="bottom",
    )

    # Price-target annotations
    colors = {"3m": "#d97706", "6m": "#ea580c", "9m": "#dc2626"}
    for key, target in summary.get("targets", {}).items():
        sign = "+" if target["pct_change"] >= 0 else ""
        fig.add_annotation(
            x=target["date"], y=target["price"],
            text=f"{key}: {sym}{target['price']}<br>{sign}{target['pct_change']:.1f}%",
            showarrow=True, arrowhead=2,
            arrowcolor=colors.get(key, "#111827"),
            font=dict(color=colors.get(key, "#111827"), size=11),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor=colors.get(key, "#e5e7eb"),
            borderwidth=1,
        )

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f9fafb",
        font=dict(color="#111827"),
        title=dict(
            text=f"{ticker} — Price Forecast  {sentiment_emoji} {summary.get('sentiment','')}",
            font=dict(size=16),
        ),
        height=550,
        showlegend=True,
        margin=dict(l=60, r=30, t=80, b=50),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#e5e7eb")
    fig.update_yaxes(gridcolor="#e5e7eb")
    return fig


# ---------------------------------------------------------------------------
# Stats / card helpers
# ---------------------------------------------------------------------------


def _build_stats_cards(df: pd.DataFrame, ticker: str):
    """Build a row of six summary-stat Bootstrap cards for the analysis page.

    Args:
        df: Full OHLCV DataFrame with indicator columns added.
        ticker: Ticker symbol (used for logging only).

    Returns:
        :class:`dash_bootstrap_components.Row` containing six stat cards.
    """
    import dash_bootstrap_components as dbc
    from dash import html

    sym = _get_currency(ticker)
    close = df["Close"]
    daily_returns = close.pct_change().dropna()

    ath = round(float(close.max()), 2)
    atl = round(float(close.min()), 2)
    annual_ret = round(float(daily_returns.mean() * 252 * 100), 2)
    ann_vol = round(float(daily_returns.std() * math.sqrt(252) * 100), 2)

    rolling_max = close.cummax()
    drawdown = (close - rolling_max) / rolling_max
    max_dd = round(float(drawdown.min() * 100), 2)

    ann_vol_dec = daily_returns.std() * math.sqrt(252)
    sharpe = round(
        (daily_returns.mean() * 252 - 0.04) / ann_vol_dec
        if ann_vol_dec > 0 else 0.0,
        2,
    )

    stats = [
        ("All-Time High",  f"{sym}{ath:,}",     "text-success"),
        ("All-Time Low",   f"{sym}{atl:,}",     "text-danger"),
        ("Annual Return",  f"{annual_ret:+.1f}%",
         "text-success" if annual_ret >= 0 else "text-danger"),
        ("Max Drawdown",   f"{max_dd:.1f}%",  "text-danger"),
        ("Volatility",     f"{ann_vol:.1f}%", "text-warning"),
        ("Sharpe Ratio",   str(sharpe),       "text-info"),
    ]

    cols = []
    for label, value, color_cls in stats:
        cols.append(dbc.Col(
            dbc.Card(dbc.CardBody([
                html.Small(label, className="text-muted d-block"),
                html.Span(value, className=f"fs-5 fw-bold {color_cls}"),
            ]), className="stat-card h-100"),
            xs=6, md=4, lg=2, className="mb-3",
        ))
    return dbc.Row(cols)


def _build_target_cards(summary: dict, current_price: float, ticker: str = ""):
    """Build price-target cards for the forecast page.

    Args:
        summary: Dict produced by the forecast summary helper with a
            ``targets`` sub-dict keyed by ``"3m"``, ``"6m"``, ``"9m"``.
        current_price: Most recent closing price (for display).
        ticker: Ticker symbol used to look up the correct currency symbol.

    Returns:
        :class:`dash_bootstrap_components.Row` of price-target cards.
    """
    import dash_bootstrap_components as dbc
    from dash import html

    sym = _get_currency(ticker) if ticker else "$"
    targets = summary.get("targets", {})
    if not targets:
        return html.P("No price targets available.", className="text-muted")

    cols = []
    label_map = {"3m": "3 Month", "6m": "6 Month", "9m": "9 Month"}
    color_map = {"3m": "warning", "6m": "info", "9m": "danger"}

    for key in ["3m", "6m", "9m"]:
        t = targets.get(key)
        if not t:
            continue
        sign = "+" if t["pct_change"] >= 0 else ""
        text_color = "text-success" if t["pct_change"] >= 0 else "text-danger"
        cols.append(dbc.Col(
            dbc.Card([
                dbc.CardHeader(
                    label_map[key],
                    className=f"text-center bg-transparent border-{color_map[key]}",
                ),
                dbc.CardBody([
                    html.H5(f"{sym}{t['price']:,}", className="text-center mb-1"),
                    html.P(
                        f"{sign}{t['pct_change']:.1f}%",
                        className=f"text-center fw-bold mb-1 {text_color}",
                    ),
                    html.Small(
                        f"{sym}{t['lower']:,} – {sym}{t['upper']:,}",
                        className="text-muted d-block text-center",
                    ),
                ]),
            ], className=f"target-card border border-{color_map[key]}"),
            xs=12, sm=4, className="mb-3",
        ))

    return dbc.Row(cols)


def _build_accuracy_row(accuracy: dict, ticker: str = ""):
    """Build the model-accuracy metric cards for the forecast page.

    Args:
        accuracy: Dict with ``MAE``, ``RMSE``, ``MAPE_pct`` keys (or
            ``"error"`` key if accuracy could not be computed).
        ticker: Ticker symbol used to look up the correct currency symbol.

    Returns:
        :class:`dash_bootstrap_components.Row` or an error paragraph.
    """
    import dash_bootstrap_components as dbc
    from dash import html

    if "error" in accuracy:
        return html.P(f"Accuracy: {accuracy['error']}", className="text-muted small")

    sym = _get_currency(ticker) if ticker else "$"
    metrics = [
        ("MAE",  f"{sym}{accuracy['MAE']:,.2f}", "Mean Absolute Error"),
        ("RMSE", f"{sym}{accuracy['RMSE']:,.2f}", "Root Mean Square Error"),
        ("MAPE", f"{accuracy['MAPE_pct']:.1f}%", "Mean Abs % Error (lower = better)"),
    ]
    cols = [
        dbc.Col(
            dbc.Card(dbc.CardBody([
                html.Small(title, className="text-muted d-block"),
                html.Span(value, className="fs-5 fw-bold text-info"),
                html.Small(f" ({label})", className="text-muted"),
            ]), className="stat-card"),
            xs=12, sm=4, className="mb-3",
        )
        for label, value, title in metrics
    ]
    return dbc.Row(cols)


def _generate_forecast_summary_cb(
    forecast_df: pd.DataFrame,
    current_price: float,
    ticker: str,
    months: int,
) -> dict:
    """Compute price targets and sentiment from a forecast DataFrame.

    Args:
        forecast_df: Future-only forecast with ``ds``, ``yhat``, etc.
        current_price: Most recent closing price.
        ticker: Ticker symbol.
        months: Forecast horizon in months.

    Returns:
        Dict with ``targets`` sub-dict and ``sentiment`` string.
    """
    today = pd.Timestamp(date.today())
    targets = {}

    for m in [3, 6, 9]:
        if m > months:
            continue
        target_date = today + pd.DateOffset(months=m)
        idx = (forecast_df["ds"] - target_date).abs().idxmin()
        row = forecast_df.iloc[idx]
        price = float(row["yhat"])
        pct = (price - current_price) / current_price * 100
        targets[f"{m}m"] = {
            "date": str(row["ds"].date()),
            "price": round(price, 2),
            "pct_change": round(pct, 2),
            "lower": round(float(row["yhat_lower"]), 2),
            "upper": round(float(row["yhat_upper"]), 2),
        }

    last_key = (
        f"{min(months, 9)}m" if f"{min(months, 9)}m" in targets
        else ("6m" if "6m" in targets else "3m")
    )
    final_pct = targets.get(last_key, {}).get("pct_change", 0.0)
    sentiment = "Bullish" if final_pct > 10 else ("Bearish" if final_pct < -10 else "Neutral")

    return {"ticker": ticker, "current_price": current_price, "targets": targets, "sentiment": sentiment}


# ---------------------------------------------------------------------------
# Iceberg repository — lazy singleton for dashboard process
# ---------------------------------------------------------------------------

_DASH_REPO = None
_DASH_REPO_INIT_ATTEMPTED = False


def _get_iceberg_repo():
    """Return the module-level :class:`~stocks.repository.StockRepository` singleton.

    Initialised on first call; returns ``None`` when PyIceberg is unavailable.

    Returns:
        :class:`~stocks.repository.StockRepository` instance or ``None``.
    """
    global _DASH_REPO, _DASH_REPO_INIT_ATTEMPTED
    if _DASH_REPO_INIT_ATTEMPTED:
        return _DASH_REPO
    _DASH_REPO_INIT_ATTEMPTED = True
    try:
        from stocks.repository import StockRepository  # noqa: PLC0415
        _DASH_REPO = StockRepository()
        logger.debug("StockRepository initialised for dashboard")
    except Exception as _e:
        logger.warning("StockRepository unavailable in dashboard: %s", _e)
    return _DASH_REPO


# ---------------------------------------------------------------------------
# Public callback registration function
# ---------------------------------------------------------------------------


def register_callbacks(app) -> None:
    """Register all Dash callbacks with the application instance.

    Args:
        app: The :class:`~dash.Dash` application instance created in
            ``dashboard/app.py``.
    """
    import dash_bootstrap_components as dbc
    from dash import html

    # ======================================================================
    # Auth: extract JWT from ?token= query param and persist in client store
    # ======================================================================

    @app.callback(
        Output("auth-token-store", "data"),
        Input("url", "search"),
        prevent_initial_call=False,
    )
    def store_token_from_url(search: Optional[str]) -> Optional[str]:
        """Persist a JWT access token from the URL query string to localStorage.

        When the Next.js frontend embeds the dashboard in an ``<iframe>`` it
        appends ``?token=<jwt>`` to the URL.  This callback intercepts the
        query parameter and writes the token to the ``auth-token-store``
        (``storage_type="local"``), so it survives page navigation within
        the dashboard without the token re-appearing in the URL.

        If the URL does not contain a ``token`` parameter the callback
        returns :data:`~dash.no_update` so any previously stored value is
        preserved.

        Args:
            search: The query string portion of the current URL, e.g.
                ``"?token=eyJ..."``.  May be ``None`` or an empty string.

        Returns:
            The raw JWT string to store, or :data:`~dash.no_update` when
            the URL contains no ``token`` parameter.
        """
        if not search:
            return no_update
        qs = parse_qs(search.lstrip("?"))
        token_list = qs.get("token")
        if not token_list:
            return no_update
        return token_list[0]

    # ======================================================================
    # Home page: refresh stock cards + populate registry dropdown
    # ======================================================================

    @app.callback(
        [
            Output("stock-raw-data-store", "data"),
            Output("home-registry-dropdown", "options"),
        ],
        [
            Input("registry-refresh", "n_intervals"),
            Input("url", "pathname"),
        ],
    )
    def refresh_stock_cards(n_intervals, pathname):
        """Load stock data from the registry and store raw dicts for rendering.

        Fires on page load or interval tick.  Stores serialisable dicts so that
        ``render_home_cards`` can filter and paginate without repeating I/O.

        Args:
            n_intervals: Auto-refresh interval counter.
            pathname: Current URL path.

        Returns:
            Tuple of (list of raw card data dicts, dropdown options list).
        """
        registry = _load_reg_cb()
        if not registry:
            return [], []

        dropdown_options = [{"label": t, "value": t} for t in sorted(registry.keys())]
        card_data = []

        for ticker, entry in sorted(registry.items()):
            last_updated = entry.get("last_fetch_date", "Unknown")

            # Current price + 10Y return from parquet
            current_price_str = "N/A"
            total_return_str  = "N/A"
            return_color_cls  = "text-muted"
            try:
                df = _load_raw(ticker)
                if df is not None and len(df) > 1:
                    cp = float(df["Close"].iloc[-1])
                    fp = float(df["Close"].iloc[0])
                    tr = (cp / fp - 1) * 100
                    current_price_str = f"{_get_currency(ticker)}{cp:,.2f}"
                    total_return_str  = f"{tr:+.1f}%"
                    return_color_cls  = "text-success" if tr >= 0 else "text-danger"
            except Exception as exc:
                logger.warning("Card data error for %s: %s", ticker, exc)

            # Sentiment from forecast parquet
            sentiment  = "Unknown"
            sent_color = "secondary"
            sent_emoji = "⚪"
            try:
                forecast_files = list(_DATA_FORECASTS.glob(f"{ticker}_*m_forecast.parquet"))
                if forecast_files:
                    latest = max(forecast_files, key=lambda p: p.stat().st_mtime)
                    fc_df  = pd.read_parquet(latest, engine="pyarrow")
                    df_raw = _load_raw(ticker)
                    if df_raw is not None and len(fc_df) > 0:
                        cp  = float(df_raw["Close"].iloc[-1])
                        fp  = float(fc_df["yhat"].iloc[-1])
                        pct = (fp - cp) / cp * 100
                        if pct > 10:
                            sentiment, sent_color, sent_emoji = "Bullish", "success", "🟢"
                        elif pct < -10:
                            sentiment, sent_color, sent_emoji = "Bearish", "danger",  "🔴"
                        else:
                            sentiment, sent_color, sent_emoji = "Neutral", "warning", "🟡"
            except Exception as exc:
                logger.warning("Sentiment error for %s: %s", ticker, exc)

            # Company name from metadata JSON if available
            company   = ticker
            info_path = _DATA_METADATA / f"{ticker}_info.json"
            if info_path.exists():
                try:
                    with open(info_path) as fh:
                        info    = json.load(fh)
                        company = info.get("name", ticker) or ticker
                except Exception:
                    pass

            card_data.append({
                "ticker":            ticker,
                "company":           company,
                "current_price_str": current_price_str,
                "total_return_str":  total_return_str,
                "return_color_cls":  return_color_cls,
                "last_updated":      last_updated,
                "sentiment":         sentiment,
                "sent_color":        sent_color,
                "sent_emoji":        sent_emoji,
                "market":            _get_market(ticker),
            })

        return card_data, dropdown_options

    @app.callback(
        Output("market-filter-store", "data"),
        Output("filter-india-btn",    "color"),
        Output("filter-us-btn",       "color"),
        Output("home-pagination",     "active_page"),
        Input("filter-india-btn", "n_clicks"),
        Input("filter-us-btn",    "n_clicks"),
        prevent_initial_call=True,
    )
    def update_market_filter(india_clicks, us_clicks):
        """Toggle the market filter store between India and US stocks.

        Args:
            india_clicks: Click count on the India filter button.
            us_clicks: Click count on the US filter button.

        Returns:
            Tuple of (market string, india button color, us button color, reset page).
        """
        if ctx.triggered_id == "filter-us-btn":
            return "us", "outline-secondary", "primary", 1
        return "india", "primary", "outline-secondary", 1

    @app.callback(
        Output("home-pagination", "active_page", allow_duplicate=True),
        Input("home-page-size", "value"),
        prevent_initial_call=True,
    )
    def reset_home_page_on_size_change(page_size):
        """Reset home pagination to page 1 when the page size changes.

        Args:
            page_size: New page size value from the select dropdown.

        Returns:
            Integer ``1`` to reset the active page.
        """
        return 1

    @app.callback(
        Output("users-pagination", "active_page"),
        Input("users-search",    "value"),
        Input("users-page-size", "value"),
        prevent_initial_call=True,
    )
    def reset_users_page_on_filter(search, page_size):
        """Reset users pagination to page 1 when search or page size changes.

        Args:
            search: Current search input value.
            page_size: New page size value.

        Returns:
            Integer ``1`` to reset the active page.
        """
        return 1

    @app.callback(
        Output("audit-pagination", "active_page"),
        Input("audit-search",    "value"),
        Input("audit-page-size", "value"),
        prevent_initial_call=True,
    )
    def reset_audit_page_on_filter(search, page_size):
        """Reset audit log pagination to page 1 when search or page size changes.

        Args:
            search: Current search input value.
            page_size: New page size value.

        Returns:
            Integer ``1`` to reset the active page.
        """
        return 1

    @app.callback(
        Output("stock-cards-container", "children"),
        Output("home-pagination",       "max_value"),
        Output("home-count-text",       "children"),
        Input("stock-raw-data-store", "data"),
        Input("market-filter-store",  "data"),
        Input("home-pagination",      "active_page"),
        Input("home-page-size",       "value"),
    )
    def render_home_cards(raw_data, market_filter, active_page, page_size):
        """Filter, paginate, and render stock cards from stored raw data.

        Args:
            raw_data: List of raw card data dicts from ``stock-raw-data-store``.
            market_filter: Active market string — ``'india'`` or ``'us'``.
            active_page: Current pagination page (1-indexed).
            page_size: Number of cards per page as a string (e.g. ``"10"``).

        Returns:
            Tuple of (list of card columns, pagination max_value, count text).
        """
        PAGE_SIZE = int(page_size or 10)
        if not raw_data:
            return (
                [dbc.Col(html.P(
                    "No stocks saved yet. Analyse a stock via the chat interface first.",
                    className="text-muted",
                ))],
                1,
                "",
            )

        market   = market_filter or "india"
        page     = active_page or 1
        filtered = [d for d in raw_data if d.get("market") == market]

        if not filtered:
            label = "India (.NS / .BO)" if market == "india" else "US"
            return (
                [dbc.Col(html.P(f"No {label} stocks saved yet.", className="text-muted"))],
                1,
                "",
            )

        total     = len(filtered)
        max_pages = max(1, math.ceil(total / PAGE_SIZE))
        page      = min(page, max_pages)
        start     = (page - 1) * PAGE_SIZE
        page_data = filtered[start: start + PAGE_SIZE]
        count_txt = f"Showing {start + 1}–{min(start + PAGE_SIZE, total)} of {total}"

        cols = []
        for d in page_data:
            card = html.A(
                href=f"/analysis?ticker={d['ticker']}",
                className="text-decoration-none",
                children=dbc.Card([
                    dbc.CardBody([
                        html.Div([
                            html.H5(d["ticker"], className="card-title text-info mb-0"),
                            dbc.Badge(
                                f"{d['sent_emoji']} {d['sentiment']}",
                                color=d["sent_color"],
                                className="ms-auto",
                            ),
                        ], className="d-flex justify-content-between align-items-center mb-1"),
                        html.P(d["company"], className="card-subtitle text-muted small mb-3"),
                        html.Div([
                            html.Div([
                                html.Small("Price", className="text-muted d-block"),
                                html.Strong(d["current_price_str"], className="text-dark"),
                            ], className="me-3"),
                            html.Div([
                                html.Small("10Y Return", className="text-muted d-block"),
                                html.Strong(d["total_return_str"], className=d["return_color_cls"]),
                            ], className="me-3"),
                            html.Div([
                                html.Small("Updated", className="text-muted d-block"),
                                html.Small(d["last_updated"], className="text-muted"),
                            ]),
                        ], className="d-flex align-items-start"),
                    ]),
                ], className="stock-card h-100"),
            )
            cols.append(dbc.Col(card, xs=12, sm=6, md=4, lg=3, className="mb-4"))

        return cols, max_pages, count_txt

    # ======================================================================
    # Home page: navigate to analysis page on search / dropdown select
    # ======================================================================

    @app.callback(
        [Output("url", "pathname"), Output("nav-ticker-store", "data")],
        [
            Input("search-btn", "n_clicks"),
            Input("home-registry-dropdown", "value"),
        ],
        [State("ticker-search-input", "value")],
        prevent_initial_call=True,
    )
    def navigate_to_analysis(search_clicks, dropdown_val, search_input):
        """Navigate to the analysis page when the user selects or searches a ticker.

        Args:
            search_clicks: Number of times the Analyse button was clicked.
            dropdown_val: Selected value from the home-page dropdown.
            search_input: Text entered in the ticker search input.

        Returns:
            Tuple of (new URL pathname, ticker to store for pre-selection).
        """
        triggered = ctx.triggered_id
        if triggered == "search-btn":
            if not search_input:
                return no_update, no_update
            return "/analysis", search_input.upper().strip()
        if triggered == "home-registry-dropdown" and dropdown_val:
            return "/analysis", dropdown_val
        return no_update, no_update

    # ======================================================================
    # Analysis page: sync dropdown from URL query param or nav store
    # ======================================================================

    @app.callback(
        Output("analysis-ticker-dropdown", "value"),
        [Input("url", "search"), Input("url", "pathname")],
        State("nav-ticker-store", "data"),
    )
    def sync_analysis_ticker(search, pathname, stored_ticker):
        """Pre-select the analysis dropdown when navigating from a stock card.

        Args:
            search: URL query string (e.g. ``"?ticker=AAPL"``).
            pathname: Current URL path.
            stored_ticker: Ticker stored via the home-page search or dropdown.

        Returns:
            Ticker string to set as dropdown value, or :data:`~dash.no_update`.
        """
        if pathname != "/analysis":
            return no_update
        if search:
            params = parse_qs(search.lstrip("?"))
            t = params.get("ticker", [None])[0]
            if t:
                return t.upper()
        if stored_ticker:
            return stored_ticker
        tickers = sorted(_load_reg_cb().keys())
        return tickers[0] if tickers else no_update

    # ======================================================================
    # Analysis page: update chart and stats when inputs change
    # ======================================================================

    @app.callback(
        [Output("analysis-chart", "figure"), Output("analysis-stats-row", "children")],
        [
            Input("analysis-ticker-dropdown", "value"),
            Input("date-range-slider", "value"),
            Input("overlay-toggles", "value"),
        ],
        State("auth-token-store", "data"),
    )
    def update_analysis_chart(ticker, date_range_idx, overlays, token):
        """Rebuild the 3-panel analysis chart and summary-stat cards.

        Args:
            ticker: Selected ticker from the dropdown.
            date_range_idx: Integer index into the date-range map
                (0=1M, 1=3M, 2=6M, 3=1Y, 4=3Y, 5=Max).
            overlays: List of active overlay keys.
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (analysis figure, stats row component).
        """
        if _validate_token(token) is None:
            return _empty_fig("Authentication required."), _unauth_notice()

        if not ticker:
            return _empty_fig("Select a ticker to begin."), []

        df = _load_raw(ticker)
        if df is None:
            return _empty_fig(f"No data found for '{ticker}'. Fetch data via the chat interface."), []

        # Calculate indicators on full df (needs 200+ rows for SMA 200)
        df_full = _add_indicators(df)

        # Apply date-range filter
        n_map = {0: 21, 1: 63, 2: 126, 3: 252, 4: 756, 5: len(df_full)}
        n_days = n_map.get(date_range_idx if date_range_idx is not None else 5, len(df_full))
        df_plot = df_full.tail(n_days).copy()

        overlays = overlays or []
        fig = _build_analysis_fig(df_plot, ticker, overlays)
        stats = _build_stats_cards(df_full, ticker)
        return fig, stats

    # ======================================================================
    # Forecast page: sync dropdown from nav store
    # ======================================================================

    @app.callback(
        Output("forecast-ticker-dropdown", "value"),
        [Input("url", "search"), Input("url", "pathname")],
        State("nav-ticker-store", "data"),
    )
    def sync_forecast_ticker(search, pathname, stored_ticker):
        """Pre-select the forecast dropdown when navigating from a stock card.

        Args:
            search: URL query string.
            pathname: Current URL path.
            stored_ticker: Ticker stored via the nav store.

        Returns:
            Ticker string or :data:`~dash.no_update`.
        """
        if pathname != "/forecast":
            return no_update
        if search:
            params = parse_qs(search.lstrip("?"))
            t = params.get("ticker", [None])[0]
            if t:
                return t.upper()
        if stored_ticker:
            return stored_ticker
        return no_update

    # ======================================================================
    # Forecast page: update chart when ticker / horizon / refresh changes
    # ======================================================================

    @app.callback(
        [
            Output("forecast-chart", "figure"),
            Output("forecast-target-cards", "children"),
            Output("forecast-accuracy-row", "children"),
        ],
        [
            Input("forecast-ticker-dropdown", "value"),
            Input("forecast-horizon-radio", "value"),
            Input("forecast-refresh-store", "data"),
        ],
        State("auth-token-store", "data"),
    )
    def update_forecast_chart(ticker, horizon, refresh_trigger, token):
        """Reload and render the forecast chart when inputs change.

        Args:
            ticker: Selected ticker from the dropdown.
            horizon: Forecast horizon string (``"3"``, ``"6"``, ``"9"``).
            refresh_trigger: Counter incremented by the Run New Analysis
                callback to force a chart refresh.
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (forecast figure, target-cards component,
            accuracy-row component).
        """
        if _validate_token(token) is None:
            return _empty_fig("Authentication required."), _unauth_notice(), []

        if not ticker:
            return _empty_fig("Select a ticker to begin."), [], []

        horizon_months = int(horizon) if horizon else 9

        df_raw = _load_raw(ticker)
        if df_raw is None:
            return _empty_fig(f"No price data for '{ticker}'."), [], []

        # Build prophet-format historical series
        price_col = "Adj Close" if "Adj Close" in df_raw.columns else "Close"
        prophet_df = pd.DataFrame({
            "ds": pd.to_datetime(df_raw.index).tz_localize(None),
            "y":  df_raw[price_col].values,
        }).dropna(subset=["y"]).sort_values("ds")
        current_price = float(prophet_df["y"].iloc[-1])

        forecast_df = _load_forecast(ticker, horizon_months)
        if forecast_df is None:
            msg = (
                f"No forecast found for '{ticker}'. "
                "Click 'Run New Analysis' to generate one."
            )
            return _empty_fig(msg, height=550), [], [
                html.P(msg, className="text-muted small")
            ]

        # Trim to requested horizon
        cutoff = pd.Timestamp.now() + pd.DateOffset(months=horizon_months)
        forecast_df = forecast_df[forecast_df["ds"] <= cutoff].copy()

        summary = _generate_forecast_summary_cb(forecast_df, current_price, ticker, horizon_months)
        fig = _build_forecast_fig(prophet_df, forecast_df, ticker, current_price, summary)

        target_cards  = _build_target_cards(summary, current_price, ticker)
        accuracy_note = [html.P(
            "Model accuracy metrics are computed when you click 'Run New Analysis'.",
            className="text-muted small",
        )]
        return fig, target_cards, accuracy_note

    # ======================================================================
    # Forecast page: Run New Analysis button
    # ======================================================================

    @app.callback(
        [
            Output("run-analysis-status", "children"),
            Output("forecast-refresh-store", "data"),
            Output("forecast-accuracy-row", "children", allow_duplicate=True),
        ],
        Input("run-analysis-btn", "n_clicks"),
        [
            State("forecast-ticker-dropdown", "value"),
            State("forecast-horizon-radio", "value"),
            State("forecast-refresh-store", "data"),
            State("auth-token-store", "data"),
        ],
        prevent_initial_call=True,
    )
    def run_new_analysis(n_clicks, ticker, horizon, current_refresh, token):
        """Run the full fetch → Prophet forecast pipeline for the selected ticker.

        Imports backend tool functions directly (no HTTP call to the backend
        API).  Increments the ``forecast-refresh-store`` counter on success
        to trigger a chart reload.

        Args:
            n_clicks: Button click counter.
            ticker: Selected ticker symbol.
            horizon: Forecast horizon string.
            current_refresh: Current store value (incremented on success).
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (status message, new refresh counter,
            accuracy-row component).
        """
        if _validate_token(token) is None:
            return _unauth_notice(), no_update, []

        if not ticker:
            return (
                dbc.Alert("Please select a ticker first.", color="warning"),
                no_update, [],
            )

        horizon_months = int(horizon) if horizon else 9
        ticker = ticker.upper().strip()

        try:
            # ── Step 1: Fetch / delta-update price data ────────────────────
            from backend.tools.stock_data_tool import fetch_stock_data
            fetch_result = fetch_stock_data.invoke({"ticker": ticker})
            logger.info("fetch_stock_data result: %s", fetch_result[:80])

            # ── Step 2: Run Prophet forecast pipeline ──────────────────────
            from backend.tools.forecasting_tool import (
                _load_parquet as _ft_load,
                _prepare_data_for_prophet,
                _train_prophet_model,
                _generate_forecast,
                _calculate_forecast_accuracy,
                _generate_forecast_summary,
                _save_forecast,
            )

            df = _ft_load(ticker)
            if df is None:
                raise ValueError(f"No data loaded for {ticker} after fetch.")

            prophet_df = _prepare_data_for_prophet(df)
            current_price = float(prophet_df["y"].iloc[-1])

            logger.info("Training Prophet model for %s (%dm)…", ticker, horizon_months)
            model       = _train_prophet_model(prophet_df)
            forecast_df = _generate_forecast(model, prophet_df, horizon_months)
            accuracy    = _calculate_forecast_accuracy(model, prophet_df)
            _save_forecast(forecast_df, ticker, horizon_months)

            logger.info("New analysis complete for %s.", ticker)

            acc_row = _build_accuracy_row(accuracy, ticker)
            status  = dbc.Alert(
                f"Analysis complete for {ticker}. Forecast updated.",
                color="success", duration=5000,
            )
            return status, (current_refresh or 0) + 1, acc_row

        except Exception as exc:
            logger.error("run_new_analysis error: %s", exc, exc_info=True)
            return (
                dbc.Alert(f"Error: {exc}", color="danger"),
                no_update, [],
            )

    # ======================================================================
    # Compare page: update all three charts / table when selection changes
    # ======================================================================

    @app.callback(
        [
            Output("compare-perf-chart",       "figure"),
            Output("compare-metrics-container", "children"),
            Output("compare-heatmap",           "figure"),
        ],
        Input("compare-ticker-dropdown", "value"),
        State("auth-token-store", "data"),
    )
    def update_compare(tickers, token):
        """Build the normalised performance chart, metrics table, and heatmap.

        Args:
            tickers: List of selected ticker symbols (2–5).
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (performance figure, metrics table component,
            heatmap figure).
        """
        if _validate_token(token) is None:
            empty = _empty_fig("Authentication required.", height=450)
            return empty, _unauth_notice(), _empty_fig("", height=380)

        empty_perf = _empty_fig("Select 2–5 stocks to compare.", height=450)
        empty_heat = _empty_fig("", height=380)

        if not tickers or len(tickers) < 2:
            return empty_perf, html.P("Select at least 2 stocks.", className="text-muted"), empty_heat

        # ── Load data ─────────────────────────────────────────────────────
        dfs = {}
        for t in tickers[:5]:
            df = _load_raw(t)
            if df is not None and len(df) > 1:
                dfs[t] = df

        if len(dfs) < 2:
            return (
                _empty_fig("Could not load data for 2 or more selected stocks.", 450),
                html.P("Data unavailable.", className="text-muted"),
                empty_heat,
            )

        # ── Common start date ─────────────────────────────────────────────
        common_start = max(df.index.min() for df in dfs.values())
        aligned = {t: df[df.index >= common_start]["Close"] for t, df in dfs.items()}

        # ── Normalised performance chart ──────────────────────────────────
        perf_fig = go.Figure()
        final_values = {}
        for t, series in aligned.items():
            norm = (series / series.iloc[0]) * 100
            perf_fig.add_trace(go.Scatter(
                x=norm.index, y=norm, name=t, mode="lines", line=dict(width=2),
            ))
            final_values[t] = float(norm.iloc[-1])

        best_ticker = max(final_values, key=final_values.get)
        perf_fig.update_layout(
            template="plotly_white", height=450,
            paper_bgcolor="#ffffff", plot_bgcolor="#f9fafb",
            font=dict(color="#111827"),
            title=dict(text="Normalised Performance (Base = 100)", font=dict(size=15)),
            yaxis_title="Value (Base 100)",
            margin=dict(l=60, r=30, t=60, b=40),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=1, xanchor="right"),
        )
        perf_fig.update_xaxes(gridcolor="#e5e7eb")
        perf_fig.update_yaxes(gridcolor="#e5e7eb")

        # ── Metrics table ─────────────────────────────────────────────────
        rows = []
        for t in sorted(dfs.keys()):
            df = dfs[t]
            df_ind = _add_indicators(df)
            close = df["Close"]
            daily = close.pct_change().dropna()
            ann_vol   = daily.std() * math.sqrt(252)
            ann_ret   = daily.mean() * 252
            sharpe    = round((ann_ret - 0.04) / ann_vol if ann_vol > 0 else 0.0, 2)
            rm        = close.cummax()
            dd        = (close - rm) / rm
            max_dd    = round(float(dd.min() * 100), 2)
            rsi_val   = (
                round(float(df_ind["RSI_14"].iloc[-1]), 1)
                if "RSI_14" in df_ind.columns else "N/A"
            )
            macd_val = df_ind["MACD"].iloc[-1] if "MACD" in df_ind.columns else None
            msig_val = df_ind["MACD_Signal"].iloc[-1] if "MACD_Signal" in df_ind.columns else None
            macd_sig = (
                "Bullish" if (macd_val is not None and msig_val is not None and macd_val > msig_val)
                else "Bearish"
            )

            # 6-month forecast upside
            fc_df = _load_forecast(t, 6)
            if fc_df is not None and len(fc_df) > 0:
                cp         = float(close.iloc[-1])
                fp         = float(fc_df["yhat"].iloc[-1])
                fc_upside  = f"{(fp - cp)/cp*100:+.1f}%"
                fc_sent    = "Bullish" if (fp - cp)/cp*100 > 10 else ("Bearish" if (fp - cp)/cp*100 < -10 else "Neutral")
            else:
                fc_upside = "N/A"
                fc_sent   = "N/A"

            badge = "🏆 " if t == best_ticker else ""
            rows.append({
                "Ticker":      f"{badge}{t}",
                "Annual Ret":  f"{ann_ret*100:+.1f}%",
                "Volatility":  f"{ann_vol*100:.1f}%",
                "Sharpe":      str(sharpe),
                "Max Drawdown":f"{max_dd:.1f}%",
                "RSI":         str(rsi_val),
                "MACD":        macd_sig,
                "6M Upside":   fc_upside,
                "Sentiment":   fc_sent,
            })

        metrics_df   = pd.DataFrame(rows)
        header_cells = [html.Th(col, className="text-muted small") for col in metrics_df.columns]
        body_rows    = []
        for _, row in metrics_df.iterrows():
            cells = [html.Td(str(v), className="small") for v in row]
            body_rows.append(html.Tr(cells))

        table = dbc.Table(
            [html.Thead(html.Tr(header_cells)), html.Tbody(body_rows)],
            bordered=True, hover=True, responsive=True,
            className="table table-sm mt-2",
        )

        # ── Correlation heatmap ────────────────────────────────────────────
        returns_dict = {t: aligned[t].pct_change().dropna() for t in aligned}
        corr = pd.DataFrame(returns_dict).corr()

        heat_fig = go.Figure(go.Heatmap(
            z=corr.values,
            x=list(corr.columns),
            y=list(corr.index),
            colorscale="RdBu",
            zmid=0,
            zmin=-1, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in corr.values],
            texttemplate="%{text}",
            showscale=True,
        ))
        heat_fig.update_layout(
            template="plotly_white", height=380,
            paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
            font=dict(color="#111827"),
            margin=dict(l=60, r=10, t=40, b=40),
            title=dict(text="Daily Returns Correlation", font=dict(size=13)),
        )

        return perf_fig, table, heat_fig

    # ======================================================================
    # Admin: User Management
    # ======================================================================

    @app.callback(
        Output("users-store", "data"),
        Input("url", "pathname"),
        Input("users-refresh-store", "data"),
        State("auth-token-store", "data"),
        State("url", "search"),
        prevent_initial_call=False,
    )
    def load_users_table(
        pathname: Optional[str],
        _refresh: Optional[int],
        stored_token: Optional[str],
        url_search: Optional[str],
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
        resp  = _api_call("get", "/users", token)
        if resp is None or not resp.ok:
            return []

        return resp.json()

    @app.callback(
        Output("users-table-container", "children"),
        Output("users-pagination",      "max_value"),
        Output("users-count-text",      "children"),
        Input("users-store",      "data"),
        Input("users-pagination", "active_page"),
        Input("users-search",     "value"),
        Input("users-page-size",  "value"),
    )
    def render_users_page(users_data, active_page, search_term, page_size):
        """Filter, slice, and render one page of the users table.

        Args:
            users_data: Full list of user dicts from ``users-store``.
            active_page: Current pagination page (1-indexed).
            search_term: Debounced search text (name, email or role).
            page_size: Number of rows per page as a string (e.g. ``"10"``).

        Returns:
            Tuple of (table component, pagination max_value, count text).
        """
        PAGE_SIZE = int(page_size or 10)
        users     = users_data or []

        # Apply search filter
        q = (search_term or "").strip().lower()
        if q:
            users = [
                u for u in users
                if q in (u.get("full_name") or "").lower()
                or q in (u.get("email") or "").lower()
                or q in (u.get("role") or "").lower()
            ]

        total = len(users)
        if total == 0:
            msg = "No matching users found." if q else "No user accounts found."
            return html.P(msg, className="text-muted"), 1, ""
        max_pages = max(1, math.ceil(total / PAGE_SIZE))
        page      = min(active_page or 1, max_pages)
        start     = (page - 1) * PAGE_SIZE
        count_txt = f"Showing {start + 1}–{min(start + PAGE_SIZE, total)} of {total} users"
        return _build_users_table(users[start: start + PAGE_SIZE]), max_pages, count_txt

    @app.callback(
        Output("audit-data-store", "data"),
        Input("admin-tabs", "active_tab"),
        Input("url", "pathname"),
        State("auth-token-store", "data"),
        State("url", "search"),
        prevent_initial_call=False,
    )
    def load_audit_log(
        active_tab: Optional[str],
        pathname: Optional[str],
        stored_token: Optional[str],
        url_search: Optional[str],
    ):
        """Fetch the audit log from the backend API and store the raw event list.

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
        resp  = _api_call("get", "/admin/audit-log", token)
        if resp is None or not resp.ok:
            return []

        return resp.json().get("events", [])

    @app.callback(
        Output("audit-log-container", "children"),
        Output("audit-pagination",    "max_value"),
        Output("audit-count-text",    "children"),
        Input("audit-data-store",  "data"),
        Input("audit-pagination",  "active_page"),
        Input("audit-search",      "value"),
        Input("audit-page-size",   "value"),
    )
    def render_audit_page(audit_data, active_page, search_term, page_size):
        """Filter, slice, and render one page of the audit log table.

        Args:
            audit_data: Full list of audit event dicts from ``audit-data-store``.
            active_page: Current pagination page (1-indexed).
            search_term: Debounced search text (event type, actor/target ID, details).
            page_size: Number of rows per page as a string (e.g. ``"10"``).

        Returns:
            Tuple of (table component, pagination max_value, count text).
        """
        PAGE_SIZE = int(page_size or 10)
        events    = audit_data or []

        # Apply search filter
        q = (search_term or "").strip().lower()
        if q:
            events = [
                e for e in events
                if q in (e.get("event_type") or "").lower()
                or q in (e.get("actor_user_id") or "").lower()
                or q in (e.get("target_user_id") or "").lower()
                or q in (e.get("metadata") or "").lower()
            ]

        total = len(events)
        if total == 0:
            msg = "No matching events found." if q else "No audit events found."
            return html.P(msg, className="text-muted"), 1, ""
        max_pages = max(1, math.ceil(total / PAGE_SIZE))
        page      = min(active_page or 1, max_pages)
        start     = (page - 1) * PAGE_SIZE
        count_txt = f"Showing {start + 1}–{min(start + PAGE_SIZE, total)} of {total} events"
        return _build_audit_table(events[start: start + PAGE_SIZE]), max_pages, count_txt

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
            store data, and error message.
        """
        triggered = ctx.triggered_id
        triggered_value = ctx.triggered[0]["value"] if ctx.triggered else None

        # ── Cancel ────────────────────────────────────────────────────────
        if triggered == "modal-cancel-btn":
            return (
                False, no_update, no_update, no_update,
                no_update, no_update, no_update, no_update,
                no_update, "",
            )

        # ── Add user ──────────────────────────────────────────────────────
        if triggered == "add-user-btn":
            return (
                True, "Add User",
                "", "", "general", ["active"],
                {},                        # show password row
                {"display": "none"},       # hide is-active toggle
                {"mode": "add", "user": None},
                "",
            )

        # ── Edit user ─────────────────────────────────────────────────────
        if isinstance(triggered, dict) and triggered.get("type") == "edit-user-btn":
            # triggered_value is None when Dash fires due to DOM injection
            # (pattern-match re-fires when Edit buttons are added to the layout)
            # rather than an actual user click.  Skip in that case.
            if not triggered_value:
                return (
                    no_update, no_update, no_update, no_update,
                    no_update, no_update, no_update, no_update,
                    no_update, no_update,
                )
            user_id = triggered["index"]
            user = next(
                (u for u in (users_data or []) if u.get("user_id") == user_id),
                None,
            )
            if user is None:
                return (
                    no_update, no_update, no_update, no_update,
                    no_update, no_update, no_update, no_update,
                    no_update, "User data not found — try refreshing.",
                )
            return (
                True, f"Edit User — {user.get('email', '')}",
                user.get("full_name", ""),
                user.get("email", ""),
                user.get("role", "general"),
                ["active"] if user.get("is_active", True) else [],
                {"display": "none"},       # hide password row for edits
                {},                        # show is-active toggle
                {"mode": "edit", "user": user},
                "",
            )

        return (
            no_update, no_update, no_update, no_update,
            no_update, no_update, no_update, no_update,
            no_update, no_update,
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
        refresh_n: Optional[int],
        stored_token: Optional[str],
        url_search: Optional[str],
    ):
        """Create or update a user via the backend API.

        Validates required fields locally, then calls ``POST /users`` (add
        mode) or ``PATCH /users/{user_id}`` (edit mode).  On success the
        modal is closed and the user table is refreshed.  On error the modal
        stays open and an inline error message is shown.

        Args:
            n_clicks: Save button click count.
            modal_data: Dict with ``mode`` (``"add"``/``"edit"``) and
                ``user`` (the original user dict for edits).
            full_name: Form value.
            email: Form value.
            password: Form value (only used in add mode).
            role: Selected role value.
            is_active_list: Checklist value — ``["active"]`` or ``[]``.
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

        if mode == "add":
            if not (password and password.strip()):
                return True, no_update, "Password is required for new users.", no_update
            payload: Dict[str, Any] = {
                "full_name": full_name.strip(),
                "email": email.strip(),
                "password": password,
                "role": role or "general",
            }
            resp = _api_call("post", "/users", token, json_body=payload)
        else:
            user = (modal_data or {}).get("user") or {}
            user_id = user.get("user_id", "")
            if not user_id:
                return True, no_update, "Cannot determine user ID.", no_update
            updates: Dict[str, Any] = {
                "full_name": full_name.strip(),
                "email": email.strip(),
                "role": role or "general",
                "is_active": "active" in (is_active_list or []),
            }
            resp = _api_call("patch", f"/users/{user_id}", token, json_body=updates)

        if resp is None:
            return True, no_update, "Could not reach backend.", no_update
        if resp.status_code == 400:
            detail = resp.json().get("detail", "Bad request")
            return True, no_update, str(detail), no_update
        if resp.status_code == 409:
            return True, no_update, "Email already in use.", no_update
        if not resp.ok:
            return True, no_update, f"Error {resp.status_code}.", no_update

        verb = "created" if mode == "add" else "updated"
        alert = dbc.Alert(
            f"User {verb} successfully.",
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
        if not isinstance(triggered, dict) or triggered.get("type") != "toggle-user-btn":
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
                "User not found — refresh the page.", color="warning",
                dismissable=True, duration=4000, className="py-2",
            )

        token = _resolve_token(stored_token, url_search)
        is_active = user.get("is_active", True)

        if is_active:
            resp = _api_call("delete", f"/users/{user_id}", token)
            action = "deactivated"
        else:
            resp = _api_call("patch", f"/users/{user_id}", token, json_body={"is_active": True})
            action = "reactivated"

        if resp is None or not resp.ok:
            err = "" if resp is None else f" ({resp.status_code})"
            return no_update, dbc.Alert(
                f"Action failed{err}.", color="danger",
                dismissable=True, duration=4000, className="py-2",
            )

        alert = dbc.Alert(
            f"User {action} successfully.",
            color="success" if action == "reactivated" else "warning",
            dismissable=True,
            duration=4000,
            className="py-2",
        )
        return (refresh_n or 0) + 1, alert

    # ======================================================================
    # Global: Change Password modal (accessible from NAVBAR on any page)
    # ======================================================================

    @app.callback(
        Output("change-password-modal", "is_open"),
        Output("change-pw-new", "value"),
        Output("change-pw-confirm", "value"),
        Output("change-pw-error", "children"),
        Input("open-change-password-btn", "n_clicks"),
        Input("change-pw-cancel-btn", "n_clicks"),
        State("change-password-modal", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_change_password_modal(
        open_clicks: Optional[int],
        cancel_clicks: Optional[int],
        is_open: bool,
    ):
        """Open or close the Change Password modal.

        Clears all form fields and errors when opening or cancelling.

        Args:
            open_clicks: ``n_clicks`` from the "Change Password" NAVBAR button.
            cancel_clicks: ``n_clicks`` from the modal Cancel button.
            is_open: Current modal open state.

        Returns:
            Tuple of (is_open, new password cleared, confirm cleared, error cleared).
        """
        triggered = ctx.triggered_id
        if triggered == "open-change-password-btn":
            return True, "", "", ""
        # Cancel
        return False, "", "", ""

    @app.callback(
        Output("change-password-modal", "is_open", allow_duplicate=True),
        Output("change-pw-error", "children", allow_duplicate=True),
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
            "post", "/auth/password-reset/request", token,
            json_body={"email": email},
        )
        if resp1 is None or not resp1.ok:
            detail = "" if resp1 is None else resp1.json().get("detail", "")
            return True, f"Request failed: {detail or 'backend unreachable'}."

        reset_token = resp1.json().get("reset_token", "")
        if not reset_token:
            return True, "No reset token returned by server."

        # Step 2: confirm with new password
        resp2 = _api_call(
            "post", "/auth/password-reset/confirm", token,
            json_body={"reset_token": reset_token, "new_password": new_pw},
        )
        if resp2 is None or not resp2.ok:
            detail = "" if resp2 is None else resp2.json().get("detail", "")
            return True, f"Confirm failed: {detail or 'backend unreachable'}."

        return False, ""

    # ── Insights page callbacks (Iceberg-backed) ─────────────────────────────

    # ── Screener ─────────────────────────────────────────────────────────────

    @app.callback(
        Output("screener-table-container", "children"),
        Input("screener-rsi-filter", "value"),
        Input("screener-market-filter", "value"),
    )
    def update_screener(rsi_filter: str, market_filter: str) -> Any:
        """Populate the screener table from stocks.analysis_summary.

        Args:
            rsi_filter: RSI filter value (``"all"``, ``"oversold"``, etc.).
            market_filter: Market filter value (``"all"``, ``"india"``, ``"us"``).

        Returns:
            Dash table component or an alert when no data is available.
        """
        repo = _get_iceberg_repo()
        df = pd.DataFrame()

        if repo is not None:
            df = repo.get_all_latest_analysis_summary()

        # Fallback: compute from flat parquet files if Iceberg table is empty
        if df.empty:
            rows = []
            try:
                import json as _json
                registry_path = _REGISTRY_PATH
                registry = {}
                if registry_path.exists():
                    with open(registry_path) as _f:
                        registry = _json.load(_f)
                from price_analysis_tool import (  # noqa: PLC0415
                    _calculate_technical_indicators,
                    _analyse_price_movement,
                    _generate_summary_stats,
                )
                for ticker in sorted(registry.keys()):
                    parquet_path = _DATA_RAW / f"{ticker}_raw.parquet"
                    if not parquet_path.exists():
                        continue
                    try:
                        _df = pd.read_parquet(parquet_path, engine="pyarrow")
                        _df.index = pd.to_datetime(_df.index).tz_localize(None)
                        _df = _calculate_technical_indicators(_df)
                        movement = _analyse_price_movement(_df)
                        stats = _generate_summary_stats(_df, ticker)
                        rows.append({
                            "ticker": ticker,
                            "current_price": stats.get("current_price"),
                            "rsi_14": stats.get("rsi_14"),
                            "rsi_signal": stats.get("rsi_signal"),
                            "macd_signal_text": stats.get("macd_signal"),
                            "sma_200_signal": stats.get("sma_200_signal"),
                            "sharpe_ratio": movement.get("sharpe_ratio"),
                            "annualized_return_pct": movement.get("annualized_return_pct"),
                            "annualized_volatility_pct": movement.get("annualized_volatility_pct"),
                        })
                    except Exception as _e:
                        logger.debug("Screener fallback failed for %s: %s", ticker, _e)
            except Exception as _e:
                logger.warning("Screener fallback import failed: %s", _e)
            if rows:
                df = pd.DataFrame(rows)

        if df.empty:
            return dbc.Alert(
                "No analysis data available. Analyse stocks via the chat agent first.",
                color="warning",
                className="mt-3",
            )

        # Market filter using registry
        if market_filter != "all":
            def _mkt(t: str) -> str:
                return "india" if str(t).upper().endswith((".NS", ".BO")) else "us"
            df = df[df["ticker"].apply(_mkt) == market_filter]
            df = df.reset_index(drop=True)

        # RSI filter — prefer numeric rsi_14 (fallback path) else text rsi_signal (Iceberg path)
        if rsi_filter != "all":
            if "rsi_14" in df.columns:
                rsi_num = pd.to_numeric(df["rsi_14"], errors="coerce")
                if rsi_filter == "oversold":
                    df = df[rsi_num.lt(30).values]
                elif rsi_filter == "overbought":
                    df = df[rsi_num.gt(70).values]
                elif rsi_filter == "neutral":
                    df = df[(rsi_num.ge(30) & rsi_num.le(70)).values]
            elif "rsi_signal" in df.columns:
                sig = df["rsi_signal"].str.lower().fillna("")
                if rsi_filter == "oversold":
                    df = df[sig.str.contains("oversold").values]
                elif rsi_filter == "overbought":
                    df = df[sig.str.contains("overbought").values]
                elif rsi_filter == "neutral":
                    df = df[sig.eq("neutral").values]

        if df.empty:
            return dbc.Alert("No stocks match the selected filters.", color="info", className="mt-3")

        # Build display table
        cols_map = {
            "ticker": "Ticker",
            "current_price": "Price",
            "rsi_14": "RSI (14)",
            "rsi_signal": "RSI Signal",
            "macd_signal_text": "MACD",
            "sma_200_signal": "vs SMA 200",
            "annualized_return_pct": "Ann. Return %",
            "annualized_volatility_pct": "Volatility %",
            "sharpe_ratio": "Sharpe",
        }
        display_cols = [c for c in cols_map if c in df.columns]
        display_df = df[display_cols].copy()
        display_df.columns = [cols_map[c] for c in display_cols]

        for num_col in ["Price", "RSI (14)", "Ann. Return %", "Volatility %", "Sharpe"]:
            if num_col in display_df.columns:
                display_df[num_col] = pd.to_numeric(
                    display_df[num_col], errors="coerce"
                ).round(2)

        rows_html = []
        for _, row in display_df.iterrows():
            cells = []
            for col, val in row.items():
                badge_class = ""
                if col == "RSI Signal":
                    badge_class = (
                        "badge bg-danger" if val == "Overbought" else
                        "badge bg-success" if val == "Oversold" else
                        "badge bg-secondary"
                    )
                if col == "MACD":
                    badge_class = (
                        "badge bg-success" if val == "Bullish" else
                        "badge bg-danger" if val == "Bearish" else
                        "badge bg-secondary"
                    )
                if col == "vs SMA 200":
                    badge_class = (
                        "badge bg-success" if val == "Above" else
                        "badge bg-danger" if val == "Below" else
                        "badge bg-secondary"
                    )
                if badge_class:
                    cells.append(html.Td(html.Span(val, className=badge_class)))
                else:
                    cells.append(html.Td(str(val) if val is not None else "—"))
            rows_html.append(html.Tr(cells))

        return dbc.Table(
            [
                html.Thead(html.Tr([html.Th(c) for c in display_df.columns])),
                html.Tbody(rows_html),
            ],
            bordered=True,
            hover=True,
            responsive=True,
            size="sm",
            className="mt-2",
        )

    # ── Price Targets ─────────────────────────────────────────────────────────

    @app.callback(
        Output("targets-table-container", "children"),
        Input("targets-ticker-dropdown", "value"),
    )
    def update_targets(ticker_filter: str) -> Any:
        """Populate the price targets table from stocks.forecast_runs.

        Args:
            ticker_filter: Selected ticker or ``"all"``.

        Returns:
            Dash table component or an alert when no data is available.
        """
        repo = _get_iceberg_repo()
        if repo is None:
            return dbc.Alert(
                "Iceberg unavailable — cannot load price targets.", color="warning"
            )

        try:
            from stocks.repository import StockRepository as _SR  # noqa: F401
            from pyiceberg.catalog import load_catalog  # noqa: PLC0415
            catalog = load_catalog("local")
            tbl = catalog.load_table("stocks.forecast_runs")
            df = tbl.scan().to_pandas()
        except Exception as exc:
            return dbc.Alert(f"Could not load forecast_runs: {exc}", color="danger")

        if df.empty:
            return dbc.Alert(
                "No forecast data available. Run backfill or use the forecast tool first.",
                color="warning",
                className="mt-3",
            )

        # Keep latest run per (ticker, horizon_months)
        df = (
            df.sort_values("run_date", ascending=False)
            .groupby(["ticker", "horizon_months"], as_index=False)
            .first()
        )

        if ticker_filter and ticker_filter != "all":
            df = df[df["ticker"] == ticker_filter.upper()]

        if df.empty:
            return dbc.Alert(f"No forecast data for {ticker_filter}.", color="info")

        rows_html = []
        for _, row in df.iterrows():
            sentiment = row.get("sentiment", "—") or "—"
            sentiment_badge = (
                "badge bg-success" if sentiment == "Bullish" else
                "badge bg-danger" if sentiment == "Bearish" else
                "badge bg-secondary"
            )

            def _target_cell(price, pct, m_label):
                if price is None or (hasattr(price, "__float__") and math.isnan(float(price))):
                    return html.Td("—")
                sign = "+" if float(pct or 0) >= 0 else ""
                color = "text-success" if float(pct or 0) >= 0 else "text-danger"
                return html.Td([
                    html.Span(f"{float(price):.2f}", className="fw-semibold"),
                    html.Br(),
                    html.Small(f"{sign}{float(pct or 0):.1f}%", className=color),
                ])

            rows_html.append(html.Tr([
                html.Td(html.Strong(row.get("ticker", ""))),
                html.Td(str(row.get("horizon_months", "")) + "m"),
                html.Td(str(row.get("run_date", "—"))),
                html.Td(f"{float(row['current_price_at_run']):.2f}" if row.get("current_price_at_run") else "—"),
                _target_cell(row.get("target_3m_price"), row.get("target_3m_pct_change"), "3m"),
                _target_cell(row.get("target_6m_price"), row.get("target_6m_pct_change"), "6m"),
                _target_cell(row.get("target_9m_price"), row.get("target_9m_pct_change"), "9m"),
                html.Td(html.Span(sentiment, className=sentiment_badge)),
            ]))

        return dbc.Table(
            [
                html.Thead(html.Tr([
                    html.Th("Ticker"), html.Th("Horizon"), html.Th("Run Date"),
                    html.Th("Price at Run"),
                    html.Th("3m Target"), html.Th("6m Target"), html.Th("9m Target"),
                    html.Th("Sentiment"),
                ])),
                html.Tbody(rows_html),
            ],
            bordered=True,
            hover=True,
            responsive=True,
            size="sm",
            className="mt-2",
        )

    # ── Dividends ─────────────────────────────────────────────────────────────

    @app.callback(
        Output("dividends-table-container", "children"),
        Input("dividends-ticker-dropdown", "value"),
    )
    def update_dividends(ticker_filter: str) -> Any:
        """Populate the dividends table from stocks.dividends.

        Args:
            ticker_filter: Selected ticker or ``"all"``.

        Returns:
            Dash table component or an alert when no data is available.
        """
        repo = _get_iceberg_repo()
        if repo is None:
            return dbc.Alert("Iceberg unavailable.", color="warning")

        if ticker_filter and ticker_filter != "all":
            df = repo.get_dividends(ticker_filter.upper())
        else:
            df = repo._table_to_df("stocks.dividends")

        if df.empty:
            return dbc.Alert(
                "No dividend data available. Run backfill or use the dividend tool first.",
                color="warning",
                className="mt-3",
            )

        # Sort most-recent first
        df = df.sort_values("ex_date", ascending=False).reset_index(drop=True)

        sym_map = {
            "USD": "$", "INR": "₹", "GBP": "£", "EUR": "€",
            "JPY": "¥", "CNY": "¥", "AUD": "A$", "CAD": "CA$",
        }

        rows_html = []
        for _, row in df.head(500).iterrows():
            currency = str(row.get("currency", "USD") or "USD")
            sym = sym_map.get(currency.upper(), currency)
            amount = row.get("dividend_amount")
            amount_str = f"{sym}{float(amount):.4f}" if amount else "—"
            rows_html.append(html.Tr([
                html.Td(html.Strong(str(row.get("ticker", "")))),
                html.Td(str(row.get("ex_date", "—"))),
                html.Td(amount_str),
                html.Td(currency),
            ]))

        return dbc.Table(
            [
                html.Thead(html.Tr([
                    html.Th("Ticker"), html.Th("Ex-Date"),
                    html.Th("Amount"), html.Th("Currency"),
                ])),
                html.Tbody(rows_html),
            ],
            bordered=True,
            hover=True,
            responsive=True,
            size="sm",
            className="mt-2",
        )

    # ── Risk Metrics ──────────────────────────────────────────────────────────

    @app.callback(
        Output("risk-table-container", "children"),
        Input("risk-sort-by", "value"),
    )
    def update_risk(sort_col: str) -> Any:
        """Populate the risk metrics table from stocks.analysis_summary.

        Args:
            sort_col: Column name to sort by (descending for Sharpe/return;
                ascending for drawdown/volatility).

        Returns:
            Dash table component or an alert when no data is available.
        """
        repo = _get_iceberg_repo()
        df = pd.DataFrame()
        if repo is not None:
            df = repo.get_all_latest_analysis_summary()

        if df.empty:
            return dbc.Alert(
                "No risk data available. Run backfill or analyse stocks first.",
                color="warning",
                className="mt-3",
            )

        display_cols = [
            "ticker", "annualized_return_pct", "annualized_volatility_pct",
            "sharpe_ratio", "max_drawdown_pct", "max_drawdown_duration_days",
            "bull_phase_pct", "bear_phase_pct",
        ]
        display_cols = [c for c in display_cols if c in df.columns]
        display_df = df[display_cols].copy()

        # Sort ascending for drawdown/volatility, descending for return/Sharpe
        ascending = sort_col in ("max_drawdown_pct", "annualized_volatility_pct",
                                 "max_drawdown_duration_days")
        if sort_col in display_df.columns:
            display_df = display_df.sort_values(
                sort_col, ascending=ascending, na_position="last"
            )

        col_labels = {
            "ticker": "Ticker",
            "annualized_return_pct": "Ann. Return %",
            "annualized_volatility_pct": "Volatility %",
            "sharpe_ratio": "Sharpe",
            "max_drawdown_pct": "Max DD %",
            "max_drawdown_duration_days": "Max DD Days",
            "bull_phase_pct": "Bull %",
            "bear_phase_pct": "Bear %",
        }
        display_df.columns = [col_labels.get(c, c) for c in display_df.columns]

        for num_col in ["Ann. Return %", "Volatility %", "Sharpe", "Max DD %", "Bull %", "Bear %"]:
            if num_col in display_df.columns:
                display_df[num_col] = pd.to_numeric(
                    display_df[num_col], errors="coerce"
                ).round(2)

        rows_html = [
            html.Tr([html.Td(str(v) if v is not None else "—") for v in row])
            for _, row in display_df.iterrows()
        ]

        return dbc.Table(
            [
                html.Thead(html.Tr([html.Th(c) for c in display_df.columns])),
                html.Tbody(rows_html),
            ],
            bordered=True,
            hover=True,
            responsive=True,
            size="sm",
            className="mt-2",
        )

    # ── Sectors ───────────────────────────────────────────────────────────────

    @app.callback(
        Output("sectors-bar-chart", "figure"),
        Output("sectors-table-container", "children"),
        Input("insights-tabs", "active_tab"),
    )
    def update_sectors(active_tab: str) -> tuple:
        """Populate the sector analysis chart and summary table.

        Joins ``stocks.company_info`` (for sector names) with
        ``stocks.analysis_summary`` (for performance).

        Args:
            active_tab: Currently active Insights tab ID.

        Returns:
            Tuple of (Plotly figure, table component).
        """
        if active_tab != "sectors-tab":
            return go.Figure(), html.Div()

        repo = _get_iceberg_repo()
        empty_fig = go.Figure().update_layout(
            template="plotly_white",
            title="No sector data available",
            paper_bgcolor="#f9fafb",
        )

        if repo is None:
            return empty_fig, dbc.Alert("Iceberg unavailable.", color="warning")

        company_df = repo.get_all_latest_company_info()
        analysis_df = repo.get_all_latest_analysis_summary()

        if company_df.empty or analysis_df.empty:
            return empty_fig, dbc.Alert(
                "No sector data available. Run backfill first.",
                color="warning",
                className="mt-3",
            )

        # Join on ticker
        merged = company_df[["ticker", "sector"]].merge(
            analysis_df[["ticker", "annualized_return_pct", "sharpe_ratio",
                          "annualized_volatility_pct"]],
            on="ticker",
            how="inner",
        )
        merged = merged[merged["sector"].notna() & (merged["sector"] != "N/A")]

        if merged.empty:
            return empty_fig, dbc.Alert("No sector metadata found.", color="info")

        sector_agg = (
            merged.groupby("sector")
            .agg(
                count=("ticker", "count"),
                avg_return=("annualized_return_pct", "mean"),
                avg_sharpe=("sharpe_ratio", "mean"),
                avg_vol=("annualized_volatility_pct", "mean"),
            )
            .reset_index()
            .sort_values("avg_return", ascending=False)
        )

        # Bar chart — average annualised return by sector
        colors = [
            "#4caf50" if r >= 0 else "#ef5350"
            for r in sector_agg["avg_return"]
        ]
        fig = go.Figure(go.Bar(
            x=sector_agg["sector"],
            y=sector_agg["avg_return"].round(2),
            marker_color=colors,
            text=sector_agg["avg_return"].round(1).astype(str) + "%",
            textposition="outside",
        ))
        fig.update_layout(
            template="plotly_white",
            title="Average Annualised Return by Sector",
            xaxis_title="Sector",
            yaxis_title="Avg Ann. Return %",
            paper_bgcolor="#f9fafb",
            plot_bgcolor="#ffffff",
            height=400,
            margin=dict(l=50, r=30, t=60, b=100),
        )
        fig.update_xaxes(tickangle=-30)

        # Summary table
        sector_agg_disp = sector_agg.copy()
        sector_agg_disp.columns = ["Sector", "Stocks", "Avg Return %", "Avg Sharpe", "Avg Vol %"]
        for col in ["Avg Return %", "Avg Sharpe", "Avg Vol %"]:
            sector_agg_disp[col] = sector_agg_disp[col].round(2)

        table = dbc.Table.from_dataframe(
            sector_agg_disp,
            bordered=True,
            hover=True,
            responsive=True,
            size="sm",
            className="mt-2",
        )

        return fig, table

    # ── Correlation ───────────────────────────────────────────────────────────

    @app.callback(
        Output("correlation-heatmap", "figure"),
        Input("corr-period-filter", "value"),
    )
    def update_correlation(period: str) -> go.Figure:
        """Build the returns correlation heatmap.

        Reads OHLCV data from the Iceberg ``stocks.ohlcv`` table (or flat
        parquet files as fallback), computes daily close-price returns for
        each ticker, and renders a heatmap.

        Args:
            period: Lookback period: ``"1y"``, ``"3y"``, or ``"all"``.

        Returns:
            Plotly heatmap figure.
        """
        empty_fig = go.Figure().update_layout(
            template="plotly_white",
            title="No OHLCV data available",
            paper_bgcolor="#f9fafb",
        )

        repo = _get_iceberg_repo()
        close_data: Dict[str, pd.Series] = {}

        if repo is not None:
            df_all = repo._table_to_df("stocks.ohlcv")
            if not df_all.empty:
                for ticker in df_all["ticker"].unique():
                    sub = df_all[df_all["ticker"] == ticker].copy()
                    sub["date"] = pd.to_datetime(sub["date"])
                    sub = sub.sort_values("date").set_index("date")
                    close_data[ticker] = sub["close"].dropna()

        # Fallback to flat parquet files
        if not close_data:
            try:
                import json as _json
                if _REGISTRY_PATH.exists():
                    with open(_REGISTRY_PATH) as _f:
                        registry = _json.load(_f)
                    for ticker in sorted(registry.keys()):
                        parquet_path = _DATA_RAW / f"{ticker}_raw.parquet"
                        if parquet_path.exists():
                            _df = pd.read_parquet(parquet_path, engine="pyarrow")
                            _df.index = pd.to_datetime(_df.index).tz_localize(None)
                            close_data[ticker] = _df["Close"].dropna()
            except Exception as _e:
                logger.warning("Correlation fallback failed: %s", _e)

        if not close_data:
            return empty_fig

        # Apply period filter
        cutoff = None
        if period == "1y":
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=1)
        elif period == "3y":
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=3)

        daily_returns: Dict[str, pd.Series] = {}
        for ticker, prices in close_data.items():
            if cutoff is not None:
                prices = prices[prices.index >= cutoff]
            if len(prices) > 10:
                daily_returns[ticker] = prices.pct_change().dropna()

        if len(daily_returns) < 2:
            return empty_fig

        ret_df = pd.DataFrame(daily_returns).dropna(how="all")
        corr = ret_df.corr().round(3)
        tickers_sorted = sorted(corr.columns)
        corr = corr.loc[tickers_sorted, tickers_sorted]

        z = corr.values.tolist()
        text = [[f"{v:.2f}" for v in row] for row in z]

        fig = go.Figure(go.Heatmap(
            z=z,
            x=tickers_sorted,
            y=tickers_sorted,
            text=text,
            texttemplate="%{text}",
            colorscale="RdBu",
            zmid=0,
            zmin=-1,
            zmax=1,
            colorbar=dict(title="Correlation"),
        ))
        fig.update_layout(
            template="plotly_white",
            title=f"Daily Returns Correlation ({period.upper()} lookback)",
            paper_bgcolor="#f9fafb",
            plot_bgcolor="#ffffff",
            height=580,
            margin=dict(l=80, r=30, t=60, b=80),
            xaxis=dict(tickangle=-45),
        )

        return fig
