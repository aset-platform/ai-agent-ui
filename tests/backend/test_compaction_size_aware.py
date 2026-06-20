from unittest.mock import patch

import backend.maintenance.iceberg_maintenance as im


def test_small_table_compacts_despite_high_avg(monkeypatch, tmp_path):
    # Redirect WAREHOUSE_DIR so compact_table never touches the real
    # warehouse path regardless of which filesystem helpers are patched.
    monkeypatch.setattr(im, "WAREHOUSE_DIR", tmp_path)
    # avg files/partition = 100 (> 50 guard) but only 10 MB total
    monkeypatch.setattr(im, "_count_parquet_files", lambda d: 200)
    monkeypatch.setattr(
        im, "is_compaction_already_optimal", lambda d: False
    )
    monkeypatch.setattr(
        im, "_avg_files_per_partition", lambda d: (200, 2, 100.0)
    )
    monkeypatch.setattr(
        im, "_table_data_bytes", lambda d: 10 * 1024 * 1024
    )
    # Fail the read path AFTER the guard so we only assert the guard
    # let us through (not a full compaction).  Reaching
    # error == "read failed" proves the size guard was bypassed —
    # if the guard had triggered, compact_table would have returned
    # early with skipped_deep_manifest=True instead.
    with patch(
        "tools._stock_shared._require_repo",
        side_effect=RuntimeError("past-guard"),
    ):
        res = im.compact_table("algo.events")
    assert "skipped_deep_manifest" not in res
    assert res.get("error") == "read failed"


def test_large_table_still_skips_deep_manifest(monkeypatch, tmp_path):
    # Redirect WAREHOUSE_DIR so compact_table never touches the real
    # warehouse path regardless of which filesystem helpers are patched.
    monkeypatch.setattr(im, "WAREHOUSE_DIR", tmp_path)
    monkeypatch.setattr(im, "_count_parquet_files", lambda d: 5000)
    monkeypatch.setattr(
        im, "is_compaction_already_optimal", lambda d: False
    )
    monkeypatch.setattr(
        im, "_avg_files_per_partition", lambda d: (5000, 50, 100.0)
    )
    monkeypatch.setattr(
        im, "_table_data_bytes", lambda d: 2 * 1024 * 1024 * 1024
    )
    res = im.compact_table("stocks.huge")
    assert res.get("skipped_deep_manifest") is True
