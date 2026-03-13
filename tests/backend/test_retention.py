"""Tests for data retention policies (ASETPLTFRM-15).

Validates that:
- Retention policies are correctly parsed from config.
- Dry-run mode logs but does not delete rows.
- Live cleanup deletes old rows and preserves recent ones.
- Registry table is always skipped (protected).
- Audit results contain correct row counts.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stocks.retention import (
    RetentionManager,
    RetentionPolicy,
    RetentionResult,
    _PROTECTED_TABLES,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _mock_repo_with_data(
    table_id: str,
    date_col: str,
    dates: list[date],
):
    """Create a mock repo that returns rows with given dates.

    Args:
        table_id: Table identifier.
        date_col: Date column name.
        dates: List of dates for the rows.

    Returns:
        A MagicMock repo.
    """
    df = pd.DataFrame(
        {
            date_col: dates,
            "ticker": ["AAPL"] * len(dates),
        }
    )

    mock_tbl = MagicMock()
    mock_scan = MagicMock()
    mock_scan.to_pandas.return_value = df
    mock_tbl.scan.return_value = mock_scan

    repo = MagicMock()
    repo._load_table.return_value = mock_tbl
    repo._delete_rows = MagicMock()

    return repo


# ------------------------------------------------------------------
# Tests: RetentionPolicy parsing
# ------------------------------------------------------------------


class TestRetentionPolicyParsing:
    """Tests for policy construction from config."""

    @patch("config.get_settings")
    def test_policies_from_config(self, mock_settings):
        """Should create policies from Settings values."""
        settings = MagicMock()
        settings.retention_llm_usage_days = 90
        settings.retention_analysis_summary_days = 365
        settings.retention_forecast_runs_days = 180
        settings.retention_company_info_days = 365
        mock_settings.return_value = settings

        policies = RetentionManager._policies_from_config()

        assert len(policies) == 4
        table_ids = [p.table_id for p in policies]
        assert "stocks.llm_usage" in table_ids
        assert "stocks.analysis_summary" in table_ids
        assert "stocks.forecast_runs" in table_ids
        assert "stocks.company_info" in table_ids

    @patch("config.get_settings")
    def test_zero_days_skips_policy(self, mock_settings):
        """days_to_keep=0 means keep forever (no policy)."""
        settings = MagicMock()
        settings.retention_llm_usage_days = 0
        settings.retention_analysis_summary_days = 0
        settings.retention_forecast_runs_days = 0
        settings.retention_company_info_days = 0
        mock_settings.return_value = settings

        policies = RetentionManager._policies_from_config()
        assert len(policies) == 0


# ------------------------------------------------------------------
# Tests: Dry-run mode
# ------------------------------------------------------------------


class TestDryRun:
    """Tests for dry-run (report-only) mode."""

    def test_dry_run_does_not_delete(self):
        """Dry-run should count rows but not call
        _delete_rows."""
        today = date.today()
        old_dates = [
            today - timedelta(days=100),
            today - timedelta(days=200),
            today - timedelta(days=5),
        ]

        repo = _mock_repo_with_data(
            "stocks.llm_usage",
            "request_date",
            old_dates,
        )

        policy = RetentionPolicy(
            table_id="stocks.llm_usage",
            date_column="request_date",
            days_to_keep=90,
        )

        mgr = RetentionManager(policies=[policy])
        result = mgr._apply_policy(repo, policy, dry_run=True)

        assert result.dry_run is True
        assert result.rows_before == 3
        assert result.rows_deleted == 2  # 100d + 200d old
        repo._delete_rows.assert_not_called()

    def test_dry_run_audit_result(self):
        """Dry-run result should have correct audit fields."""
        today = date.today()

        repo = _mock_repo_with_data(
            "stocks.analysis_summary",
            "analysis_date",
            [today - timedelta(days=400)],
        )

        policy = RetentionPolicy(
            table_id="stocks.analysis_summary",
            date_column="analysis_date",
            days_to_keep=365,
        )

        mgr = RetentionManager(policies=[policy])
        result = mgr._apply_policy(repo, policy, dry_run=True)

        assert result.table_id == "stocks.analysis_summary"
        assert result.rows_deleted == 1
        assert result.error == ""


# ------------------------------------------------------------------
# Tests: Live cleanup
# ------------------------------------------------------------------


class TestLiveCleanup:
    """Tests for actual deletion mode."""

    def test_cleanup_deletes_old_rows(self):
        """Live cleanup should call _delete_rows for old
        data."""
        today = date.today()
        dates = [
            today - timedelta(days=100),
            today - timedelta(days=50),
            today - timedelta(days=10),
        ]

        repo = _mock_repo_with_data(
            "stocks.llm_usage",
            "request_date",
            dates,
        )

        policy = RetentionPolicy(
            table_id="stocks.llm_usage",
            date_column="request_date",
            days_to_keep=90,
        )

        mgr = RetentionManager(policies=[policy])
        result = mgr._apply_policy(repo, policy, dry_run=False)

        assert result.dry_run is False
        assert result.rows_deleted == 1  # Only 100d old row
        repo._delete_rows.assert_called_once()

    def test_no_old_rows_skips_delete(self):
        """When no rows are old enough, should not delete."""
        today = date.today()
        dates = [
            today - timedelta(days=5),
            today - timedelta(days=10),
        ]

        repo = _mock_repo_with_data(
            "stocks.llm_usage",
            "request_date",
            dates,
        )

        policy = RetentionPolicy(
            table_id="stocks.llm_usage",
            date_column="request_date",
            days_to_keep=90,
        )

        mgr = RetentionManager(policies=[policy])
        result = mgr._apply_policy(repo, policy, dry_run=False)

        assert result.rows_deleted == 0
        repo._delete_rows.assert_not_called()


# ------------------------------------------------------------------
# Tests: Protected tables
# ------------------------------------------------------------------


class TestProtectedTables:
    """Tests that protected tables are never cleaned."""

    def test_registry_is_protected(self):
        """stocks.registry must be in protected set."""
        assert "stocks.registry" in _PROTECTED_TABLES

    def test_protected_table_skipped(self):
        """Applying a policy to a protected table should
        skip it."""
        repo = MagicMock()

        policy = RetentionPolicy(
            table_id="stocks.registry",
            date_column="updated_at",
            days_to_keep=30,
        )

        mgr = RetentionManager(policies=[policy])
        result = mgr._apply_policy(repo, policy, dry_run=False)

        assert "Protected" in result.error
        repo._load_table.assert_not_called()
        repo._delete_rows.assert_not_called()


# ------------------------------------------------------------------
# Tests: Empty table handling
# ------------------------------------------------------------------


class TestEmptyTable:
    """Tests for cleanup on empty tables."""

    def test_empty_table_returns_zero(self):
        """Empty table should return rows_deleted=0."""
        df = pd.DataFrame(columns=["request_date", "ticker"])

        mock_tbl = MagicMock()
        mock_scan = MagicMock()
        mock_scan.to_pandas.return_value = df
        mock_tbl.scan.return_value = mock_scan

        repo = MagicMock()
        repo._load_table.return_value = mock_tbl

        policy = RetentionPolicy(
            table_id="stocks.llm_usage",
            date_column="request_date",
            days_to_keep=90,
        )

        mgr = RetentionManager(policies=[policy])
        result = mgr._apply_policy(repo, policy, dry_run=False)

        assert result.rows_before == 0
        assert result.rows_deleted == 0
        repo._delete_rows.assert_not_called()


# ------------------------------------------------------------------
# Tests: Error handling
# ------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling in cleanup."""

    def test_error_captured_in_result(self):
        """Errors during cleanup should be captured, not
        raised."""
        repo = MagicMock()
        repo._load_table.side_effect = Exception(
            "catalog down"
        )

        policy = RetentionPolicy(
            table_id="stocks.llm_usage",
            date_column="request_date",
            days_to_keep=90,
        )

        mgr = RetentionManager(policies=[policy])
        result = mgr._apply_policy(repo, policy, dry_run=False)

        assert "catalog down" in result.error
        assert result.rows_deleted == 0
