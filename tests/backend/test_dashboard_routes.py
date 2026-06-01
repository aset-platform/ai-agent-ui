"""Tests for dashboard API endpoints.

Exercises ``/v1/dashboard/*`` routes: watchlist, forecasts,
analysis, and LLM usage.  All Iceberg access is mocked via
:func:`unittest.mock.patch`.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    app.dependency_overrides[get_current_user] = lambda: _TEST_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def unauthed_client():
    """TestClient without auth override (401 expected)."""
    app = _make_app()
    return TestClient(app)


# ---------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------


class TestWatchlist:
    """GET /v1/dashboard/watchlist."""

    def test_requires_auth(self, unauthed_client):
        """No auth token -> 401."""
        resp = unauthed_client.get(
            "/v1/dashboard/watchlist",
        )
        assert resp.status_code == 401

    @patch(
        "dashboard_routes._helpers._get_repo",
    )
    def test_empty_tickers(
        self,
        mock_repo,
        client,
    ):
        """No linked tickers -> empty response."""
        mock_repo.return_value.get_user_tickers = AsyncMock(return_value=[])

        resp = client.get("/v1/dashboard/watchlist")

        assert resp.status_code == 200
        data = resp.json()
        assert data["tickers"] == []

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    @patch(
        "dashboard_routes._helpers._get_repo",
    )
    def test_with_data(
        self,
        mock_user_repo,
        mock_stock_repo,
        mock_cache,
        client,
    ):
        """One ticker returns price + change."""
        mock_user_repo.return_value.get_user_tickers = AsyncMock(
            return_value=["AAPL"]
        )

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache.return_value = cache

        prices = [
            148.0,
            149.0,
            150.0,
            151.0,
            152.0,
        ]
        ohlcv_df = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 5,
                "date": [
                    "2024-01-01",
                    "2024-01-02",
                    "2024-01-03",
                    "2024-01-04",
                    "2024-01-05",
                ],
                "close": prices,
            }
        )
        info_df = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "company_name": "Apple Inc.",
                    "currency": "USD",
                }
            ]
        )

        repo = mock_stock_repo.return_value
        repo.get_ohlcv_batch.return_value = ohlcv_df
        repo.get_company_info_batch.return_value = info_df

        resp = client.get(
            "/v1/dashboard/watchlist",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tickers"]) == 1
        ticker = data["tickers"][0]
        assert ticker["ticker"] == "AAPL"
        assert ticker["company_name"] == "Apple Inc."
        assert ticker["current_price"] == 152.0
        assert ticker["previous_close"] == 151.0
        assert ticker["change"] == 1.0


# ---------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------


class TestForecasts:
    """GET /v1/dashboard/forecasts/summary."""

    @patch(
        "dashboard_routes._helpers._get_repo",
    )
    def test_empty(self, mock_repo, client):
        """No linked tickers -> empty forecasts."""
        mock_repo.return_value.get_user_tickers = AsyncMock(return_value=[])

        resp = client.get(
            "/v1/dashboard/forecasts/summary",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["forecasts"] == []

    @patch("dashboard_routes._get_stock_repo")
    @patch(
        "dashboard_routes._helpers._get_repo",
    )
    def test_with_data(
        self,
        mock_user_repo,
        mock_stock_repo,
        client,
    ):
        """Forecast run with 3-month target."""
        mock_user_repo.return_value.get_user_tickers = AsyncMock(
            return_value=["AAPL"]
        )

        df = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "run_date": "2026-03-01",
                    "current_price_at_run": 150.0,
                    "sentiment": "bullish",
                    "target_3m_date": "2026-06-01",
                    "target_3m_price": 165.0,
                    "target_3m_pct_change": 10.0,
                    "target_3m_lower": 155.0,
                    "target_3m_upper": 175.0,
                    "mae": 2.5,
                    "rmse": 3.1,
                },
            ]
        )

        repo_inst = mock_stock_repo.return_value
        repo_inst.get_dashboard_forecast_runs.return_value = df

        resp = client.get(
            "/v1/dashboard/forecasts/summary",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["forecasts"]) == 1
        fc = data["forecasts"][0]
        assert fc["ticker"] == "AAPL"
        assert len(fc["targets"]) >= 1
        assert fc["targets"][0]["horizon_months"] == 3
        assert fc["targets"][0]["target_price"] == 165.0


# ---------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------


class TestAnalysis:
    """GET /v1/dashboard/analysis/latest."""

    @patch(
        "dashboard_routes._helpers._get_repo",
    )
    def test_empty(self, mock_repo, client):
        """No linked tickers -> empty analyses."""
        mock_repo.return_value.get_user_tickers = AsyncMock(return_value=[])

        resp = client.get(
            "/v1/dashboard/analysis/latest",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["analyses"] == []

    @patch("dashboard_routes._get_stock_repo")
    @patch(
        "dashboard_routes._helpers._get_repo",
    )
    def test_with_data(
        self,
        mock_user_repo,
        mock_stock_repo,
        client,
    ):
        """Analysis row with RSI signal."""
        mock_user_repo.return_value.get_user_tickers = AsyncMock(
            return_value=["AAPL"]
        )

        df = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "analysis_date": "2026-03-15",
                    "rsi_signal": "Bullish reversal",
                    "rsi_14": 32.5,
                    "macd_signal_text": None,
                    "macd": None,
                    "sma_50_signal": None,
                    "sma_50": None,
                    "sma_200_signal": None,
                    "sma_200": None,
                    "sharpe_ratio": 1.23,
                    "annualized_return_pct": 12.5,
                    "annualized_volatility_pct": 18.3,
                    "max_drawdown_pct": -8.2,
                },
            ]
        )

        repo_inst = mock_stock_repo.return_value
        repo_inst.get_dashboard_analysis.return_value = df

        resp = client.get(
            "/v1/dashboard/analysis/latest",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["analyses"]) == 1
        analysis = data["analyses"][0]
        assert analysis["ticker"] == "AAPL"
        assert len(analysis["signals"]) == 1
        sig = analysis["signals"][0]
        assert sig["name"] == "RSI 14"
        assert sig["signal"] == "Bullish"
        assert analysis["sharpe_ratio"] == 1.23


# ---------------------------------------------------------------
# LLM Usage
# ---------------------------------------------------------------


class TestLLMUsage:
    """GET /v1/dashboard/llm-usage."""

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_returns_structure(
        self,
        mock_stock_repo,
        mock_cache,
        client,
    ):
        """Verify response fields."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_cache.return_value = cache

        repo = mock_stock_repo.return_value
        repo.get_dashboard_llm_usage.return_value = {
            "total_requests": 42,
            "total_cost": 1.23,
            "avg_latency_ms": 250.0,
            "per_model": {
                "llama-3.3-70b": {
                    "requests": 42,
                    "cost": 1.23,
                },
            },
            "daily_trend": [],
        }

        resp = client.get(
            "/v1/dashboard/llm-usage",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 42
        assert data["total_cost_usd"] == 1.23
        assert len(data["models"]) == 1
        assert data["models"][0]["model"] == "llama-3.3-70b"

    @patch("dashboard_routes._get_stock_repo")
    def test_superuser_sees_all(
        self,
        mock_stock_repo,
    ):
        """Superuser passes user_id=None."""
        su_user = UserContext(
            user_id="admin-1",
            email="admin@example.com",
            role="superuser",
        )
        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: su_user

        repo_inst = mock_stock_repo.return_value
        repo_inst.get_dashboard_llm_usage.return_value = {
            "total_requests": 0,
            "total_cost_usd": 0,
            "models": [],
        }

        tc = TestClient(app)
        resp = tc.get("/v1/dashboard/llm-usage")

        assert resp.status_code == 200
        repo_inst.get_dashboard_llm_usage.assert_called_once()
        call_kwargs = repo_inst.get_dashboard_llm_usage.call_args
        assert call_kwargs.kwargs.get("user_id") is None

        app.dependency_overrides.clear()


# ---------------------------------------------------------------
# Registry
# ---------------------------------------------------------------


class TestRegistry:
    """GET /v1/dashboard/registry."""

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_happy_path(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """Registry with company info."""
        repo = MagicMock()
        repo.get_all_registry.return_value = {
            "AAPL": {"last_fetch_date": "2026-03-19"},
            "MSFT": {"last_fetch_date": "2026-03-18"},
        }
        info_df = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "company_name": "Apple Inc.",
                    "currency": "USD",
                    "current_price": 175.0,
                },
                {
                    "ticker": "MSFT",
                    "company_name": "Microsoft Corp.",
                    "currency": "USD",
                    "current_price": 420.0,
                },
            ]
        )
        repo.get_company_info_batch.return_value = info_df
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get("/v1/dashboard/registry")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tickers"]) == 2
        # Sorted by ticker
        assert data["tickers"][0]["ticker"] == "AAPL"
        assert data["tickers"][0]["company_name"] == "Apple Inc."
        assert data["tickers"][0]["current_price"] == 175.0

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_empty_registry(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """No registered tickers."""
        repo = MagicMock()
        repo.get_all_registry.return_value = {}
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get("/v1/dashboard/registry")

        assert resp.status_code == 200
        assert resp.json()["tickers"] == []


# ---------------------------------------------------------------
# Compare
# ---------------------------------------------------------------


class TestCompare:
    """GET /v1/dashboard/compare."""

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_happy_path(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """Two tickers with normalized series."""
        repo = MagicMock()

        dates = [
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
        ]
        rows = []
        aapl_c = [150, 152, 155, 153, 158]
        msft_c = [300, 305, 310, 308, 315]
        for i, d in enumerate(dates):
            rows.append(
                {
                    "ticker": "AAPL",
                    "date": d,
                    "close": aapl_c[i],
                }
            )
            rows.append(
                {
                    "ticker": "MSFT",
                    "date": d,
                    "close": msft_c[i],
                }
            )
        ohlcv_df = pd.DataFrame(rows)

        repo.get_ohlcv_batch.return_value = ohlcv_df
        repo.get_analysis_summary_batch.return_value = pd.DataFrame(
            columns=["ticker"]
        )
        repo.get_company_info_batch.return_value = pd.DataFrame(
            columns=["ticker"]
        )
        repo.get_technical_indicators_batch.return_value = pd.DataFrame(
            columns=["ticker"]
        )
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/compare" "?tickers=AAPL,MSFT",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert set(data["tickers"]) == {
            "AAPL",
            "MSFT",
        }
        assert len(data["series"]) == 2
        assert len(data["correlation"]) == 2
        # First normalized value is always 100
        for s in data["series"]:
            assert s["normalized"][0] == 100.0

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_less_than_two_tickers(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """Single ticker → empty compare."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/compare" "?tickers=AAPL",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["series"] == []
        assert data["correlation"] == []


# ---------------------------------------------------------------
# Home (aggregate)
# ---------------------------------------------------------------


class TestHome:
    """GET /v1/dashboard/home."""

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    @patch(
        "dashboard_routes._helpers._get_repo",
    )
    def test_returns_all_widgets(
        self,
        mock_user_repo,
        mock_stock_repo,
        mock_cache_fn,
        client,
    ):
        """Verify all 4 widget keys present."""
        mock_user_repo.return_value.get_user_tickers = AsyncMock(
            return_value=[]
        )

        repo = MagicMock()
        repo.get_dashboard_llm_usage.return_value = {
            "total_requests": 0,
            "total_cost": 0,
            "models": [],
        }
        mock_stock_repo.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get("/v1/dashboard/home")

        assert resp.status_code == 200
        data = resp.json()
        for key in (
            "watchlist",
            "forecasts",
            "analysis",
            "llm_usage",
        ):
            assert key in data


# ---------------------------------------------------------------
# Chart: OHLCV
# ---------------------------------------------------------------


class TestChartOHLCV:
    """GET /v1/dashboard/chart/ohlcv."""

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_happy_path(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """Returns OHLCV points for a ticker."""
        repo = MagicMock()
        df = pd.DataFrame(
            [
                {
                    "date": "2024-01-01",
                    "open": 150.0,
                    "high": 155.0,
                    "low": 148.0,
                    "close": 153.0,
                    "volume": 1000000,
                },
            ]
        )
        repo.get_ohlcv.return_value = df
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/ohlcv" "?ticker=AAPL",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ticker"] == "AAPL"
        assert len(data["data"]) == 1
        assert data["data"][0]["close"] == 153.0

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_empty_data(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """No OHLCV data → empty response."""
        repo = MagicMock()
        repo.get_ohlcv.return_value = pd.DataFrame()
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/ohlcv" "?ticker=AAPL",
        )

        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ---------------------------------------------------------------
# Chart: Indicators
# ---------------------------------------------------------------


class TestChartIndicators:
    """GET /v1/dashboard/chart/indicators."""

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_happy_path(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """Returns indicator points."""
        repo = MagicMock()
        df = pd.DataFrame(
            [
                {
                    "date": "2024-01-01",
                    "sma_50": 148.0,
                    "sma_200": 145.0,
                    "ema_20": 150.0,
                    "rsi_14": 55.0,
                    "macd": 1.5,
                    "macd_signal": 1.2,
                    "macd_hist": 0.3,
                    "bb_upper": 160.0,
                    "bb_lower": 140.0,
                },
            ]
        )
        repo.get_technical_indicators.return_value = df
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/indicators" "?ticker=AAPL",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ticker"] == "AAPL"
        assert len(data["data"]) == 1
        assert data["data"][0]["rsi_14"] == 55.0

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_empty_data(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """No indicators → empty response."""
        repo = MagicMock()
        repo.get_technical_indicators.return_value = pd.DataFrame()
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/indicators" "?ticker=AAPL",
        )

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @patch(
        "tools._analysis_movement._analyse_price_movement",
    )
    @patch("tools._analysis_shared.compute_indicators")
    @patch("dashboard_routes.get_cache")
    def test_returns_sr_levels(
        self,
        mock_cache_fn,
        mock_compute,
        mock_movement,
        client,
    ):
        """S/R levels appear in response when OHLCV exists."""
        mock_compute.return_value = pd.DataFrame(
            [
                {
                    "Close": 2500.0,
                    "SMA_50": None,
                    "SMA_200": None,
                    "EMA_20": None,
                    "RSI_14": None,
                    "MACD": None,
                    "MACD_Signal": None,
                    "MACD_Hist": None,
                    "BB_Upper": None,
                    "BB_Lower": None,
                }
            ],
            index=pd.DatetimeIndex(["2024-01-01"]),
        )
        mock_movement.return_value = {
            "support_levels": [1950.0, 2080.0, 2210.0],
            "resistance_levels": [
                2840.0,
                2710.0,
                2580.0,
            ],
        }

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/indicators" "?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["support_levels"] == [
            1950.0,
            2080.0,
            2210.0,
        ]
        assert body["resistance_levels"] == [
            2840.0,
            2710.0,
            2580.0,
        ]

    @patch("tools._analysis_movement._analyse_price_movement")
    @patch("tools._analysis_shared.compute_indicators")
    @patch("dashboard_routes.get_cache")
    def test_rsi_2_in_response(
        self,
        mock_cache_fn,
        mock_compute,
        mock_movement,
        client,
    ):
        """rsi_2 is surfaced per IndicatorPoint."""
        mock_compute.return_value = pd.DataFrame(
            [
                {
                    "Close": 2500.0,
                    "SMA_50": None,
                    "SMA_200": None,
                    "EMA_20": None,
                    "RSI_14": 55.0,
                    "RSI_2": 87.3,
                    "MACD": None,
                    "MACD_Signal": None,
                    "MACD_Hist": None,
                    "BB_Upper": None,
                    "BB_Lower": None,
                }
            ],
            index=pd.DatetimeIndex(["2024-01-01"]),
        )
        mock_movement.return_value = {
            "support_levels": [],
            "resistance_levels": [],
        }
        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/indicators?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["rsi_2"] == 87.3
        # Sanity: rsi_14 still flows through untouched.
        assert body["data"][0]["rsi_14"] == 55.0

    @patch(
        "tools._analysis_movement._analyse_price_movement",
    )
    @patch("tools._analysis_shared.compute_indicators")
    @patch("dashboard_routes.get_cache")
    def test_short_history_returns_partial_levels(
        self,
        mock_cache_fn,
        mock_compute,
        mock_movement,
        client,
    ):
        """Newly-listed ticker may yield <3 levels."""
        mock_compute.return_value = pd.DataFrame(
            [
                {
                    "Close": 100.0,
                    "SMA_50": None,
                    "SMA_200": None,
                    "EMA_20": None,
                    "RSI_14": None,
                    "MACD": None,
                    "MACD_Signal": None,
                    "MACD_Hist": None,
                    "BB_Upper": None,
                    "BB_Lower": None,
                }
            ],
            index=pd.DatetimeIndex(["2024-01-01"]),
        )
        mock_movement.return_value = {
            "support_levels": [95.0],
            "resistance_levels": [105.0, 110.0],
        }

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/indicators" "?ticker=NEW.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["support_levels"] == [95.0]
        assert body["resistance_levels"] == [
            105.0,
            110.0,
        ]

    @patch("tools._analysis_shared.compute_indicators")
    @patch("dashboard_routes.get_cache")
    def test_empty_ohlcv_returns_empty_levels(
        self,
        mock_cache_fn,
        mock_compute,
        client,
    ):
        """Unknown ticker — both arrays empty."""
        mock_compute.return_value = None

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/indicators" "?ticker=UNKNOWN.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["support_levels"] == []
        assert body["resistance_levels"] == []

    @patch(
        "tools._analysis_movement._analyse_price_movement",
    )
    @patch("tools._analysis_shared.compute_indicators")
    @patch("dashboard_routes.get_cache")
    def test_cache_hit_skips_recompute(
        self,
        mock_cache_fn,
        mock_compute,
        mock_movement,
        client,
    ):
        """Second request served from Redis."""
        cached = (
            '{"ticker":"RELIANCE.NS","data":[],'
            '"support_levels":[1950.0,2080.0,2210.0],'
            '"resistance_levels":'
            "[2840.0,2710.0,2580.0]}"
        )
        cache = MagicMock()
        cache.get.return_value = cached
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/indicators" "?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["support_levels"] == [
            1950.0,
            2080.0,
            2210.0,
        ]
        mock_compute.assert_not_called()
        mock_movement.assert_not_called()


# ---------------------------------------------------------------
# Chart: Forecast Series
# ---------------------------------------------------------------


class TestChartForecastSeries:
    """GET /v1/dashboard/chart/forecast-series."""

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_happy_path(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """Returns forecast points with bands."""
        repo = MagicMock()
        df = pd.DataFrame(
            [
                {
                    "forecast_date": "2024-03-01",
                    "predicted_price": 165.0,
                    "lower_bound": 155.0,
                    "upper_bound": 175.0,
                },
                {
                    "forecast_date": "2024-04-01",
                    "predicted_price": 170.0,
                    "lower_bound": 158.0,
                    "upper_bound": 182.0,
                },
            ]
        )
        repo.get_latest_forecast_series.return_value = df
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/forecast-series" "?ticker=AAPL&horizon=9",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ticker"] == "AAPL"
        assert data["horizon_months"] == 9
        assert len(data["data"]) == 2
        assert data["data"][0]["predicted"] == 165.0

    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_empty_data(
        self,
        mock_repo_fn,
        mock_cache_fn,
        client,
    ):
        """No forecast data → empty response."""
        repo = MagicMock()
        repo.get_latest_forecast_series.return_value = pd.DataFrame()
        mock_repo_fn.return_value = repo

        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/forecast-series" "?ticker=AAPL&horizon=9",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []
        assert data["horizon_months"] == 9


# ---------------------------------------------------------------
# Chart: OHLCV Live Overlay (Kite today-bar)
# ---------------------------------------------------------------


class TestChartOHLCVLiveOverlay:
    """GET /v1/dashboard/chart/ohlcv — Kite live overlay (market hours)."""

    def _mock_repo_returns_one_row(self, mock_repo_fn):
        repo = MagicMock()
        repo.get_ohlcv.return_value = pd.DataFrame(
            [
                {
                    "date": "2026-06-01",
                    "open": 2868.0, "high": 2870.0,
                    "low": 2867.0, "close": 2869.0,
                    "volume": 50_000,
                },
            ],
        )
        mock_repo_fn.return_value = repo
        return repo

    def _mock_cache(self, mock_cache_fn):
        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache
        return cache

    @patch(
        "dashboard_kite_overlay._try_kite_quote",
        new_callable=AsyncMock,
    )
    @patch("dashboard_routes.is_market_open")
    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_live_path_sets_is_live_true(
        self,
        mock_repo_fn,
        mock_cache_fn,
        mock_market_open,
        mock_kite,
        client,
    ):
        mock_market_open.return_value = True
        mock_kite.return_value = {
            "open": 2870.0, "high": 2895.3,
            "low": 2861.5, "close": 2882.1,
            "last_price": 2884.4, "volume": 1_842_900,
            "last_trade_time": None,
        }
        self._mock_repo_returns_one_row(mock_repo_fn)
        cache = self._mock_cache(mock_cache_fn)

        resp = client.get(
            "/v1/dashboard/chart/ohlcv?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_live"] is True
        # cache key includes user_id; TTL is TTL_MARKET_LIVE (30).
        assert cache.set.call_count == 1
        cache_key = cache.set.call_args.args[0]
        ttl = cache.set.call_args.args[2]
        assert "RELIANCE.NS" in cache_key
        assert cache_key != "cache:chart:ohlcv:RELIANCE.NS"
        assert ttl == 30

    @patch(
        "dashboard_kite_overlay._try_kite_quote",
        new_callable=AsyncMock,
    )
    @patch("dashboard_routes.is_market_open")
    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_market_closed_skips_overlay(
        self,
        mock_repo_fn,
        mock_cache_fn,
        mock_market_open,
        mock_kite,
        client,
    ):
        mock_market_open.return_value = False
        self._mock_repo_returns_one_row(mock_repo_fn)
        cache = self._mock_cache(mock_cache_fn)

        resp = client.get(
            "/v1/dashboard/chart/ohlcv?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        assert resp.json()["is_live"] is False
        mock_kite.assert_not_called()
        # shared cache key + TTL_STABLE (300)
        cache_key = cache.set.call_args.args[0]
        ttl = cache.set.call_args.args[2]
        assert cache_key == "cache:chart:ohlcv:RELIANCE.NS"
        assert ttl == 300

    @patch(
        "dashboard_kite_overlay._try_kite_quote",
        new_callable=AsyncMock,
    )
    @patch("dashboard_routes.is_market_open")
    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_us_ticker_skips_overlay(
        self,
        mock_repo_fn,
        mock_cache_fn,
        mock_market_open,
        mock_kite,
        client,
    ):
        mock_market_open.return_value = True
        self._mock_repo_returns_one_row(mock_repo_fn)
        self._mock_cache(mock_cache_fn)

        resp = client.get(
            "/v1/dashboard/chart/ohlcv?ticker=AAPL",
        )

        assert resp.status_code == 200
        assert resp.json()["is_live"] is False
        mock_kite.assert_not_called()

    @patch(
        "dashboard_kite_overlay._try_kite_quote",
        new_callable=AsyncMock,
    )
    @patch("dashboard_routes.is_market_open")
    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_kite_returns_none_falls_back(
        self,
        mock_repo_fn,
        mock_cache_fn,
        mock_market_open,
        mock_kite,
        client,
    ):
        mock_market_open.return_value = True
        mock_kite.return_value = None
        self._mock_repo_returns_one_row(mock_repo_fn)
        cache = self._mock_cache(mock_cache_fn)

        resp = client.get(
            "/v1/dashboard/chart/ohlcv?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_live"] is False
        # User-fallback path: shared key + TTL_STABLE.
        cache_key = cache.set.call_args.args[0]
        ttl = cache.set.call_args.args[2]
        assert cache_key == "cache:chart:ohlcv:RELIANCE.NS"
        assert ttl == 300
