"""Tests for ASETPLTFRM-418 scoped Iceberg maintenance.

The ``execute_iceberg_maintenance`` wrapper learnt a
``payload`` argument: when ``payload["tables"]`` is a
non-empty list of known tables, the run is scoped to that
subset only — per-table ``backup_table()`` replaces the
full-warehouse rsync and the compact + sweep loop walks
the scoped list instead of ``_HOT_ICEBERG_TABLES``.

These tests stub out the I/O-heavy primitives
(``run_backup``, ``backup_table``, ``compact_table``,
``cleanup_orphans_v2``) so each test runs in milliseconds
and asserts on call sets rather than disk state.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.jobs.executor import (
    _HOT_ICEBERG_TABLES,
    execute_iceberg_maintenance,
)

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def _ok_compact(_t: str) -> dict:
    return {"before": 5, "after": 1, "rows": 10, "elapsed_s": 0.1}


def _ok_sweep(*_a, **_k) -> dict:
    return {
        "verified": True,
        "deleted_files": 0,
        "deleted_bytes": 0,
        "expired_snapshots": 0,
    }


def _patch_targets():
    """Common patch context — returns a dict of mocks.

    Patches at the source modules per CLAUDE.md hard rule
    #16 — ``execute_iceberg_maintenance`` does a function-
    local import of these names, so patching at the
    importer would be a no-op.
    """
    return {
        "run_backup": patch(
            "backend.maintenance.backup.run_backup",
            return_value="/tmp/backup-2026-05-15",
        ),
        "backup_table": patch(
            "backend.maintenance.backup.backup_table",
            side_effect=lambda t: f"/tmp/backup-{t}",
        ),
        "compact_table": patch(
            "backend.maintenance.iceberg_maintenance.compact_table",
            side_effect=_ok_compact,
        ),
        "cleanup_orphans_v2": patch(
            "backend.maintenance.iceberg_maintenance.cleanup_orphans_v2",
            side_effect=_ok_sweep,
        ),
    }


def _start(payload=None):
    repo = MagicMock()
    repo.update_scheduler_run = MagicMock()
    repo.append_scheduler_run = MagicMock()
    with (
        _patch_targets()["run_backup"] as rb,
        _patch_targets()["backup_table"] as bt,
        _patch_targets()["compact_table"] as ct,
        _patch_targets()["cleanup_orphans_v2"] as co,
    ):
        execute_iceberg_maintenance(
            scope="india",
            run_id="test-run",
            repo=repo,
            payload=payload,
        )
        return {
            "run_backup": rb,
            "backup_table": bt,
            "compact_table": ct,
            "cleanup_orphans_v2": co,
            "repo": repo,
        }


# ---------------------------------------------------------
# Scoping behaviour
# ---------------------------------------------------------


def test_iceberg_maintenance_unscoped_falls_back_to_all_tables() -> None:
    """payload=None → behaviour identical to pre-418."""
    calls = _start(payload=None)
    compact_args = {c.args[0] for c in calls["compact_table"].call_args_list}
    assert compact_args == set(_HOT_ICEBERG_TABLES)


def test_iceberg_maintenance_scoped_uses_payload_tables_only() -> None:
    """Only the listed tables get compacted + swept."""
    calls = _start(
        payload={"tables": ["stocks.ohlcv"]},
    )
    compact_args = [c.args[0] for c in calls["compact_table"].call_args_list]
    sweep_args = [
        c.args[0] for c in calls["cleanup_orphans_v2"].call_args_list
    ]
    assert compact_args == ["stocks.ohlcv"]
    assert sweep_args == ["stocks.ohlcv"]


def test_iceberg_maintenance_scoped_skips_full_warehouse_backup() -> None:
    """Scoped run uses backup_table per-table, not run_backup."""
    calls = _start(
        payload={
            "tables": ["stocks.ohlcv", "stocks.sentiment_scores"],
        },
    )
    assert calls["run_backup"].call_count == 0
    assert calls["backup_table"].call_count == 2
    bt_args = [c.args[0] for c in calls["backup_table"].call_args_list]
    assert bt_args == ["stocks.ohlcv", "stocks.sentiment_scores"]


def test_iceberg_maintenance_unscoped_calls_run_backup() -> None:
    """Legacy path preserves the full warehouse rsync."""
    calls = _start(payload=None)
    assert calls["run_backup"].call_count == 1
    assert calls["backup_table"].call_count == 0


def test_unknown_table_in_payload_logged_and_skipped() -> None:
    """Typo'd table name doesn't crash and doesn't get compacted."""
    calls = _start(
        payload={
            "tables": [
                "stocks.ohlcv",
                "stocks.does_not_exist",
            ],
        },
    )
    compact_args = [c.args[0] for c in calls["compact_table"].call_args_list]
    assert compact_args == ["stocks.ohlcv"]
    # And no per-table backup for the bogus name.
    bt_args = [c.args[0] for c in calls["backup_table"].call_args_list]
    assert bt_args == ["stocks.ohlcv"]


def test_empty_tables_list_treated_as_unscoped() -> None:
    """payload={"tables": []} → fallback to full set."""
    calls = _start(payload={"tables": []})
    compact_args = {c.args[0] for c in calls["compact_table"].call_args_list}
    assert compact_args == set(_HOT_ICEBERG_TABLES)
    # Falls back to full-warehouse rsync, NOT per-table.
    assert calls["run_backup"].call_count == 1
    assert calls["backup_table"].call_count == 0


def test_all_unknown_tables_falls_back_to_unscoped() -> None:
    """Defensive: all typos → log + run the full sweep."""
    calls = _start(
        payload={"tables": ["bogus.one", "bogus.two"]},
    )
    compact_args = {c.args[0] for c in calls["compact_table"].call_args_list}
    assert compact_args == set(_HOT_ICEBERG_TABLES)
    assert calls["run_backup"].call_count == 1


def test_missing_table_data_dir_does_not_abort_scoped_run() -> None:
    """``backup_table`` raising FileNotFoundError on one table
    must not stop the loop — other scoped tables continue."""
    repo = MagicMock()
    with (
        patch(
            "backend.maintenance.backup.run_backup",
            return_value="/tmp/x",
        ) as rb,
        patch(
            "backend.maintenance.backup.backup_table",
        ) as bt,
        patch(
            "backend.maintenance.iceberg_maintenance.compact_table",
            side_effect=_ok_compact,
        ) as ct,
        patch(
            "backend.maintenance.iceberg_maintenance.cleanup_orphans_v2",
            side_effect=_ok_sweep,
        ),
    ):
        bt.side_effect = [
            FileNotFoundError("stocks.intraday_features"),
            "/tmp/backup-stocks.ohlcv",
        ]
        execute_iceberg_maintenance(
            scope="india",
            run_id="r",
            repo=repo,
            payload={
                "tables": [
                    "stocks.intraday_features",
                    "stocks.ohlcv",
                ],
            },
        )
        assert rb.call_count == 0
        compact_args = [c.args[0] for c in ct.call_args_list]
        # Both still compact — backup gap is non-fatal.
        assert compact_args == [
            "stocks.intraday_features",
            "stocks.ohlcv",
        ]


# ---------------------------------------------------------
# Composition with the smart-skip gate (yesterday's hotfix)
# ---------------------------------------------------------


def test_scoped_run_composes_with_smart_skip_on_optimal_table() -> None:
    """When the scoped table is already optimal, ``compact_table``
    returns ``skipped_optimal=True`` and the loop moves on — sweep
    still runs (it's idempotent + reclaims orphans regardless)."""
    repo = MagicMock()
    skipped_result = {
        "skipped_optimal": True,
        "before": 1,
        "after": 1,
        "rows": 0,
        "elapsed_s": 0.0,
    }
    with (
        patch(
            "backend.maintenance.backup.run_backup",
            return_value="/tmp/x",
        ),
        patch(
            "backend.maintenance.backup.backup_table",
            return_value="/tmp/per-table",
        ),
        patch(
            "backend.maintenance.iceberg_maintenance.compact_table",
            return_value=skipped_result,
        ) as ct,
        patch(
            "backend.maintenance.iceberg_maintenance.cleanup_orphans_v2",
            side_effect=_ok_sweep,
        ) as co,
    ):
        execute_iceberg_maintenance(
            scope="india",
            run_id="r",
            repo=repo,
            payload={"tables": ["stocks.intraday_bars"]},
        )
        # compact_table was called exactly once on the scoped
        # table — its smart-skip return value is honoured.
        assert ct.call_args_list[0].args[0] == "stocks.intraday_bars"
        # Other hot tables (e.g. stocks.ohlcv) were not touched.
        compact_tables = [c.args[0] for c in ct.call_args_list]
        assert compact_tables == ["stocks.intraday_bars"]
        # Sweep still runs even when compact short-circuited.
        sweep_tables = [c.args[0] for c in co.call_args_list]
        assert sweep_tables == ["stocks.intraday_bars"]


# ---------------------------------------------------------
# Wrapper signature (back-compat)
# ---------------------------------------------------------


def test_wrapper_payload_kwarg_defaults_to_none() -> None:
    """Calling without ``payload=`` works (legacy callers)."""
    repo = MagicMock()
    with (
        patch(
            "backend.maintenance.backup.run_backup",
            return_value="/tmp/x",
        ),
        patch(
            "backend.maintenance.iceberg_maintenance.compact_table",
            side_effect=_ok_compact,
        ),
        patch(
            "backend.maintenance.iceberg_maintenance.cleanup_orphans_v2",
            side_effect=_ok_sweep,
        ),
    ):
        # No payload kwarg — must not raise TypeError.
        execute_iceberg_maintenance(
            scope="india",
            run_id="r",
            repo=repo,
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
