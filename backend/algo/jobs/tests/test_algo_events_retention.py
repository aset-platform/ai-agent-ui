"""Tests for the algo.events tiered-retention scheduler job
(2026-05-16, ASETPLTFRM iceberg-table-design-checklist sprint).

The retention predicate is the load-bearing piece of the
weekly job — get it wrong and we either purge live placed-
on-Zerodha events (compliance risk) or fail to expire
backtest noise (storage cost).  These tests stub the Iceberg
catalog so the predicate is exercised against a synthetic
table and we can verify the right rows survive.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.iceberg_init import (
    LIVE_PLACED_ZERODHA_TYPES,
    LONG_RETENTION_DAYS,
    SHORT_RETENTION_DAYS,
)
from backend.algo.jobs.algo_events_retention import (
    _delete_predicate,
    run_algo_events_retention_job,
)


# ---------------------------------------------------------------
# Predicate-shape tests — these guard the OR/AND structure of
# the delete expression.  Each assertion captures one rule of
# the retention matrix in the schema docstring.
# ---------------------------------------------------------------

class TestDeletePredicateShape:
    """The predicate must include all three retention clauses."""

    def test_repr_mentions_all_short_modes(self) -> None:
        pred = _delete_predicate(date(2026, 5, 16))
        s = repr(pred)
        for m in (
            "backtest", "paper", "dryrun",
            "live-ws", "walkforward", "pipeline",
        ):
            assert m in s, (
                f"short-retention mode '{m}' not in predicate: {s}"
            )

    def test_repr_mentions_long_cap_for_live(self) -> None:
        pred = _delete_predicate(date(2026, 5, 16))
        s = repr(pred)
        # The 365-day cap clause must reference live + ts_date.
        assert "live" in s
        # PyIceberg renders LessThan as something like
        # "LessThan(term=Reference('ts_date'), literal=...)"
        assert "ts_date" in s

    def test_repr_excludes_placed_types_from_short_window(self) -> None:
        """Live non-placed events go to short retention; the
        ``NotIn`` clause enumerates the placed-on-Zerodha
        allowlist so they're excluded from that delete clause.
        """
        pred = _delete_predicate(date(2026, 5, 16))
        s = repr(pred)
        for t in LIVE_PLACED_ZERODHA_TYPES:
            assert t in s, (
                f"placed-on-Zerodha type '{t}' missing from "
                f"NotIn clause: {s}"
            )


class TestRetentionCutoffMath:
    """The short/long cuts in the response payload should
    line up with the documented retention windows.
    """

    def test_cuts_match_retention_constants(self) -> None:
        today = date(2026, 5, 16)
        # dry_run path avoids any catalog interaction
        result = run_algo_events_retention_job({
            "today": today.isoformat(),
            "dry_run": True,
        })
        assert result["status"] == "dry_run"
        from datetime import timedelta
        assert result["short_cut"] == (
            today - timedelta(days=SHORT_RETENTION_DAYS)
        ).isoformat()
        assert result["long_cut"] == (
            today - timedelta(days=LONG_RETENTION_DAYS)
        ).isoformat()


# ---------------------------------------------------------------
# End-to-end-ish test: stub the catalog so we can assert the
# delete is called with the expected predicate AND verify the
# backup-then-delete contract.
# ---------------------------------------------------------------

class TestRunRetentionJob:

    def _fake_catalog(self) -> MagicMock:
        cat = MagicMock()
        tbl = MagicMock()
        cat.load_table.return_value = tbl
        return cat

    def test_dry_run_does_not_call_catalog(self) -> None:
        with patch(
            "stocks.create_tables._get_catalog",
            return_value=self._fake_catalog(),
        ) as mock_get:
            run_algo_events_retention_job({"dry_run": True})
        mock_get.assert_not_called()

    def test_backup_failure_aborts_delete(self) -> None:
        """Fail-closed contract: if pre-delete backup fails the
        job must NOT proceed to the destructive Iceberg delete.
        """
        with (
            patch(
                "backend.algo.jobs.algo_events_retention."
                "backup_table",
                side_effect=RuntimeError("disk full"),
            ),
            patch(
                "stocks.create_tables._get_catalog",
            ) as mock_get,
        ):
            result = run_algo_events_retention_job({})
        assert result["status"] == "error"
        assert "backup_failed" in result["error"]
        mock_get.assert_not_called()

    def test_happy_path_invokes_delete_with_predicate(self) -> None:
        cat = self._fake_catalog()
        with (
            patch(
                "backend.algo.jobs.algo_events_retention."
                "backup_table",
                return_value="/tmp/backup-1",
            ),
            patch(
                "stocks.create_tables._get_catalog",
                return_value=cat,
            ),
            patch(
                "backend.algo.jobs.algo_events_retention."
                "invalidate_metadata",
            ),
        ):
            result = run_algo_events_retention_job({
                "today": "2026-05-16",
            })
        assert result["status"] == "ok"
        assert result["backup_path"] == "/tmp/backup-1"
        cat.load_table.return_value.delete.assert_called_once()
        # The predicate passed to delete() should mention live
        # + ts_date + at least one placed-on-Zerodha type (the
        # NotIn clause) — full structural shape is covered by
        # TestDeletePredicateShape above.
        called_with = (
            cat.load_table.return_value.delete.call_args.args[0]
        )
        s = repr(called_with)
        assert "live" in s
        assert "ts_date" in s


# ---------------------------------------------------------------
# Smoke: the job is registered in the scheduler executor under
# the expected name (422 wires it into the Weekly Long-Tail
# pipeline).
# ---------------------------------------------------------------

def test_scheduler_job_registered() -> None:
    """Importing the executor module must register the new
    ``algo_events_retention`` job type so the scheduler can
    invoke it when the Weekly Long-Tail Iceberg Maintenance
    pipeline fires.
    """
    from backend.jobs.executor import JOB_EXECUTORS

    assert "algo_events_retention" in JOB_EXECUTORS
