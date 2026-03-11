"""Tests for Iceberg commit retry logic in StockRepository.

Verifies that ``_retry_commit`` retries on
``CommitFailedException`` with exponential backoff,
and that ``_append_rows`` / ``_overwrite_table`` delegate
correctly.
"""

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from pyiceberg.exceptions import CommitFailedException


class TestRetryCommit:
    """Tests for :meth:`StockRepository._retry_commit`."""

    def _make_repo(self):
        """Create a StockRepository with mocked catalog."""
        from stocks.repository import StockRepository

        repo = StockRepository()
        repo._catalog = MagicMock()
        return repo

    def test_succeeds_on_first_attempt(self):
        """No retry needed when append succeeds immediately."""
        repo = self._make_repo()
        mock_table = MagicMock()

        with patch.object(repo, "_load_table", return_value=mock_table):
            repo._retry_commit("stocks.ohlcv", "append", pa.table({}))

        mock_table.append.assert_called_once()

    @patch("stocks.repository.time.sleep")
    def test_retries_on_commit_conflict(self, mock_sleep):
        """Retries up to _MAX_RETRIES on CommitFailedException."""
        repo = self._make_repo()
        mock_table = MagicMock()
        mock_table.append.side_effect = [
            CommitFailedException("conflict 1"),
            CommitFailedException("conflict 2"),
            None,  # success on 3rd attempt
        ]

        with patch.object(repo, "_load_table", return_value=mock_table):
            repo._retry_commit("stocks.ohlcv", "append", pa.table({}))

        assert mock_table.append.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("stocks.repository.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep):
        """Raises CommitFailedException after exhausting retries."""
        repo = self._make_repo()
        mock_table = MagicMock()
        mock_table.overwrite.side_effect = CommitFailedException(
            "persistent conflict"
        )

        with (
            patch.object(
                repo,
                "_load_table",
                return_value=mock_table,
            ),
            pytest.raises(CommitFailedException),
        ):
            repo._retry_commit(
                "stocks.registry",
                "overwrite",
                pa.table({}),
            )

        # 1 initial + 3 retries = 4 total
        assert mock_table.overwrite.call_count == 4
        assert mock_sleep.call_count == 3

    @patch("stocks.repository.time.sleep")
    def test_reloads_table_on_each_retry(self, mock_sleep):
        """Table is reloaded on each attempt for a fresh snapshot."""
        repo = self._make_repo()
        mock_table = MagicMock()
        mock_table.append.side_effect = [
            CommitFailedException("stale"),
            None,
        ]

        with patch.object(
            repo, "_load_table", return_value=mock_table
        ) as mock_load:
            repo._retry_commit("stocks.ohlcv", "append", pa.table({}))

        # _load_table called once per attempt
        assert mock_load.call_count == 2


class TestAppendRowsRetry:
    """Tests for :meth:`StockRepository._append_rows`."""

    @patch("stocks.repository.time.sleep")
    def test_append_rows_retries(self, mock_sleep):
        """_append_rows retries on CommitFailedException."""
        from stocks.repository import StockRepository

        repo = StockRepository()
        mock_table = MagicMock()
        mock_table.append.side_effect = [
            CommitFailedException("conflict"),
            None,
        ]

        with patch.object(repo, "_load_table", return_value=mock_table):
            repo._append_rows("stocks.ohlcv", pa.table({}))

        assert mock_table.append.call_count == 2


class TestOverwriteTableRetry:
    """Tests for :meth:`StockRepository._overwrite_table`."""

    @patch("stocks.repository.time.sleep")
    def test_overwrite_table_retries(self, mock_sleep):
        """_overwrite_table retries on CommitFailedException."""
        from stocks.repository import StockRepository

        repo = StockRepository()
        mock_table = MagicMock()
        mock_table.overwrite.side_effect = [
            CommitFailedException("conflict"),
            None,
        ]

        with patch.object(repo, "_load_table", return_value=mock_table):
            repo._overwrite_table("stocks.registry", pa.table({}))

        assert mock_table.overwrite.call_count == 2


class TestDirtyTableRefresh:
    """Tests for read-after-write snapshot refresh."""

    def _make_repo(self):
        from stocks.repository import StockRepository

        repo = StockRepository()
        repo._catalog = MagicMock()
        return repo

    def test_commit_marks_table_dirty(self):
        """Successful commit adds identifier to _dirty_tables."""
        repo = self._make_repo()
        mock_table = MagicMock()

        with patch.object(repo, "_load_table", return_value=mock_table):
            repo._retry_commit("stocks.ohlcv", "append", pa.table({}))

        assert "stocks.ohlcv" in repo._dirty_tables

    def test_scan_ticker_refreshes_dirty_table(self):
        """_scan_ticker calls tbl.refresh() for dirty tables."""
        repo = self._make_repo()
        repo._dirty_tables.add("stocks.ohlcv")
        mock_table = MagicMock()
        mock_table.scan.return_value.to_pandas.return_value = MagicMock()

        with patch.object(repo, "_load_table", return_value=mock_table):
            repo._scan_ticker("stocks.ohlcv", "AAPL")

        mock_table.refresh.assert_called_once()
        assert "stocks.ohlcv" not in repo._dirty_tables

    def test_scan_ticker_skips_refresh_clean_table(self):
        """_scan_ticker does NOT call refresh for clean tables."""
        repo = self._make_repo()
        mock_table = MagicMock()
        mock_table.scan.return_value.to_pandas.return_value = MagicMock()

        with patch.object(repo, "_load_table", return_value=mock_table):
            repo._scan_ticker("stocks.ohlcv", "AAPL")

        mock_table.refresh.assert_not_called()

    def test_table_to_df_refreshes_dirty_table(self):
        """_table_to_df calls tbl.refresh() for dirty tables."""
        repo = self._make_repo()
        repo._dirty_tables.add("stocks.registry")
        mock_table = MagicMock()
        mock_table.scan.return_value.to_pandas.return_value = MagicMock()

        with patch.object(repo, "_load_table", return_value=mock_table):
            repo._table_to_df("stocks.registry")

        mock_table.refresh.assert_called_once()
        assert "stocks.registry" not in repo._dirty_tables

    @patch("stocks.repository.time.sleep")
    def test_failed_commit_does_not_dirty(self, mock_sleep):
        """Failed commit (all retries exhausted) leaves table clean."""
        repo = self._make_repo()
        mock_table = MagicMock()
        mock_table.append.side_effect = CommitFailedException("fail")

        with (
            patch.object(
                repo,
                "_load_table",
                return_value=mock_table,
            ),
            pytest.raises(CommitFailedException),
        ):
            repo._retry_commit("stocks.ohlcv", "append", pa.table({}))

        assert "stocks.ohlcv" not in repo._dirty_tables
