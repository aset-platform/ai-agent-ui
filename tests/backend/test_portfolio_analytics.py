"""Tests for portfolio performance and forecast endpoints.

Exercises ``/v1/dashboard/portfolio/performance`` and
``/v1/dashboard/portfolio/forecast`` with mocked Iceberg
data via :func:`unittest.mock.patch`.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from auth.models import UserContext


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _make_app():
    """Build a FastAPI app with mocked infra deps."""
    mock_registry = MagicMock()
    mock_registry.get.return_value = None
    mock_registry.list_agents.return_value = []

    mock_executor = MagicMock()

    mock_settings = MagicMock()
    mock_settings.agent_timeout_seconds = 30
    mock_settings.groq_model_tiers = ""

    with (
        patch(
            "routes.create_auth_router",
            return_value=APIRouter(),
        ),
        patch(
            "routes.get_ticker_router",
            return_value=APIRouter(),
        ),
        patch("paths.ensure_dirs"),
        patch(
            "paths.AVATARS_DIR",
            new=Path("/tmp/test_avatars"),
        ),
        patch("routes.StaticFiles"),
    ):
        from routes import create_app

        app = create_app(
            mock_registry,
            mock_executor,
            mock_settings,
        )

    return app


_TEST_USER = UserContext(
    user_id="test-user-1",
    email="test@example.com",
    role="user",
)


@pytest.fixture()
def client():
    """TestClient with auth override."""
    app = _make_app()
    app.dependency_overrides[
        get_current_user
    ] = lambda: _TEST_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


def _txn_df():
    """Two tickers bought on different dates."""
    return pd.DataFrame([
        {
            "transaction_id": "t1",
            "user_id": "test-user-1",
            "ticker": "AAPL",
            "side": "BUY",
            "quantity": 10,
            "price": 150.0,
            "currency": "USD",
            "market": "us",
            "trade_date": "2024-01-15",
        },
        {
            "transaction_id": "t2",
            "user_id": "test-user-1",
            "ticker": "MSFT",
            "side": "BUY",
            "quantity": 5,
            "price": 300.0,
            "currency": "USD",
            "market": "us",
            "trade_date": "2024-02-01",
        },
    ])


def _ohlcv_df():
    """OHLCV for AAPL and MSFT across 5 dates."""
    rows = []
    aapl = [150, 152, 155, 153, 158]
    msft = [300, 305, 310, 308, 315]
    dates = [
        "2024-01-15", "2024-01-16",
        "2024-02-01", "2024-02-02",
        "2024-02-03",
    ]
    for i, d in enumerate(dates):
        rows.append({
            "ticker": "AAPL",
            "date": d,
            "open": aapl[i],
            "high": aapl[i] + 2,
            "low": aapl[i] - 2,
            "close": aapl[i],
            "volume": 1000000,
        })
        rows.append({
            "ticker": "MSFT",
            "date": d,
            "open": msft[i],
            "high": msft[i] + 3,
            "low": msft[i] - 3,
            "close": msft[i],
            "volume": 500000,
        })
    return pd.DataFrame(rows)


def _holdings_df():
    """Holdings for two tickers."""
    return pd.DataFrame([
        {
            "ticker": "AAPL",
            "quantity": 10,
            "avg_price": 150.0,
            "currency": "USD",
            "market": "us",
            "invested": 1500.0,
        },
        {
            "ticker": "MSFT",
            "quantity": 5,
            "avg_price": 300.0,
            "currency": "USD",
            "market": "us",
            "invested": 1500.0,
        },
    ])


def _forecast_df(ticker):
    """3 forecast points for a ticker."""
    base = 160 if ticker == "AAPL" else 320
    return pd.DataFrame([
        {
            "forecast_date": "2024-03-01",
            "predicted_price": base,
            "lower_bound": base - 10,
            "upper_bound": base + 10,
            "run_date": "2024-02-03",
        },
        {
            "forecast_date": "2024-04-01",
            "predicted_price": base + 5,
            "lower_bound": base - 8,
            "upper_bound": base + 15,
            "run_date": "2024-02-03",
        },
        {
            "forecast_date": "2024-05-01",
            "predicted_price": base + 10,
            "lower_bound": base - 5,
            "upper_bound": base + 20,
            "run_date": "2024-02-03",
        },
    ])


def _forecasts_table_df(tickers=("AAPL", "MSFT"), horizon_months=9):
    """Combined multi-ticker stocks.forecasts shape (adds ticker +
    horizon_months on top of _forecast_df's per-ticker columns) —
    matches what _duckdb_read("stocks.forecasts", ...) really
    returns, not the StockRepository.get_latest_forecast_series(
    ticker, horizon) signature these tests used to mock."""
    frames = []
    for t in tickers:
        df = _forecast_df(t).copy()
        df["ticker"] = t
        df["horizon_months"] = horizon_months
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _current_price_ohlcv_df(prices):
    """Minimal stocks.ohlcv shape for the forecast route's
    "current price" lookup — one row per ticker, last close used
    as-is. ``prices`` is {ticker: close}."""
    return pd.DataFrame(
        [
            {"ticker": t, "date": "2024-02-03", "close": c}
            for t, c in prices.items()
        ]
    )


def _duckdb_side_effect(ohlcv_df=None, forecasts_df=None):
    """Build a side_effect fn for dashboard_routes._duckdb_read,
    keyed by table name. _build_portfolio_performance /
    _build_portfolio_forecast read stocks.ohlcv and/or
    stocks.forecasts directly via DuckDB now, NOT via
    StockRepository.get_ohlcv_batch/get_ohlcv/
    get_latest_forecast_series — those mocks silently stopped
    intercepting anything after that refactor, letting real
    (environment-dependent) Iceberg data leak into these tests."""
    def _fn(table, sql):
        if table == "stocks.ohlcv" and ohlcv_df is not None:
            return ohlcv_df
        if table == "stocks.forecasts" and forecasts_df is not None:
            return forecasts_df
        return pd.DataFrame()
    return _fn


# ---------------------------------------------------------------
# Performance tests
# ---------------------------------------------------------------

@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_performance_happy_path(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """Two tickers, different trade dates."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_ohlcv_df(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?period=ALL&currency=USD",
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) > 0
    assert body["currency"] == "USD"
    assert body["metrics"] is not None
    # First point should have zero P&L
    assert body["data"][0]["daily_pnl"] == 0.0
    # invested_value present
    assert "invested_value" in body["data"][0]
    assert body["data"][0]["invested_value"] > 0


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_performance_invested_value_timeline(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """Invested increases when 2nd ticker starts."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_ohlcv_df(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?period=ALL&currency=USD",
    )
    body = r.json()
    # 2024-01-15: AAPL only → invested 10*150=1500
    first = body["data"][0]
    assert first["invested_value"] == 1500.0
    # 2024-02-01: AAPL+MSFT → 1500 + 5*300=3000
    feb1 = next(
        p for p in body["data"]
        if p["date"] == "2024-02-01"
    )
    assert feb1["invested_value"] == 3000.0


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._get_stock_repo")
def test_performance_no_holdings(
    mock_repo_fn, mock_cache_fn, client,
):
    """No transactions → empty response."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        pd.DataFrame()
    )
    mock_repo_fn.return_value = repo

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?currency=USD",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["metrics"] is None


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_performance_no_ohlcv(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """Ticker with no OHLCV → skipped."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=pd.DataFrame(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?currency=USD",
    )
    assert r.status_code == 200
    assert r.json()["data"] == []


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_performance_trade_date_filtering(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """MSFT bought on 2024-02-01 should NOT count
    for dates before that."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_ohlcv_df(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?period=ALL&currency=USD",
    )
    body = r.json()
    # First date (2024-01-15): only AAPL counts
    # AAPL close = 150, qty = 10 → 1500
    first = body["data"][0]
    assert first["date"] == "2024-01-15"
    assert first["value"] == 1500.0

    # 2024-02-01: both AAPL + MSFT
    # AAPL=155*10=1550, MSFT=310*5=1550 → 3100
    feb1 = next(
        p for p in body["data"]
        if p["date"] == "2024-02-01"
    )
    assert feb1["value"] == 3100.0


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_performance_period_filter(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """1M period returns only recent data."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_ohlcv_df(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?period=1M&currency=USD",
    )
    assert r.status_code == 200
    # 1M from last date (2024-02-03) → cutoff
    # 2024-01-04. All our dates are after this.
    assert len(r.json()["data"]) > 0


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_performance_metrics_values(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """Verify metrics are computed correctly."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_ohlcv_df(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?period=ALL&currency=USD",
    )
    m = r.json()["metrics"]
    assert m is not None
    # Total return: (final - initial) / initial %
    assert m["total_return_pct"] != 0
    # Max drawdown should be <= 0
    assert m["max_drawdown_pct"] <= 0
    # Best day > 0
    assert m["best_day_pct"] > 0


# ---------------------------------------------------------------
# Forecast tests
# ---------------------------------------------------------------

@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_forecast_happy_path(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """Weighted aggregation of two tickers."""
    repo = MagicMock()
    repo.get_portfolio_holdings.return_value = (
        _holdings_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_current_price_ohlcv_df(
            {"AAPL": 158.0, "MSFT": 315.0},
        ),
        forecasts_df=_forecasts_table_df(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/forecast"
        "?horizon=9&currency=USD",
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 3
    assert body["horizon_months"] == 9
    assert body["current_value"] > 0

    # First point: AAPL 160*10 + MSFT 320*5 = 3200
    assert body["data"][0]["predicted"] == 3200.0
    # total_invested = 1500 + 1500 = 3000
    assert body["total_invested"] == 3000.0


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_forecast_always_fetches_9m(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """The route hardcodes fc_all[fc_all["horizon_months"] == 9]
    regardless of the query's horizon param — only 9-month runs
    are persisted/used. Mix in a bogus 3-month row with an
    obviously-wrong predicted_price and confirm it never surfaces,
    even when the query explicitly asks for horizon=3."""
    repo = MagicMock()
    repo.get_portfolio_holdings.return_value = (
        _holdings_df()
    )
    mock_repo_fn.return_value = repo

    forecasts_9m = _forecasts_table_df(
        tickers=("AAPL",), horizon_months=9,
    )
    forecasts_3m_bogus = _forecast_df("AAPL").copy()
    forecasts_3m_bogus["predicted_price"] = 999999.0
    forecasts_3m_bogus["ticker"] = "AAPL"
    forecasts_3m_bogus["horizon_months"] = 3
    forecasts_df = pd.concat(
        [forecasts_9m, forecasts_3m_bogus], ignore_index=True,
    )
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_current_price_ohlcv_df(
            {"AAPL": 158.0, "MSFT": 315.0},
        ),
        forecasts_df=forecasts_df,
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    # Request horizon=3 but the response must still reflect only
    # the 9-month forecast data (predicted == 10 * 160, never the
    # bogus 3-month row's 999999.0).
    r = client.get(
        "/v1/dashboard/portfolio/forecast"
        "?horizon=3&currency=USD",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"][0]["predicted"] == 1600.0


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._get_stock_repo")
def test_forecast_no_holdings(
    mock_repo_fn, mock_cache_fn, client,
):
    """No holdings → empty forecast."""
    repo = MagicMock()
    repo.get_portfolio_holdings.return_value = (
        pd.DataFrame(columns=[
            "ticker", "quantity", "avg_price",
            "currency", "market", "invested",
        ])
    )
    mock_repo_fn.return_value = repo

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/forecast"
        "?currency=USD",
    )
    assert r.status_code == 200
    assert r.json()["data"] == []


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_forecast_missing_ticker_skipped(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """Ticker with no forecast data is skipped."""
    repo = MagicMock()
    repo.get_portfolio_holdings.return_value = (
        _holdings_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_current_price_ohlcv_df(
            {"AAPL": 158.0, "MSFT": 315.0},
        ),
        # MSFT has no forecast rows at all.
        forecasts_df=_forecasts_table_df(tickers=("AAPL",)),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/forecast"
        "?horizon=9&currency=USD",
    )
    assert r.status_code == 200
    body = r.json()
    # Only AAPL forecast points
    assert len(body["data"]) == 3
    # AAPL only: 160*10 = 1600
    assert body["data"][0]["predicted"] == 1600.0


# ---------------------------------------------------------------
# _safe_float helper
# ---------------------------------------------------------------


class TestSafeFloat:
    """dashboard_routes._safe_float edge cases."""

    def test_nan(self):
        """NaN → 0.0."""
        from dashboard_routes import _safe_float

        assert _safe_float(float("nan")) == 0.0

    def test_none(self):
        """None → 0.0."""
        from dashboard_routes import _safe_float

        assert _safe_float(None) == 0.0

    def test_valid(self):
        """Normal float passes through."""
        from dashboard_routes import _safe_float

        assert _safe_float(42.5) == 42.5

    def test_string(self):
        """Unparseable string → 0.0."""
        from dashboard_routes import _safe_float

        assert _safe_float("bad") == 0.0


# ---------------------------------------------------------------
# Cash-flow adjusted return
# ---------------------------------------------------------------


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_cashflow_adjusted_return(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """Daily return strips new capital injection."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_ohlcv_df(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?period=ALL&currency=USD",
    )
    body = r.json()
    # On 2024-02-01, MSFT is added (cashflow).
    # daily_pnl should strip the capital injection.
    feb1 = next(
        p for p in body["data"]
        if p["date"] == "2024-02-01"
    )
    # AAPL went 152→155 = +30 for 10 shares.
    # MSFT bought at 300, close 310 → +50 for 5 sh.
    # But invested jumped 1500→3000, so cashflow
    # = 1500. raw delta = 3100-1520 = 1580.
    # pnl = 3100 - 1520 - 1500 = 80
    assert feb1["daily_pnl"] != 0


# ---------------------------------------------------------------
# Invested-basis total return
# ---------------------------------------------------------------


@patch("dashboard_routes.get_cache")
@patch("dashboard_routes._duckdb_read")
@patch("dashboard_routes._get_stock_repo")
def test_invested_basis_total_return(
    mock_repo_fn, mock_duckdb, mock_cache_fn, client,
):
    """total_return uses (last_v - last_iv) / last_iv."""
    repo = MagicMock()
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )
    mock_repo_fn.return_value = repo
    mock_duckdb.side_effect = _duckdb_side_effect(
        ohlcv_df=_ohlcv_df(),
    )

    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get(
        "/v1/dashboard/portfolio/performance"
        "?period=ALL&currency=USD",
    )
    m = r.json()["metrics"]
    assert m is not None
    # Last day: AAPL 158*10=1580, MSFT 315*5=1575
    # total = 3155; invested = 1500+1500 = 3000
    # total_return = (3155-3000)/3000*100 ≈ 5.17%
    expected = round(
        (3155 - 3000) / 3000 * 100, 2,
    )
    assert m["total_return_pct"] == expected
