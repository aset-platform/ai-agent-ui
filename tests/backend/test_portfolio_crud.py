"""Tests for portfolio CRUD and user preferences endpoints.

Exercises ``/v1/users/me/portfolio`` CRUD operations and
``/v1/users/me/preferences`` read/write with mocked Iceberg
and cache via :func:`unittest.mock.patch`.
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

    # Mount the real ticker router for these tests
    from auth.endpoints.ticker_routes import router
    app.include_router(router, prefix="/v1")

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


def _holdings_df():
    """Two tickers in portfolio."""
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


def _txn_df():
    """Two transactions with IDs."""
    return pd.DataFrame([
        {
            "transaction_id": "txn-aaa",
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
            "transaction_id": "txn-bbb",
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


# ---------------------------------------------------------------
# GET /v1/users/me/portfolio
# ---------------------------------------------------------------

@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_get_portfolio_happy_path(
    mock_repo_fn, client,
):
    """Holdings enriched with current prices."""
    repo = MagicMock()
    repo.get_portfolio_holdings.return_value = (
        _holdings_df()
    )
    repo.get_portfolio_transactions.return_value = (
        _txn_df()
    )

    ohlcv_aapl = pd.DataFrame([{"close": 160.0}])
    ohlcv_msft = pd.DataFrame([{"close": 310.0}])

    def _ohlcv(ticker):
        if ticker == "AAPL":
            return ohlcv_aapl
        return ohlcv_msft

    repo.get_ohlcv.side_effect = _ohlcv
    mock_repo_fn.return_value = repo

    r = client.get("/v1/users/me/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert len(body["holdings"]) == 2
    aapl = body["holdings"][0]
    assert aapl["ticker"] == "AAPL"
    assert aapl["transaction_id"] == "txn-aaa"
    assert aapl["current_price"] == 160.0
    assert aapl["invested"] == 1500.0
    assert aapl["current_value"] == 1600.0
    assert aapl["gain_loss_pct"] is not None
    assert body["totals"]["USD"] > 0


@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_get_portfolio_empty(mock_repo_fn, client):
    """No holdings returns empty list."""
    repo = MagicMock()
    repo.get_portfolio_holdings.return_value = (
        pd.DataFrame(columns=[
            "ticker", "quantity", "avg_price",
            "currency", "market", "invested",
        ])
    )
    repo.get_portfolio_transactions.return_value = (
        pd.DataFrame()
    )
    mock_repo_fn.return_value = repo

    r = client.get("/v1/users/me/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["holdings"] == []
    assert body["totals"] == {}


# ---------------------------------------------------------------
# POST /v1/users/me/portfolio
# ---------------------------------------------------------------

@patch(
    "cache.get_cache",
)
@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_add_portfolio_holding(
    mock_repo_fn, mock_cache_fn, client,
):
    """Add returns transaction_id."""
    repo = MagicMock()
    mock_repo_fn.return_value = repo

    cache = MagicMock()
    mock_cache_fn.return_value = cache

    r = client.post(
        "/v1/users/me/portfolio",
        json={
            "ticker": "GOOG",
            "quantity": 3,
            "price": 140.0,
            "trade_date": "2024-06-01",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["detail"] == "added"
    assert "transaction_id" in body
    assert len(body["transaction_id"]) > 0

    repo.add_portfolio_transaction.assert_called_once()
    txn_arg = (
        repo.add_portfolio_transaction.call_args[0][0]
    )
    assert txn_arg["ticker"] == "GOOG"
    assert txn_arg["quantity"] == 3
    assert txn_arg["side"] == "BUY"


@patch(
    "cache.get_cache",
)
@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_add_portfolio_india_ticker(
    mock_repo_fn, mock_cache_fn, client,
):
    """India ticker sets market=india, currency=INR."""
    repo = MagicMock()
    mock_repo_fn.return_value = repo
    mock_cache_fn.return_value = MagicMock()

    r = client.post(
        "/v1/users/me/portfolio",
        json={
            "ticker": "RELIANCE.NS",
            "quantity": 10,
            "price": 2500.0,
            "trade_date": "2024-06-01",
        },
    )
    assert r.status_code == 200
    txn_arg = (
        repo.add_portfolio_transaction.call_args[0][0]
    )
    assert txn_arg["market"] == "india"
    assert txn_arg["currency"] == "INR"


@patch(
    "cache.get_cache",
)
@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_add_portfolio_cache_invalidated(
    mock_repo_fn, mock_cache_fn, client,
):
    """Cache invalidated on add."""
    repo = MagicMock()
    mock_repo_fn.return_value = repo

    cache = MagicMock()
    mock_cache_fn.return_value = cache

    client.post(
        "/v1/users/me/portfolio",
        json={
            "ticker": "TSLA",
            "quantity": 2,
            "price": 250.0,
            "trade_date": "2024-07-01",
        },
    )
    assert cache.invalidate.call_count >= 1


# ---------------------------------------------------------------
# PUT /v1/users/me/portfolio/{id}
# ---------------------------------------------------------------

@patch(
    "cache.get_cache",
)
@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_edit_portfolio_holding(
    mock_repo_fn, mock_cache_fn, client,
):
    """Edit quantity and price succeeds."""
    repo = MagicMock()
    repo.update_portfolio_transaction.return_value = (
        True
    )
    mock_repo_fn.return_value = repo
    mock_cache_fn.return_value = MagicMock()

    r = client.put(
        "/v1/users/me/portfolio/txn-aaa",
        json={"quantity": 15, "price": 155.0},
    )
    assert r.status_code == 200
    assert r.json()["detail"] == "updated"

    repo.update_portfolio_transaction.assert_called_once()
    args = (
        repo.update_portfolio_transaction.call_args[0]
    )
    assert args[0] == "txn-aaa"
    assert args[1] == "test-user-1"
    assert args[2]["quantity"] == 15
    assert args[2]["price"] == 155.0


@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_edit_portfolio_not_found(
    mock_repo_fn, client,
):
    """404 when transaction_id not found."""
    repo = MagicMock()
    repo.update_portfolio_transaction.return_value = (
        False
    )
    mock_repo_fn.return_value = repo

    r = client.put(
        "/v1/users/me/portfolio/bad-id",
        json={"quantity": 5},
    )
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_edit_portfolio_no_fields(client):
    """422 when no updateable fields provided."""
    r = client.put(
        "/v1/users/me/portfolio/txn-aaa",
        json={},
    )
    assert r.status_code == 422


@patch(
    "cache.get_cache",
)
@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_edit_portfolio_cache_invalidated(
    mock_repo_fn, mock_cache_fn, client,
):
    """Cache invalidated on edit."""
    repo = MagicMock()
    repo.update_portfolio_transaction.return_value = (
        True
    )
    mock_repo_fn.return_value = repo

    cache = MagicMock()
    mock_cache_fn.return_value = cache

    client.put(
        "/v1/users/me/portfolio/txn-aaa",
        json={"price": 160.0},
    )
    assert cache.invalidate.call_count >= 1


# ---------------------------------------------------------------
# DELETE /v1/users/me/portfolio/{id}
# ---------------------------------------------------------------

@patch(
    "cache.get_cache",
)
@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_delete_portfolio_holding(
    mock_repo_fn, mock_cache_fn, client,
):
    """Delete succeeds."""
    repo = MagicMock()
    repo.delete_portfolio_transaction.return_value = (
        True
    )
    mock_repo_fn.return_value = repo
    mock_cache_fn.return_value = MagicMock()

    r = client.delete(
        "/v1/users/me/portfolio/txn-aaa",
    )
    assert r.status_code == 200
    assert r.json()["detail"] == "deleted"

    repo.delete_portfolio_transaction.assert_called_once_with(
        "txn-aaa", "test-user-1",
    )


@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_delete_portfolio_not_found(
    mock_repo_fn, client,
):
    """404 when transaction_id not found."""
    repo = MagicMock()
    repo.delete_portfolio_transaction.return_value = (
        False
    )
    mock_repo_fn.return_value = repo

    r = client.delete(
        "/v1/users/me/portfolio/bad-id",
    )
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


@patch(
    "cache.get_cache",
)
@patch(
    "auth.endpoints.ticker_routes._get_stock_repo",
)
def test_delete_portfolio_cache_invalidated(
    mock_repo_fn, mock_cache_fn, client,
):
    """Cache invalidated on delete."""
    repo = MagicMock()
    repo.delete_portfolio_transaction.return_value = (
        True
    )
    mock_repo_fn.return_value = repo

    cache = MagicMock()
    mock_cache_fn.return_value = cache

    client.delete(
        "/v1/users/me/portfolio/txn-bbb",
    )
    assert cache.invalidate.call_count >= 1


# ---------------------------------------------------------------
# GET /v1/users/me/preferences
# ---------------------------------------------------------------

@patch(
    "cache.get_cache",
)
def test_get_preferences_empty(
    mock_cache_fn, client,
):
    """No stored prefs returns empty dict."""
    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.get("/v1/users/me/preferences")
    assert r.status_code == 200
    assert r.json() == {}


@patch(
    "cache.get_cache",
)
def test_get_preferences_stored(
    mock_cache_fn, client,
):
    """Stored prefs returned correctly."""
    import json

    prefs = {
        "chart": {"theme": "dark"},
        "dashboard": {"refresh": 30},
    }
    cache = MagicMock()
    cache.get.return_value = json.dumps(prefs)
    mock_cache_fn.return_value = cache

    r = client.get("/v1/users/me/preferences")
    assert r.status_code == 200
    body = r.json()
    assert body["chart"]["theme"] == "dark"
    assert body["dashboard"]["refresh"] == 30

    # Verify sliding TTL extended
    cache.set.assert_called_once()


# ---------------------------------------------------------------
# PUT /v1/users/me/preferences
# ---------------------------------------------------------------

@patch(
    "cache.get_cache",
)
def test_put_preferences_partial_merge(
    mock_cache_fn, client,
):
    """Partial update merges with existing prefs."""
    import json

    existing = {
        "chart": {"theme": "dark", "gridlines": True},
    }
    cache = MagicMock()
    cache.get.return_value = json.dumps(existing)
    mock_cache_fn.return_value = cache

    r = client.put(
        "/v1/users/me/preferences",
        json={"chart": {"theme": "light"}},
    )
    assert r.status_code == 200
    assert r.json()["detail"] == "saved"

    # Verify merged payload written
    saved = json.loads(
        cache.set.call_args[0][1]
    )
    assert saved["chart"]["theme"] == "light"
    assert saved["chart"]["gridlines"] is True


@patch(
    "cache.get_cache",
)
def test_put_preferences_new_section(
    mock_cache_fn, client,
):
    """New section created when not existing."""
    cache = MagicMock()
    cache.get.return_value = None
    mock_cache_fn.return_value = cache

    r = client.put(
        "/v1/users/me/preferences",
        json={"navigation": {"sidebar": "collapsed"}},
    )
    assert r.status_code == 200

    import json
    saved = json.loads(
        cache.set.call_args[0][1]
    )
    assert saved["navigation"]["sidebar"] == (
        "collapsed"
    )


@patch(
    "cache.get_cache",
)
def test_put_preferences_overwrites_scalar(
    mock_cache_fn, client,
):
    """Scalar value overwrites existing section."""
    import json

    existing = {"last_login": "2024-01-01"}
    cache = MagicMock()
    cache.get.return_value = json.dumps(existing)
    mock_cache_fn.return_value = cache

    r = client.put(
        "/v1/users/me/preferences",
        json={"last_login": "2024-06-15"},
    )
    assert r.status_code == 200
    saved = json.loads(
        cache.set.call_args[0][1]
    )
    assert saved["last_login"] == "2024-06-15"
