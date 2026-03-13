"""Data retention policies and cleanup jobs for Iceberg tables.

.. note:: ``from __future__ import annotations`` enables PEP 604
   (``X | None``) on Python < 3.10 at parse time.

Implements configurable per-table retention windows with dry-run
support and audit logging.  The ``registry`` table is always
excluded from cleanup (it is the source of truth for ticker
metadata).

Usage::

    from stocks.retention import RetentionManager

    mgr = RetentionManager()
    report = mgr.run_cleanup(dry_run=True)
    # report: list[RetentionResult]

Configuration is via ``backend/config.py`` Settings:

- ``retention_enabled`` — master switch (default ``False``)
- ``retention_dry_run`` — log deletions but don't execute
- ``retention_llm_usage_days`` — days to keep LLM usage rows
- ``retention_analysis_summary_days`` — days to keep analysis
- ``retention_forecast_runs_days`` — days to keep forecast runs
- ``retention_company_info_days`` — days to keep company info
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

_logger = logging.getLogger(__name__)


@dataclass
class RetentionPolicy:
    """Retention policy for a single Iceberg table.

    Attributes:
        table_id: Fully-qualified table name.
        date_column: Column used for age calculation.
        days_to_keep: Rows older than this are candidates
            for deletion.  ``0`` means keep forever.
    """

    table_id: str
    date_column: str
    days_to_keep: int


@dataclass
class RetentionResult:
    """Result of a retention cleanup run for one table.

    Attributes:
        table_id: Table that was cleaned.
        cutoff_date: Date threshold used.
        rows_before: Total rows before cleanup.
        rows_deleted: Rows removed (0 in dry-run).
        dry_run: Whether this was a dry-run.
        error: Error message if cleanup failed.
    """

    table_id: str
    cutoff_date: date
    rows_before: int = 0
    rows_deleted: int = 0
    dry_run: bool = True
    error: str = ""


# Tables that must NEVER be cleaned up.
_PROTECTED_TABLES = frozenset({
    "stocks.registry",
})


class RetentionManager:
    """Manages data retention policies across Iceberg tables.

    Reads configuration from ``backend/config.py`` Settings
    on construction.  Call :meth:`run_cleanup` to execute
    (or dry-run) all configured policies.

    Args:
        policies: Optional override list of policies.
            If ``None``, policies are built from Settings.
    """

    def __init__(
        self,
        policies: list[RetentionPolicy] | None = None,
    ) -> None:
        if policies is not None:
            self._policies = policies
        else:
            self._policies = self._policies_from_config()

    @staticmethod
    def _policies_from_config() -> list[RetentionPolicy]:
        """Build retention policies from app Settings.

        Returns:
            List of :class:`RetentionPolicy` instances.
        """
        from config import get_settings

        s = get_settings()
        policies: list[RetentionPolicy] = []

        if s.retention_llm_usage_days > 0:
            policies.append(
                RetentionPolicy(
                    table_id="stocks.llm_usage",
                    date_column="request_date",
                    days_to_keep=s.retention_llm_usage_days,
                )
            )

        if s.retention_analysis_summary_days > 0:
            policies.append(
                RetentionPolicy(
                    table_id="stocks.analysis_summary",
                    date_column="analysis_date",
                    days_to_keep=(
                        s.retention_analysis_summary_days
                    ),
                )
            )

        if s.retention_forecast_runs_days > 0:
            policies.append(
                RetentionPolicy(
                    table_id="stocks.forecast_runs",
                    date_column="run_date",
                    days_to_keep=(
                        s.retention_forecast_runs_days
                    ),
                )
            )

        if s.retention_company_info_days > 0:
            policies.append(
                RetentionPolicy(
                    table_id="stocks.company_info",
                    date_column="fetched_at",
                    days_to_keep=(
                        s.retention_company_info_days
                    ),
                )
            )

        return policies

    def run_cleanup(
        self,
        dry_run: bool | None = None,
    ) -> list[RetentionResult]:
        """Execute all retention policies.

        Args:
            dry_run: Override the Settings ``retention_dry_run``
                flag.  ``None`` uses the Settings value.

        Returns:
            List of :class:`RetentionResult` for each policy.
        """
        from stocks.repository import StockRepository

        if dry_run is None:
            from config import get_settings
            dry_run = get_settings().retention_dry_run

        repo = StockRepository()
        results: list[RetentionResult] = []

        for policy in self._policies:
            result = self._apply_policy(
                repo, policy, dry_run,
            )
            results.append(result)

        return results

    def _apply_policy(
        self,
        repo,
        policy: RetentionPolicy,
        dry_run: bool,
    ) -> RetentionResult:
        """Apply a single retention policy.

        Args:
            repo: StockRepository instance.
            policy: The policy to apply.
            dry_run: If True, only count — don't delete.

        Returns:
            :class:`RetentionResult` with counts.
        """
        if policy.table_id in _PROTECTED_TABLES:
            _logger.warning(
                "Skipping protected table %s",
                policy.table_id,
            )
            return RetentionResult(
                table_id=policy.table_id,
                cutoff_date=date.today(),
                error="Protected table — skipped",
            )

        cutoff = date.today() - timedelta(
            days=policy.days_to_keep,
        )

        try:
            return self._delete_before_cutoff(
                repo,
                policy.table_id,
                policy.date_column,
                cutoff,
                dry_run,
            )
        except Exception as exc:
            _logger.error(
                "Retention cleanup failed for %s: %s",
                policy.table_id,
                exc,
            )
            return RetentionResult(
                table_id=policy.table_id,
                cutoff_date=cutoff,
                error=str(exc),
                dry_run=dry_run,
            )

    @staticmethod
    def _delete_before_cutoff(
        repo,
        table_id: str,
        date_column: str,
        cutoff: date,
        dry_run: bool,
    ) -> RetentionResult:
        """Delete rows where date_column < cutoff.

        For ``company_info`` the date column is a timestamp,
        so we compare the date portion.

        Args:
            repo: StockRepository instance.
            table_id: Fully-qualified table name.
            date_column: Column to compare against cutoff.
            cutoff: Delete rows with date < this value.
            dry_run: If True, count only.

        Returns:
            :class:`RetentionResult` with counts.
        """
        import pandas as pd
        from pyiceberg.expressions import LessThan

        tbl = repo._load_table(table_id)
        df = tbl.scan().to_pandas()
        total_rows = len(df)

        if df.empty:
            _logger.info(
                "Retention: %s is empty — nothing to do.",
                table_id,
            )
            return RetentionResult(
                table_id=table_id,
                cutoff_date=cutoff,
                rows_before=0,
                rows_deleted=0,
                dry_run=dry_run,
            )

        # Count rows that would be deleted.
        if date_column == "fetched_at":
            # Timestamp column — compare date portion.
            col_dates = pd.to_datetime(
                df[date_column],
            ).dt.date
        else:
            col_dates = pd.to_datetime(
                df[date_column],
            ).dt.date

        old_mask = col_dates < cutoff
        rows_to_delete = int(old_mask.sum())

        if rows_to_delete == 0:
            _logger.info(
                "Retention: %s has no rows older than %s.",
                table_id,
                cutoff,
            )
            return RetentionResult(
                table_id=table_id,
                cutoff_date=cutoff,
                rows_before=total_rows,
                rows_deleted=0,
                dry_run=dry_run,
            )

        if dry_run:
            _logger.info(
                "Retention DRY-RUN: %s — would delete "
                "%d/%d rows older than %s.",
                table_id,
                rows_to_delete,
                total_rows,
                cutoff,
            )
            return RetentionResult(
                table_id=table_id,
                cutoff_date=cutoff,
                rows_before=total_rows,
                rows_deleted=rows_to_delete,
                dry_run=True,
            )

        # Actual delete via Iceberg row-level delete.
        repo._delete_rows(
            table_id,
            LessThan(date_column, cutoff),
        )
        _logger.info(
            "Retention: %s — deleted %d/%d rows "
            "older than %s.",
            table_id,
            rows_to_delete,
            total_rows,
            cutoff,
        )

        return RetentionResult(
            table_id=table_id,
            cutoff_date=cutoff,
            rows_before=total_rows,
            rows_deleted=rows_to_delete,
            dry_run=False,
        )
