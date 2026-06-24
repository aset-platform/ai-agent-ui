"""Regression tests for the estimated-Arrow-memory compaction guard
added after the 2026-06-24 OOM incident.

Background: ``compact_table`` reads the WHOLE table into an in-process
Arrow table (``scan().to_arrow()`` → ``cast`` → ``overwrite``, ~2-3
live copies) before committing. The pre-existing safety guard
compared *compressed on-disk parquet bytes* against
``_MAX_SAFE_COMPACT_BYTES`` (1 GiB) to decide whether to route to
batched per-month compaction instead.

That proxy is wrong for highly-compressible feature data:
``stocks.intraday_features`` was 491 MB on disk (well under 1 GiB) but
70.5M rows × 10 cols ≈ 5.3 GB in Arrow. The in-process path's copies
pushed peak RAM past the ~11.7 GiB VM and the kernel OOM-killer
SIGKILLed the uvicorn worker — and because the backend runs under
``uvicorn --reload`` (whose supervisor only respawns on file changes,
not on worker death) the whole backend stayed down for hours.

The fix gates on the ESTIMATED Arrow footprint
(``total-records`` × columns × bytes/cell, read from the snapshot
summary — no data scan), so byte-light-but-row-heavy tables route to
the memory-safe per-month path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.maintenance.iceberg_maintenance import (
    _ARROW_BYTES_PER_CELL,
    _MAX_SAFE_COMPACT_ARROW_BYTES,
    _avg_files_per_partition,
    _estimated_arrow_bytes,
    compact_table,
)


def _seed_table(
    root: Path,
    name: str,
    layout: dict[tuple[str, ...], int],
) -> Path:
    """Build a fake table directory of 1-byte parquet files.

    layout: ``{(partition_path_parts): num_parquets}``.
    """
    data_dir = root / name / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for parts, n in layout.items():
        partition = data_dir.joinpath(*parts) if parts else data_dir
        partition.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (partition / f"{i:05d}.parquet").write_bytes(b"x")
    return root / name


# ────────────────────────────────────────────────────────────────
# _estimated_arrow_bytes


def test_estimated_arrow_bytes_uses_row_count() -> None:
    """Estimate = total-records × columns × bytes-per-cell."""
    tbl = MagicMock()
    tbl.current_snapshot.return_value.summary = {
        "total-records": "1000",
    }
    tbl.schema.return_value.fields = [object()] * 10
    with patch("tools._stock_shared._require_repo") as mock_repo:
        mock_repo.return_value.load_table.return_value = tbl
        est = _estimated_arrow_bytes("stocks.intraday_features")
    assert est == 1000 * 10 * _ARROW_BYTES_PER_CELL


def test_estimated_arrow_bytes_none_when_no_snapshot() -> None:
    """An empty table (no current snapshot) returns None so the
    caller falls back to the on-disk byte guard."""
    tbl = MagicMock()
    tbl.current_snapshot.return_value = None
    with patch("tools._stock_shared._require_repo") as mock_repo:
        mock_repo.return_value.load_table.return_value = tbl
        assert _estimated_arrow_bytes("stocks.x") is None


def test_estimated_arrow_bytes_none_when_summary_missing_records(
) -> None:
    tbl = MagicMock()
    tbl.current_snapshot.return_value.summary = {}
    tbl.schema.return_value.fields = [object()] * 5
    with patch("tools._stock_shared._require_repo") as mock_repo:
        mock_repo.return_value.load_table.return_value = tbl
        assert _estimated_arrow_bytes("stocks.x") is None


def test_estimated_arrow_bytes_none_on_error() -> None:
    """Any load/inspect failure degrades gracefully to None."""
    with patch(
        "tools._stock_shared._require_repo",
        side_effect=RuntimeError("catalog down"),
    ):
        assert _estimated_arrow_bytes("stocks.x") is None


# ────────────────────────────────────────────────────────────────
# compact_table routing


def test_high_rowcount_routes_to_per_month(tmp_path: Path) -> None:
    """The exact 2026-06-24 regression: a fragmented, byte-light
    (would pass the disk-byte guard) but row-heavy table whose
    estimated Arrow footprint exceeds the in-process ceiling MUST
    route to _compact_table_by_month rather than OOM in-process."""
    _seed_table(
        tmp_path,
        "stocks/intraday_features",
        {("tb=0", "bm=600"): 2, ("tb=1", "bm=600"): 2},
    )
    sentinel = {"table": "stocks.intraday_features", "batched": True}
    with (
        patch(
            "backend.maintenance.iceberg_maintenance.WAREHOUSE_DIR",
            tmp_path,
        ),
        patch(
            "backend.maintenance.iceberg_maintenance."
            "_estimated_arrow_bytes",
            return_value=_MAX_SAFE_COMPACT_ARROW_BYTES + 1,
        ),
        patch(
            "backend.maintenance.iceberg_maintenance."
            "_compact_table_by_month",
            return_value=sentinel,
        ) as mock_batch,
    ):
        result = compact_table("stocks.intraday_features")
    mock_batch.assert_called_once_with("stocks.intraday_features")
    assert result is sentinel


def test_low_rowcount_stays_in_process(tmp_path: Path) -> None:
    """A table under the Arrow ceiling must NOT route to per-month —
    it stays on the single-overwrite fast path (intraday_bars,
    1.1 GB est). We stop at the repo load and assert the routing
    decision: _compact_table_by_month was never called."""
    _seed_table(
        tmp_path,
        "stocks/intraday_bars",
        {("tb=0", "bm=600"): 2, ("tb=1", "bm=600"): 2},
    )
    assert _avg_files_per_partition(
        tmp_path / "stocks/intraday_bars"
    )[2] == 2.0
    with (
        patch(
            "backend.maintenance.iceberg_maintenance.WAREHOUSE_DIR",
            tmp_path,
        ),
        patch(
            "backend.maintenance.iceberg_maintenance."
            "_estimated_arrow_bytes",
            return_value=_MAX_SAFE_COMPACT_ARROW_BYTES - 1,
        ),
        patch(
            "backend.maintenance.iceberg_maintenance."
            "_compact_table_by_month",
        ) as mock_batch,
        patch(
            "tools._stock_shared._require_repo",
            side_effect=RuntimeError("stop-at-read"),
        ),
    ):
        result = compact_table("stocks.intraday_bars")
    assert not mock_batch.called
    assert result.get("error") == "read failed"


# ────────────────────────────────────────────────────────────────
# Constant pinning


def test_arrow_ceiling_separates_known_tables() -> None:
    """Pin the ceiling between the two production tables it must
    discriminate, so a careless edit can't reopen the OOM window:
      intraday_bars      ~1.1 GB est → in-process (safe, observed)
      intraday_features  ~5.3 GB est → per-month  (OOM'd in-process)
    """
    gib = 1024 * 1024 * 1024
    intraday_bars_est = 11_377_837 * 13 * _ARROW_BYTES_PER_CELL
    features_est = 70_515_619 * 10 * _ARROW_BYTES_PER_CELL
    assert intraday_bars_est < _MAX_SAFE_COMPACT_ARROW_BYTES
    assert features_est > _MAX_SAFE_COMPACT_ARROW_BYTES
    assert _MAX_SAFE_COMPACT_ARROW_BYTES == 2 * gib
    assert _ARROW_BYTES_PER_CELL == 8
