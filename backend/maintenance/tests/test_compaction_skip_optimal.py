"""Tests for the ``is_compaction_already_optimal`` smart-skip
gate added in response to the 2026-05-14
stocks.intraday_bars overwrite-failed incident.

Background: ``compact_table`` would re-read + atomic-overwrite
EVERY parquet in a table even when the layout was already
one-file-per-partition (the canonical "optimal" shape). On a
22k-parquet table the 6-hour rewrite hit PyIceberg's branch-ref
conflict and failed. The smart-skip gate detects the optimal
case via ``files / partitions`` ratio and short-circuits the
read+overwrite, returning ``skipped_optimal=True`` in the
stats dict. The orphan-sweep step still runs (it's a separate
caller path in the executor).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.maintenance.iceberg_maintenance import (
    _OPTIMAL_FILES_PER_PARTITION,
    _avg_files_per_partition,
    _count_parquet_files,
    compact_table,
    is_compaction_already_optimal,
)


def _seed_table(
    root: Path,
    name: str,
    layout: dict[tuple[str, ...], int],
) -> Path:
    """Build a fake table directory.

    layout: ``{(partition_path_parts): num_parquets}``.
    Empty tuple = un-partitioned (files in the data root).
    """
    table_dir = root / name
    data_dir = table_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for parts, n in layout.items():
        partition = data_dir.joinpath(*parts) if parts else data_dir
        partition.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (partition / f"{i:05d}.parquet").write_bytes(b"x")
    return table_dir


# ────────────────────────────────────────────────────────────────
# _avg_files_per_partition


def test_avg_one_file_per_partition(tmp_path: Path) -> None:
    """Canonical optimal shape: 5 partitions × 1 parquet."""
    table_dir = _seed_table(
        tmp_path,
        "t1",
        {
            ("ticker=A", "ym=2024-01"): 1,
            ("ticker=A", "ym=2024-02"): 1,
            ("ticker=B", "ym=2024-01"): 1,
            ("ticker=B", "ym=2024-02"): 1,
            ("ticker=C", "ym=2024-01"): 1,
        },
    )
    files, partitions, avg = _avg_files_per_partition(table_dir)
    assert files == 5
    assert partitions == 5
    assert avg == 1.0


def test_avg_many_files_per_partition(tmp_path: Path) -> None:
    """Fragmented: 2 partitions × 10 parquets each."""
    table_dir = _seed_table(
        tmp_path,
        "t2",
        {("ticker=A", "ym=2024-01"): 10, ("ticker=B", "ym=2024-01"): 10},
    )
    files, partitions, avg = _avg_files_per_partition(table_dir)
    assert files == 20
    assert partitions == 2
    assert avg == 10.0


def test_avg_unpartitioned_one_file(tmp_path: Path) -> None:
    """Un-partitioned table with a single parquet at the root."""
    table_dir = _seed_table(tmp_path, "t3", {(): 1})
    files, partitions, avg = _avg_files_per_partition(table_dir)
    assert files == 1
    assert partitions == 1
    assert avg == 1.0


def test_avg_empty_table(tmp_path: Path) -> None:
    table_dir = _seed_table(tmp_path, "t4", {})
    files, partitions, avg = _avg_files_per_partition(table_dir)
    assert files == 0
    assert partitions == 0
    assert avg == 0.0


def test_avg_missing_dir(tmp_path: Path) -> None:
    files, partitions, avg = _avg_files_per_partition(
        tmp_path / "does-not-exist",
    )
    assert (files, partitions, avg) == (0, 0, 0.0)


# ────────────────────────────────────────────────────────────────
# is_compaction_already_optimal


def test_optimal_returns_true_at_one_file_per_partition(
    tmp_path: Path,
) -> None:
    """Mirror the post-backfill stocks.intraday_bars shape."""
    table_dir = _seed_table(
        tmp_path,
        "intraday_bars",
        {
            ("ticker=BIOCON.NS", "ym=2024-01"): 1,
            ("ticker=BIOCON.NS", "ym=2024-02"): 1,
            ("ticker=INFY.NS", "ym=2024-01"): 1,
        },
    )
    assert is_compaction_already_optimal(table_dir) is True


def test_optimal_returns_true_at_threshold(tmp_path: Path) -> None:
    """At avg = 1.5 (the threshold), still skipped."""
    table_dir = _seed_table(
        tmp_path,
        "t",
        {("p", "x"): 2, ("p", "y"): 1},
    )
    files, partitions, avg = _avg_files_per_partition(table_dir)
    assert avg == 1.5
    assert is_compaction_already_optimal(table_dir) is True


def test_optimal_returns_false_above_threshold(
    tmp_path: Path,
) -> None:
    """Avg = 2.0 → must compact."""
    table_dir = _seed_table(
        tmp_path,
        "t",
        {("p", "x"): 2, ("p", "y"): 2},
    )
    assert is_compaction_already_optimal(table_dir) is False


def test_optimal_returns_false_for_empty_table(
    tmp_path: Path,
) -> None:
    """Empty table is NOT optimal — the compact_table caller has
    a separate empty-table branch that should fire (and log)."""
    table_dir = _seed_table(tmp_path, "t", {})
    assert is_compaction_already_optimal(table_dir) is False


def test_optimal_returns_false_for_missing_dir(
    tmp_path: Path,
) -> None:
    assert is_compaction_already_optimal(tmp_path / "nope") is False


# ────────────────────────────────────────────────────────────────
# compact_table integration


def test_compact_table_skips_when_optimal(tmp_path: Path) -> None:
    """When the table layout is one-file-per-partition,
    compact_table returns ``skipped_optimal=True`` and does NOT
    invoke the read+overwrite path. This is the exact scenario
    that caused yesterday's 6-hour wasted rewrite of
    stocks.intraday_bars."""
    table_dir = _seed_table(
        tmp_path,
        "stocks/intraday_bars",
        {
            ("ticker=A", "ym=2024-01"): 1,
            ("ticker=A", "ym=2024-02"): 1,
            ("ticker=B", "ym=2024-01"): 1,
        },
    )
    with (
        patch(
            "backend.maintenance.iceberg_maintenance.WAREHOUSE_DIR",
            tmp_path,
        ),
        patch(
            "tools._stock_shared._require_repo",
        ) as mock_repo,
    ):
        result = compact_table("stocks.intraday_bars")
    # Repo never touched — read+overwrite path skipped.
    assert not mock_repo.called
    assert result["skipped_optimal"] is True
    assert result["partitions"] == 3
    assert result["avg_files_per_partition"] == 1.0
    assert result["before"] == result["after"]
    # No actual rewrite occurred.
    assert _count_parquet_files(table_dir) == 3


def test_compact_table_does_not_skip_when_fragmented(
    tmp_path: Path,
) -> None:
    """A fragmented table (avg > threshold) must reach the
    overwrite path. We assert the smart-skip gate did NOT fire by
    verifying the repo load was invoked. We swallow the
    overwrite-time exception (no real catalog in this unit test)
    so the assertion is on the skip-vs-not-skip decision only.
    """
    table_dir = _seed_table(
        tmp_path,
        "stocks/intraday_bars",
        {("ticker=A", "ym=2024-01"): 10, ("ticker=B", "ym=2024-01"): 10},
    )
    assert _avg_files_per_partition(table_dir)[2] == 10.0
    with (
        patch(
            "backend.maintenance.iceberg_maintenance.WAREHOUSE_DIR",
            tmp_path,
        ),
        patch(
            "tools._stock_shared._require_repo",
            side_effect=RuntimeError("test-stop"),
        ) as mock_repo,
        pytest.raises(RuntimeError, match="test-stop"),
    ):
        compact_table("stocks.intraday_bars")
    # Repo lookup WAS reached → smart-skip gate did NOT fire.
    # If the skip gate had short-circuited, _require_repo would
    # never have been called and pytest.raises wouldn't catch.
    assert mock_repo.called


# ────────────────────────────────────────────────────────────────
# Constant pinning


def test_threshold_pinned_to_one_point_five() -> None:
    """Don't accidentally drift the threshold. Any change to
    _OPTIMAL_FILES_PER_PARTITION should be intentional + come
    with a comment explaining the operational reason."""
    assert _OPTIMAL_FILES_PER_PARTITION == 1.5
