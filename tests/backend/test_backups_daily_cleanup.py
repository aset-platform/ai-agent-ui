from unittest.mock import patch


def test_backups_daily_calls_cleanup_after_manifest():
    from backend.jobs import executor
    with patch(
        "backend.maintenance.backup.run_backup", return_value="/snap"
    ), patch(
        "backend.maintenance.backup_manifest.build_manifest",
        return_value={"tables": [], "warehouse_size_mb": 1.0},
    ), patch(
        "backend.maintenance.backup_manifest.write_manifest"
    ), patch(
        "scripts.cleanup_per_table_backups.main"
    ) as cleanup:
        executor.execute_backups_daily(run_id="r", repo=None)
    cleanup.assert_called_once()
