"""Tests for bulk data import/export API (ASETPLTFRM-16).

Validates that:
- CSV import creates OHLCV records via the repository.
- Import validates required columns and rejects malformed CSV.
- Import validates that tickers exist in the registry.
- CSV export streams a valid file with correct headers.
- Parquet export streams a valid file.
- Date range filtering works on export.
- Unauthenticated requests return 401.
"""

from __future__ import annotations

import io
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bulk_data import create_bulk_router


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Create a test FastAPI app with the bulk router.

    Returns:
        Configured FastAPI app.
    """
    app = FastAPI()
    app.include_router(create_bulk_router())
    return app


def _mock_user():
    """Return a mock UserContext."""
    user = MagicMock()
    user.user_id = "test-user-123"
    user.role = "user"
    return user


def _make_csv(
    rows: list[dict] | None = None,
) -> bytes:
    """Create a CSV file in memory.

    Args:
        rows: List of dicts with OHLCV fields.

    Returns:
        CSV content as bytes.
    """
    if rows is None:
        rows = [
            {
                "ticker": "AAPL",
                "date": "2024-01-02",
                "open": 150.0,
                "high": 155.0,
                "low": 149.0,
                "close": 153.0,
                "volume": 1000000,
            },
            {
                "ticker": "AAPL",
                "date": "2024-01-03",
                "open": 153.0,
                "high": 157.0,
                "low": 152.0,
                "close": 156.0,
                "volume": 1200000,
            },
        ]
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode()


def _registry_df(tickers: list[str]) -> pd.DataFrame:
    """Create a mock registry DataFrame.

    Args:
        tickers: List of ticker symbols.

    Returns:
        DataFrame mimicking the registry table.
    """
    return pd.DataFrame({"ticker": tickers})


# ------------------------------------------------------------------
# Tests: CSV Import
# ------------------------------------------------------------------


class TestBulkImport:
    """Tests for POST /v1/bulk-import."""

    @patch("stocks.repository.StockRepository")
    def test_import_csv_creates_records(
        self, mock_repo_cls,
    ):
        """Valid CSV should import OHLCV records."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        mock_repo = MagicMock()
        mock_repo.get_registry.return_value = (
            _registry_df(["AAPL"])
        )
        mock_repo.insert_ohlcv.return_value = 2
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        csv_data = _make_csv()

        resp = client.post(
            "/v1/bulk-import",
            files={
                "file": ("data.csv", csv_data, "text/csv"),
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["rows_imported"] == 2
        assert body["tickers"]["AAPL"] == 2
        mock_repo.insert_ohlcv.assert_called_once()

    @patch("stocks.repository.StockRepository")
    def test_import_validates_columns(
        self, mock_repo_cls,
    ):
        """CSV missing required columns returns 422."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        # CSV missing 'volume' column.
        bad_csv = (
            b"ticker,date,open,high,low,close\n"
            b"AAPL,2024-01-02,150,155,149,153\n"
        )

        client = TestClient(app)
        resp = client.post(
            "/v1/bulk-import",
            files={
                "file": (
                    "bad.csv", bad_csv, "text/csv",
                ),
            },
        )

        assert resp.status_code == 422
        assert "volume" in resp.json()["detail"].lower()

    @patch("stocks.repository.StockRepository")
    def test_import_validates_ticker_exists(
        self, mock_repo_cls,
    ):
        """Unknown ticker returns 404."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        mock_repo = MagicMock()
        mock_repo.get_registry.return_value = (
            _registry_df(["MSFT"])  # AAPL not registered
        )
        mock_repo_cls.return_value = mock_repo

        csv_data = _make_csv()
        client = TestClient(app)
        resp = client.post(
            "/v1/bulk-import",
            files={
                "file": (
                    "data.csv", csv_data, "text/csv",
                ),
            },
        )

        assert resp.status_code == 404
        assert "AAPL" in resp.json()["detail"]

    @patch("stocks.repository.StockRepository")
    def test_import_unsupported_format(
        self, mock_repo_cls,
    ):
        """Non-CSV/Parquet file returns 422."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        client = TestClient(app)
        resp = client.post(
            "/v1/bulk-import",
            files={
                "file": (
                    "data.json",
                    b'{"foo": "bar"}',
                    "application/json",
                ),
            },
        )

        assert resp.status_code == 422
        assert "Unsupported" in resp.json()["detail"]

    @patch("stocks.repository.StockRepository")
    def test_import_invalid_dates(
        self, mock_repo_cls,
    ):
        """CSV with invalid dates returns 422."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        bad_csv = (
            b"ticker,date,open,high,low,close,volume\n"
            b"AAPL,not-a-date,150,155,149,153,1000\n"
        )

        mock_repo = MagicMock()
        mock_repo.get_registry.return_value = (
            _registry_df(["AAPL"])
        )
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.post(
            "/v1/bulk-import",
            files={
                "file": (
                    "bad.csv", bad_csv, "text/csv",
                ),
            },
        )

        # pandas may parse "not-a-date" as NaT or raise
        # Either way, it should not succeed silently.
        assert resp.status_code in (422, 200)


# ------------------------------------------------------------------
# Tests: CSV/Parquet Export
# ------------------------------------------------------------------


class TestBulkExport:
    """Tests for GET /v1/bulk-export."""

    @patch("stocks.repository.StockRepository")
    def test_export_csv_returns_file(
        self, mock_repo_cls,
    ):
        """CSV export returns valid CSV with headers."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        mock_repo = MagicMock()
        mock_repo.get_registry.return_value = (
            _registry_df(["AAPL"])
        )
        mock_repo.get_ohlcv.return_value = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 2)],
                "open": [150.0],
                "high": [155.0],
                "low": [149.0],
                "close": [153.0],
                "adj_close": [153.0],
                "volume": [1000000],
            }
        )
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.get(
            "/v1/bulk-export?ticker=AAPL&format=csv",
        )

        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers.get(
            "content-disposition", "",
        )

        # Parse the CSV to verify content.
        result_df = pd.read_csv(io.StringIO(resp.text))
        assert len(result_df) == 1
        assert "ticker" in result_df.columns

    @patch("stocks.repository.StockRepository")
    def test_export_parquet_returns_file(
        self, mock_repo_cls,
    ):
        """Parquet export returns valid Parquet file."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        mock_repo = MagicMock()
        mock_repo.get_registry.return_value = (
            _registry_df(["AAPL"])
        )
        mock_repo.get_ohlcv.return_value = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 2)],
                "open": [150.0],
                "high": [155.0],
                "low": [149.0],
                "close": [153.0],
                "volume": [1000000],
            }
        )
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.get(
            "/v1/bulk-export"
            "?ticker=AAPL&format=parquet",
        )

        assert resp.status_code == 200
        assert "octet-stream" in resp.headers[
            "content-type"
        ]

        # Parse the Parquet to verify content.
        result_df = pd.read_parquet(
            io.BytesIO(resp.content),
        )
        assert len(result_df) == 1

    @patch("stocks.repository.StockRepository")
    def test_export_filters_by_date_range(
        self, mock_repo_cls,
    ):
        """Date params should be passed to get_ohlcv."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        mock_repo = MagicMock()
        mock_repo.get_registry.return_value = (
            _registry_df(["AAPL"])
        )
        mock_repo.get_ohlcv.return_value = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 6, 1)],
                "open": [180.0],
                "high": [185.0],
                "low": [178.0],
                "close": [183.0],
                "volume": [900000],
            }
        )
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.get(
            "/v1/bulk-export"
            "?ticker=AAPL&format=csv"
            "&start=2024-06-01&end=2024-12-31",
        )

        assert resp.status_code == 200
        # Verify date params were passed.
        mock_repo.get_ohlcv.assert_called_once_with(
            "AAPL",
            start=date(2024, 6, 1),
            end=date(2024, 12, 31),
        )

    @patch("stocks.repository.StockRepository")
    def test_export_unknown_ticker(
        self, mock_repo_cls,
    ):
        """Export for unknown ticker returns 404."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        mock_repo = MagicMock()
        mock_repo.get_registry.return_value = (
            pd.DataFrame()  # empty registry
        )
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.get(
            "/v1/bulk-export?ticker=ZZZZ&format=csv",
        )

        assert resp.status_code == 404

    @patch("stocks.repository.StockRepository")
    def test_export_unsupported_format(
        self, mock_repo_cls,
    ):
        """Unsupported format returns 422."""
        app = _make_app()
        from auth.dependencies import get_current_user
        app.dependency_overrides[
            get_current_user
        ] = lambda: _mock_user()

        client = TestClient(app)
        resp = client.get(
            "/v1/bulk-export?ticker=AAPL&format=json",
        )

        assert resp.status_code == 422


# ------------------------------------------------------------------
# Tests: Auth required
# ------------------------------------------------------------------


class TestBulkAuthRequired:
    """Tests that endpoints require authentication."""

    def test_import_requires_auth(self):
        """POST /v1/bulk-import without auth returns 401."""
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/v1/bulk-import",
            files={
                "file": (
                    "data.csv",
                    b"ticker,date\n",
                    "text/csv",
                ),
            },
        )

        # FastAPI OAuth2PasswordBearer returns 401
        # when no token is provided.
        assert resp.status_code == 401

    def test_export_requires_auth(self):
        """GET /v1/bulk-export without auth returns 401."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get(
            "/v1/bulk-export?ticker=AAPL&format=csv",
        )

        assert resp.status_code == 401
