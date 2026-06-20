"""Guard: compact_table must skip byte-heavy tables before the
full-table ``scan().to_arrow()`` that OOM-killed the backend when the
intraday rebuild dropped stocks.intraday_features below the 40k file
ceiling (1.2 GB / 70M rows, avg ~1.6 files/partition → passed every
file-count guard → loaded whole table into Arrow → OOM)."""
from unittest.mock import patch

import backend.maintenance.iceberg_maintenance as im


def test_large_table_skips_compaction_by_bytes(monkeypatch, tmp_path):
    # 1.2 GB, low avg files/partition (would have OOM'd pre-fix).
    monkeypatch.setattr(im, "WAREHOUSE_DIR", tmp_path)
    monkeypatch.setattr(im, "_count_parquet_files", lambda d: 2574)
    monkeypatch.setattr(
        im, "is_compaction_already_optimal", lambda d: False
    )
    monkeypatch.setattr(
        im, "_avg_files_per_partition", lambda d: (2574, 1632, 1.58)
    )
    monkeypatch.setattr(
        im, "_table_data_bytes", lambda d: 1288490188
    )
    # If it reached the read it would call _require_repo — fail hard
    # to prove the byte ceiling returned BEFORE any scan.
    with patch(
        "tools._stock_shared._require_repo",
        side_effect=AssertionError("must not scan a too-large table"),
    ):
        res = im.compact_table("stocks.intraday_features")
    assert res.get("skipped_too_large_bytes") is True
    assert "error" not in res


def test_under_byte_ceiling_still_compacts(monkeypatch, tmp_path):
    # 200 MB — under the 1 GB ceiling; must proceed to the read path.
    monkeypatch.setattr(im, "WAREHOUSE_DIR", tmp_path)
    monkeypatch.setattr(im, "_count_parquet_files", lambda d: 100)
    monkeypatch.setattr(
        im, "is_compaction_already_optimal", lambda d: False
    )
    monkeypatch.setattr(
        im, "_avg_files_per_partition", lambda d: (100, 50, 2.0)
    )
    monkeypatch.setattr(
        im, "_table_data_bytes", lambda d: 200 * 1024 * 1024
    )
    with patch(
        "tools._stock_shared._require_repo",
        side_effect=RuntimeError("past-ceiling"),
    ):
        res = im.compact_table("stocks.small")
    assert "skipped_too_large_bytes" not in res
    assert res.get("error") == "read failed"
