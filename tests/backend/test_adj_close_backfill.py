"""Unit tests for adj_close backfill: repository method + script.

Tests cover :meth:`StockRepository.update_ohlcv_adj_close`
(scoped delete-and-append of the adj_close column) and the
one-time backfill script ``stocks/backfill_adj_close.py``.

All Iceberg / parquet I/O is mocked — the suite runs offline.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_iceberg_ohlcv(
    rows: int = 50,
    ticker: str = "AAPL",
    adj_close_nan: bool = False,
) -> pd.DataFrame:
    """Return a DataFrame shaped like ``StockRepository.get_ohlcv()``.

    Args:
        rows: Number of rows.
        ticker: Ticker symbol.
        adj_close_nan: If True, ``adj_close`` is all NaN.
    """
    idx = pd.date_range("2020-01-01", periods=rows, freq="B")
    rng = np.random.default_rng(0)
    close = 100 + rng.standard_normal(rows).cumsum()
    return pd.DataFrame(
        {
            "ticker": [ticker] * rows,
            "date": idx.date,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "adj_close": ([float("nan")] * rows if adj_close_nan else close),
            "volume": rng.integers(1_000_000, 5_000_000, rows),
        }
    )


def _make_multi_ticker_ohlcv() -> pd.DataFrame:
    """Return Iceberg OHLCV with two tickers (AAPL + MSFT)."""
    df_a = _make_iceberg_ohlcv(30, "AAPL", adj_close_nan=True)
    df_m = _make_iceberg_ohlcv(20, "MSFT", adj_close_nan=False)
    return pd.concat([df_a, df_m], ignore_index=True)


def _make_parquet_df(
    rows: int = 50,
    adj_close_coverage: float = 1.0,
) -> pd.DataFrame:
    """Return a DataFrame shaped like a raw parquet file from yfinance.

    Args:
        rows: Number of rows.
        adj_close_coverage: Fraction of rows with non-NaN Adj Close.
    """
    idx = pd.date_range("2020-01-01", periods=rows, freq="B")
    rng = np.random.default_rng(0)
    close = 100 + rng.standard_normal(rows).cumsum()
    adj = close * 0.98  # slightly different from close

    # Zero out a fraction of adj_close to simulate low coverage
    nan_count = int(rows * (1 - adj_close_coverage))
    if nan_count > 0:
        adj[:nan_count] = float("nan")

    df = pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.98,
            "Close": close,
            "Adj Close": adj,
            "Volume": rng.integers(1_000_000, 5_000_000, rows),
        },
        index=idx,
    )
    df.index.name = "Date"
    return df


# ===================================================================
# StockRepository.update_ohlcv_adj_close
# ===================================================================


class TestUpdateOhlcvAdjClose:
    """Tests for :meth:`StockRepository.update_ohlcv_adj_close`."""

    def _make_repo(self, full_df: pd.DataFrame):
        """Build a StockRepository with mocked catalog.

        Returns ``(repo, mock_table)`` so tests can assert on
        ``mock_table.overwrite``.
        """
        from stocks.repository import StockRepository

        repo = StockRepository()
        mock_table = MagicMock()

        with patch.object(
            repo,
            "_load_table_and_scan",
            return_value=(mock_table, full_df.copy()),
        ):
            # Cache the mock for access
            repo._mock_table = mock_table
            repo._patched_load = patch.object(
                repo,
                "_load_table_and_scan",
                return_value=(mock_table, full_df.copy()),
            )
        return repo, mock_table

    def test_updates_matching_rows(self):
        """adj_close must be updated for dates in the map."""
        from stocks.repository import StockRepository

        full_df = _make_iceberg_ohlcv(10, "AAPL", adj_close_nan=True)
        dates = full_df["date"].tolist()
        adj_map = {dates[0]: 99.0, dates[5]: 88.0}

        repo = StockRepository()

        with (
            patch.object(
                repo,
                "_scan_ticker",
                return_value=full_df,
            ),
            patch.object(repo, "_delete_rows"),
            patch.object(repo, "_append_rows") as mock_ap,
        ):
            updated = repo.update_ohlcv_adj_close(
                "AAPL", adj_map
            )

        assert updated == 2
        mock_ap.assert_called_once()

    def test_empty_map_returns_zero(self):
        """An empty adj_close_map must return 0 without touching Iceberg."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        with patch.object(repo, "_load_table_and_scan") as mock_load:
            result = repo.update_ohlcv_adj_close("AAPL", {})

        assert result == 0
        mock_load.assert_not_called()

    def test_no_matching_dates_returns_zero(self):
        """When no dates in the map match OHLCV rows, return 0."""
        from stocks.repository import StockRepository

        full_df = _make_iceberg_ohlcv(10, "AAPL", adj_close_nan=True)
        adj_map = {date(1999, 1, 1): 50.0}

        repo = StockRepository()

        with (
            patch.object(
                repo,
                "_scan_ticker",
                return_value=full_df,
            ),
            patch.object(repo, "_delete_rows") as mock_del,
        ):
            result = repo.update_ohlcv_adj_close(
                "AAPL", adj_map
            )

        assert result == 0
        mock_del.assert_not_called()

    def test_only_updates_target_ticker(self):
        """Only AAPL rows are deleted+appended; MSFT untouched."""
        from stocks.repository import StockRepository

        full_df = _make_multi_ticker_ohlcv()
        aapl_df = full_df[full_df["ticker"] == "AAPL"].copy()
        aapl_dates = aapl_df["date"].tolist()
        adj_map = {aapl_dates[0]: 42.0}

        repo = StockRepository()

        with (
            patch.object(
                repo,
                "_scan_ticker",
                return_value=aapl_df,
            ),
            patch.object(repo, "_delete_rows") as mock_del,
            patch.object(repo, "_append_rows") as mock_ap,
        ):
            updated = repo.update_ohlcv_adj_close(
                "AAPL", adj_map
            )

        assert updated == 1
        mock_del.assert_called_once()
        mock_ap.assert_called_once()

        # Arrow table passed to _append_rows has AAPL only
        arrow_arg = mock_ap.call_args[0][1]
        written_df = arrow_arg.to_pandas()
        assert set(written_df["ticker"].unique()) == {"AAPL"}
        assert len(written_df) == len(aapl_df)

    def test_empty_table_returns_zero(self):
        """When no OHLCV rows exist for the ticker, return 0."""
        from stocks.repository import StockRepository

        repo = StockRepository()

        with (
            patch.object(
                repo,
                "_scan_ticker",
                return_value=pd.DataFrame(),
            ),
            patch.object(repo, "_delete_rows") as mock_del,
        ):
            adj_map = {date(2020, 1, 1): 100.0}
            result = repo.update_ohlcv_adj_close(
                "AAPL", adj_map
            )

        assert result == 0
        mock_del.assert_not_called()

    def test_nan_and_inf_values_skipped(self):
        """NaN/inf in adj_close_map are skipped via _safe_float."""
        from stocks.repository import StockRepository

        full_df = _make_iceberg_ohlcv(
            5, "AAPL", adj_close_nan=True
        )
        dates = full_df["date"].tolist()
        adj_map = {
            dates[0]: float("nan"),
            dates[1]: float("inf"),
            dates[2]: 99.0,
        }

        repo = StockRepository()

        with (
            patch.object(
                repo,
                "_scan_ticker",
                return_value=full_df,
            ),
            patch.object(repo, "_delete_rows"),
            patch.object(repo, "_append_rows"),
        ):
            updated = repo.update_ohlcv_adj_close(
                "AAPL", adj_map
            )

        # Only dates[2] should update (nan/inf rejected)
        assert updated == 1

    def test_case_insensitive_ticker(self):
        """Ticker should be uppercased internally."""
        from stocks.repository import StockRepository

        full_df = _make_iceberg_ohlcv(
            5, "AAPL", adj_close_nan=True
        )
        dates = full_df["date"].tolist()
        adj_map = {dates[0]: 42.0}

        repo = StockRepository()

        with (
            patch.object(
                repo,
                "_scan_ticker",
                return_value=full_df,
            ),
            patch.object(repo, "_delete_rows"),
            patch.object(repo, "_append_rows"),
        ):
            updated = repo.update_ohlcv_adj_close(
                "aapl", adj_map
            )

        assert updated == 1


# ===================================================================
# stocks/backfill_adj_close.py — _backfill_from_parquet
# ===================================================================


class TestBackfillFromParquet:
    """Tests for :func:`stocks.backfill_adj_close._backfill_from_parquet`."""

    def test_skips_ticker_without_parquet(self, tmp_path):
        """Tickers with no parquet file are skipped gracefully."""
        from stocks.backfill_adj_close import _backfill_from_parquet

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {"AAPL": {}}
        mock_repo.update_ohlcv_adj_close.return_value = 0

        with (
            patch(
                "stocks.repository.StockRepository",
                return_value=mock_repo,
            ),
            patch(
                "stocks.backfill_adj_close._DATA_RAW",
                tmp_path / "raw",
            ),
        ):
            result = _backfill_from_parquet()

        assert result == 0
        mock_repo.update_ohlcv_adj_close.assert_not_called()

    def test_skips_low_coverage_parquet(self, tmp_path):
        """Tickers with < 50% Adj Close coverage are skipped."""
        from stocks.backfill_adj_close import _backfill_from_parquet

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        # Write parquet with only 10% coverage
        pq_df = _make_parquet_df(100, adj_close_coverage=0.1)
        pq_df.to_parquet(raw_dir / "AAPL_raw.parquet")

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {"AAPL": {}}

        with (
            patch(
                "stocks.repository.StockRepository",
                return_value=mock_repo,
            ),
            patch("stocks.backfill_adj_close._DATA_RAW", raw_dir),
        ):
            result = _backfill_from_parquet()

        assert result == 0
        mock_repo.update_ohlcv_adj_close.assert_not_called()

    def test_merges_good_parquet(self, tmp_path):
        """Tickers with >= 50% coverage merge adj_close into Iceberg."""
        from stocks.backfill_adj_close import _backfill_from_parquet

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        pq_df = _make_parquet_df(50, adj_close_coverage=1.0)
        pq_df.to_parquet(raw_dir / "MSFT_raw.parquet")

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {"MSFT": {}}
        mock_repo.update_ohlcv_adj_close.return_value = 50

        with (
            patch(
                "stocks.repository.StockRepository",
                return_value=mock_repo,
            ),
            patch("stocks.backfill_adj_close._DATA_RAW", raw_dir),
        ):
            result = _backfill_from_parquet()

        assert result == 1
        mock_repo.update_ohlcv_adj_close.assert_called_once()
        call_args = mock_repo.update_ohlcv_adj_close.call_args
        assert call_args[0][0] == "MSFT"
        assert len(call_args[0][1]) == 50

    def test_empty_registry_returns_zero(self):
        """When registry is empty, nothing is processed."""
        from stocks.backfill_adj_close import _backfill_from_parquet

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {}

        with patch(
            "stocks.repository.StockRepository",
            return_value=mock_repo,
        ):
            result = _backfill_from_parquet()

        assert result == 0


# ===================================================================
# stocks/backfill_adj_close.py — _fill_remaining_nulls
# ===================================================================


class TestFillRemainingNulls:
    """Tests for :func:`stocks.backfill_adj_close._fill_remaining_nulls`."""

    def test_fills_nan_adj_close_with_close(self):
        """Rows with NaN adj_close get filled with close values."""
        from stocks.backfill_adj_close import _fill_remaining_nulls

        ice_df = _make_iceberg_ohlcv(20, "AAPL", adj_close_nan=True)

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {"AAPL": {}}
        mock_repo.get_ohlcv.return_value = ice_df
        mock_repo.update_ohlcv_adj_close.return_value = 20

        with patch(
            "stocks.repository.StockRepository",
            return_value=mock_repo,
        ):
            result = _fill_remaining_nulls()

        assert result == 1
        mock_repo.update_ohlcv_adj_close.assert_called_once()
        call_args = mock_repo.update_ohlcv_adj_close.call_args
        assert call_args[0][0] == "AAPL"
        adj_map = call_args[0][1]
        assert len(adj_map) == 20
        # Values should be close prices
        first_date = ice_df["date"].iloc[0]
        assert adj_map[first_date] == pytest.approx(ice_df["close"].iloc[0])

    def test_skips_ticker_with_no_nan(self):
        """Tickers with complete adj_close are skipped."""
        from stocks.backfill_adj_close import _fill_remaining_nulls

        ice_df = _make_iceberg_ohlcv(20, "MSFT", adj_close_nan=False)

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {"MSFT": {}}
        mock_repo.get_ohlcv.return_value = ice_df

        with patch(
            "stocks.repository.StockRepository",
            return_value=mock_repo,
        ):
            result = _fill_remaining_nulls()

        assert result == 0
        mock_repo.update_ohlcv_adj_close.assert_not_called()

    def test_handles_empty_ohlcv(self):
        """Tickers with no OHLCV data are skipped gracefully."""
        from stocks.backfill_adj_close import _fill_remaining_nulls

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {"AAPL": {}}
        mock_repo.get_ohlcv.return_value = pd.DataFrame()

        with patch(
            "stocks.repository.StockRepository",
            return_value=mock_repo,
        ):
            result = _fill_remaining_nulls()

        assert result == 0

    def test_processes_multiple_tickers(self):
        """Multiple tickers are processed independently."""
        from stocks.backfill_adj_close import _fill_remaining_nulls

        df_aapl = _make_iceberg_ohlcv(10, "AAPL", adj_close_nan=True)
        df_msft = _make_iceberg_ohlcv(10, "MSFT", adj_close_nan=False)

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {
            "AAPL": {},
            "MSFT": {},
        }
        mock_repo.get_ohlcv.side_effect = [df_aapl, df_msft]
        mock_repo.update_ohlcv_adj_close.return_value = 10

        with patch(
            "stocks.repository.StockRepository",
            return_value=mock_repo,
        ):
            result = _fill_remaining_nulls()

        # Only AAPL has NaN adj_close
        assert result == 1
        mock_repo.update_ohlcv_adj_close.assert_called_once()


# ===================================================================
# stocks/backfill_adj_close.py — main (end-to-end)
# ===================================================================


class TestBackfillMain:
    """End-to-end test for :func:`stocks.backfill_adj_close.main`."""

    def test_runs_both_phases_and_verifies(self, tmp_path):
        """main() runs parquet merge, null fill, and verification."""
        from stocks.backfill_adj_close import main

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        pq_df = _make_parquet_df(30, adj_close_coverage=1.0)
        pq_df.to_parquet(raw_dir / "AAPL_raw.parquet")

        # After backfill, verification reads complete data
        complete_df = _make_iceberg_ohlcv(30, "AAPL", adj_close_nan=False)

        mock_repo = MagicMock()
        mock_repo.get_all_registry.return_value = {"AAPL": {}}
        mock_repo.update_ohlcv_adj_close.return_value = 30
        # get_ohlcv called by _fill_remaining_nulls + verification
        mock_repo.get_ohlcv.return_value = complete_df

        with (
            patch(
                "stocks.repository.StockRepository",
                return_value=mock_repo,
            ),
            patch("stocks.backfill_adj_close._DATA_RAW", raw_dir),
        ):
            # Should not raise
            main()

        # Phase 1: parquet merge called
        assert mock_repo.update_ohlcv_adj_close.call_count >= 1
