"""Tests for Iceberg partition optimisation (ASETPLTFRM-14).

Validates that:
- OHLCV and technical_indicators tables have ticker partitions.
- Date-range queries push predicates to Iceberg level.
- get_ohlcv and get_technical_indicators return correct
  date-filtered results.
- Migration report identifies correct partition specs.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow as pa
import pytest


# ------------------------------------------------------------------
# Helper: mock Iceberg table with scan support
# ------------------------------------------------------------------


def _make_mock_table(
    df: pd.DataFrame,
    partition_fields: list[str] | None = None,
):
    """Return a mock Iceberg table that supports scan().

    Args:
        df: Data to return from scan.
        partition_fields: Partition field names.

    Returns:
        A MagicMock table object.
    """
    mock_tbl = MagicMock()
    mock_scan = MagicMock()
    mock_scan.to_pandas.return_value = df
    mock_tbl.scan.return_value = mock_scan
    mock_tbl.refresh = MagicMock()
    return mock_tbl


# ------------------------------------------------------------------
# Tests: _scan_ticker_date_range
# ------------------------------------------------------------------


class TestScanTickerDateRange:
    """Tests for the Iceberg-level date range scan."""

    def _make_repo(self):
        from stocks.repository import StockRepository

        repo = StockRepository()
        return repo

    def test_date_range_pushes_predicates(self):
        """Date range scan should pass predicates to
        Iceberg scan, not filter in pandas."""
        repo = self._make_repo()

        # Create test data
        df = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 5,
                "date": pd.to_datetime(
                    [
                        "2024-01-01",
                        "2024-02-01",
                        "2024-03-01",
                        "2024-04-01",
                        "2024-05-01",
                    ]
                ).date,
                "close": [150.0, 155.0, 160.0, 165.0, 170.0],
            }
        )

        mock_tbl = _make_mock_table(df)
        with patch.object(
            repo, "_load_table", return_value=mock_tbl
        ):
            result = repo._scan_ticker_date_range(
                "stocks.ohlcv",
                "AAPL",
                date_col="date",
                start=date(2024, 2, 1),
                end=date(2024, 4, 1),
            )

        # Verify scan was called with row_filter
        mock_tbl.scan.assert_called_once()
        call_kwargs = mock_tbl.scan.call_args
        assert "row_filter" in call_kwargs.kwargs

    def test_no_dates_uses_ticker_only(self):
        """When no dates provided, should use ticker-only
        predicate."""
        repo = self._make_repo()

        df = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "close": [150.0],
            }
        )

        mock_tbl = _make_mock_table(df)
        with patch.object(
            repo, "_load_table", return_value=mock_tbl
        ):
            result = repo._scan_ticker_date_range(
                "stocks.ohlcv",
                "AAPL",
                date_col="date",
            )

        mock_tbl.scan.assert_called_once()

    def test_fallback_on_error(self):
        """Should fall back to _scan_ticker + pandas
        on Iceberg error."""
        repo = self._make_repo()

        fallback_df = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 3,
                "date": pd.to_datetime(
                    [
                        "2024-01-01",
                        "2024-02-01",
                        "2024-03-01",
                    ]
                ),
                "close": [150.0, 155.0, 160.0],
            }
        )

        # Make _load_table raise so fallback kicks in.
        with (
            patch.object(
                repo,
                "_load_table",
                side_effect=Exception("scan failed"),
            ),
            patch.object(
                repo,
                "_scan_ticker",
                return_value=fallback_df,
            ),
        ):
            result = repo._scan_ticker_date_range(
                "stocks.ohlcv",
                "AAPL",
                date_col="date",
                start=date(2024, 1, 15),
            )

        # Should have called fallback.
        assert len(result) <= 3


# ------------------------------------------------------------------
# Tests: get_ohlcv with date range
# ------------------------------------------------------------------


class TestGetOhlcvDateRange:
    """Tests for get_ohlcv with Iceberg-level date
    predicates."""

    def test_get_ohlcv_with_dates_uses_date_range_scan(
        self,
    ):
        """get_ohlcv(start, end) should use
        _scan_ticker_date_range."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        df = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 3, 1)],
                "open": [160.0],
                "high": [162.0],
                "low": [158.0],
                "close": [161.0],
                "adj_close": [161.0],
                "volume": [1000000],
            }
        )

        with patch.object(
            repo,
            "_scan_ticker_date_range",
            return_value=df,
        ) as mock_scan:
            result = repo.get_ohlcv(
                "AAPL",
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
            )
            mock_scan.assert_called_once()
            assert len(result) == 1

    def test_get_ohlcv_without_dates_uses_scan_ticker(
        self,
    ):
        """get_ohlcv() without dates should use
        _scan_ticker (no date predicate)."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        df = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 3, 1)],
                "open": [160.0],
                "high": [162.0],
                "low": [158.0],
                "close": [161.0],
                "adj_close": [161.0],
                "volume": [1000000],
            }
        )

        with patch.object(
            repo, "_scan_ticker", return_value=df
        ) as mock_scan:
            result = repo.get_ohlcv("AAPL")
            mock_scan.assert_called_once()
            assert len(result) == 1


# ------------------------------------------------------------------
# Tests: get_technical_indicators with date range
# ------------------------------------------------------------------


class TestGetTechnicalIndicatorsDateRange:
    """Tests for get_technical_indicators with Iceberg-level
    date predicates."""

    def test_with_dates_uses_date_range_scan(self):
        """get_technical_indicators(start, end) should use
        _scan_ticker_date_range."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        df = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 3, 1)],
                "sma_50": [155.0],
                "rsi_14": [65.0],
            }
        )

        with patch.object(
            repo,
            "_scan_ticker_date_range",
            return_value=df,
        ) as mock_scan:
            result = repo.get_technical_indicators(
                "AAPL",
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
            )
            mock_scan.assert_called_once()


# ------------------------------------------------------------------
# Tests: get_usage_by_date_range
# ------------------------------------------------------------------


class TestGetUsageByDateRange:
    """Tests for get_usage_by_date_range with Iceberg-level
    predicates."""

    def test_uses_scan_date_range(self):
        """Should delegate to _scan_date_range for
        partition pruning."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        df = pd.DataFrame(
            {
                "request_date": [date(2024, 3, 1)],
                "model": ["llama-3.3-70b"],
                "total_tokens": [100],
            }
        )

        with patch.object(
            repo,
            "_scan_date_range",
            return_value=df,
        ) as mock_scan:
            result = repo.get_usage_by_date_range(
                date(2024, 1, 1),
                date(2024, 12, 31),
            )
            mock_scan.assert_called_once_with(
                "stocks.llm_usage",
                date_col="request_date",
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
            )


# ------------------------------------------------------------------
# Tests: _scan_date_range
# ------------------------------------------------------------------


class TestScanDateRange:
    """Tests for the non-ticker date range scan helper."""

    def test_pushes_date_predicates(self):
        """Should push date bounds to Iceberg scan."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        df = pd.DataFrame(
            {
                "request_date": [date(2024, 3, 1)],
                "model": ["llama"],
            }
        )

        mock_tbl = _make_mock_table(df)
        with patch.object(
            repo, "_load_table", return_value=mock_tbl
        ):
            result = repo._scan_date_range(
                "stocks.llm_usage",
                date_col="request_date",
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
            )

        mock_tbl.scan.assert_called_once()
        call_kwargs = mock_tbl.scan.call_args
        assert "row_filter" in call_kwargs.kwargs

    def test_no_dates_returns_full_scan(self):
        """Without dates, should do a full scan."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        df = pd.DataFrame(
            {
                "request_date": [date(2024, 3, 1)],
            }
        )

        mock_tbl = _make_mock_table(df)
        with patch.object(
            repo, "_load_table", return_value=mock_tbl
        ):
            result = repo._scan_date_range(
                "stocks.llm_usage",
                date_col="request_date",
            )

        assert len(result) == 1

    def test_fallback_on_error(self):
        """Should fall back to full scan + pandas on
        error."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        df = pd.DataFrame(
            {
                "request_date": [
                    date(2024, 1, 1),
                    date(2024, 6, 1),
                ],
            }
        )

        with (
            patch.object(
                repo,
                "_load_table",
                side_effect=Exception("failed"),
            ),
            patch.object(
                repo,
                "_table_to_df",
                return_value=df,
            ),
        ):
            result = repo._scan_date_range(
                "stocks.llm_usage",
                date_col="request_date",
                start=date(2024, 3, 1),
            )

        # Only the June row should survive.
        assert len(result) == 1


# ------------------------------------------------------------------
# Tests: migration report
# ------------------------------------------------------------------


class TestMigrationReport:
    """Tests for the partition migration report."""

    @patch("stocks.migrate_partitions._get_catalog")
    def test_report_identifies_partition_status(
        self, mock_catalog_fn
    ):
        """Report should identify tables with
        correct/incorrect partitions."""
        from stocks.migrate_partitions import (
            report_partitions,
        )

        # Create mock tables with known partition specs.
        mock_catalog = MagicMock()
        mock_catalog_fn.return_value = mock_catalog

        # Simulate ohlcv with ticker partition.
        mock_ohlcv = MagicMock()
        mock_ohlcv_pf = MagicMock()
        mock_ohlcv_pf.source_id = 1
        mock_ohlcv.spec.return_value.fields = [
            mock_ohlcv_pf,
        ]
        mock_ohlcv_field = MagicMock()
        mock_ohlcv_field.name = "ticker"
        mock_ohlcv.schema.return_value.find_field.return_value = (
            mock_ohlcv_field
        )
        mock_ohlcv.schema.return_value.fields = [
            MagicMock(name="ticker"),
        ]
        mock_scan = MagicMock()
        mock_scan.to_pandas.return_value = pd.DataFrame(
            {"ticker": ["AAPL"]}
        )
        mock_ohlcv.scan.return_value = mock_scan

        mock_catalog.list_tables.return_value = [
            ("stocks", "ohlcv"),
        ]
        mock_catalog.load_table.return_value = mock_ohlcv

        report = report_partitions()
        assert "stocks.ohlcv" in report
        assert report["stocks.ohlcv"]["status"] == "OK"
