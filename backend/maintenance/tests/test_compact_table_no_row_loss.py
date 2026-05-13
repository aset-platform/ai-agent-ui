"""Regression test for the 2026-05-12 ``stocks.regime_history``
row-loss incident.

Prior implementation read the table through DuckDB
(``query_iceberg_df``) while ``cleanup_orphans_v2`` was racing the
in-process metadata cache. The compact step came back one row short,
``tbl.overwrite()`` committed the short payload, and the just-written
daily row was silently lost (snapshot history: APPEND 1 → DELETE
3050 → APPEND 3049).

The fix reads through ``tbl.refresh().scan().to_arrow()`` so the
reader and writer share the same ``Table`` object and snapshot —
the only invariant compaction actually needs. This test pins that
invariant.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest


@pytest.fixture
def fake_tbl():
    """A minimal stand-in for ``pyiceberg.table.Table`` that records
    the Arrow payload passed to ``overwrite()``."""

    overwritten: dict[str, pa.Table] = {}

    schema = pa.schema([
        pa.field("bar_date", pa.date32(), nullable=False),
        pa.field("regime_label", pa.string(), nullable=False),
    ])
    rows = pa.table(
        {
            "bar_date": pa.array(
                ["2026-05-10", "2026-05-11", "2026-05-12"],
                pa.string(),
            ).cast(pa.date32()),
            "regime_label": ["SIDEWAYS", "SIDEWAYS", "BULL"],
        },
        schema=schema,
    )

    iceberg_schema = MagicMock()
    iceberg_schema.as_arrow.return_value = schema

    tbl = MagicMock()
    tbl.schema.return_value = iceberg_schema
    tbl.refresh.return_value = tbl

    scan = MagicMock()
    scan.to_arrow.return_value = rows
    tbl.scan.return_value = scan

    def _overwrite(arrow):
        overwritten["payload"] = arrow

    tbl.overwrite.side_effect = _overwrite
    tbl._overwritten = overwritten
    return tbl


def test_compact_preserves_every_row_from_scan(
    fake_tbl, tmp_path: Path,
):
    """Compact must write back exactly what the scan returned.

    Reading via PyIceberg directly (rather than DuckDB) makes the
    snapshot the reader sees identical to the snapshot the writer
    will overwrite — eliminating the stale-read race that dropped
    the 2026-05-12 row from ``stocks.regime_history``.
    """
    repo = MagicMock()
    repo.load_table.return_value = fake_tbl
    table_dir = tmp_path / "stocks" / "regime_history"
    table_dir.mkdir(parents=True)

    with (
        patch(
            "backend.maintenance.iceberg_maintenance.WAREHOUSE_DIR",
            tmp_path,
        ),
        patch(
            "tools._stock_shared._require_repo",
            return_value=repo,
        ),
    ):
        from backend.maintenance.iceberg_maintenance import (
            compact_table,
        )
        result = compact_table("stocks.regime_history")

    fake_tbl.refresh.assert_called_once()
    assert "error" not in result, result
    assert result["rows"] == 3
    payload = fake_tbl._overwritten["payload"]
    assert payload.num_rows == 3, (
        "compact must round-trip every row from the scan; "
        "dropping one would re-trigger the 2026-05-12 regime_history "
        "incident"
    )


def test_compact_empty_table_short_circuits(
    fake_tbl, tmp_path: Path,
):
    """An empty table must not call ``overwrite()`` — a no-op
    overwrite would still rotate a new metadata file unnecessarily.
    """
    empty = pa.table(
        {
            "bar_date": pa.array([], pa.date32()),
            "regime_label": pa.array([], pa.string()),
        },
        schema=fake_tbl.schema().as_arrow(),
    )
    fake_tbl.scan.return_value.to_arrow.return_value = empty

    repo = MagicMock()
    repo.load_table.return_value = fake_tbl
    table_dir = tmp_path / "stocks" / "regime_history"
    table_dir.mkdir(parents=True)

    with (
        patch(
            "backend.maintenance.iceberg_maintenance.WAREHOUSE_DIR",
            tmp_path,
        ),
        patch(
            "tools._stock_shared._require_repo",
            return_value=repo,
        ),
    ):
        from backend.maintenance.iceberg_maintenance import (
            compact_table,
        )
        result = compact_table("stocks.regime_history")

    fake_tbl.overwrite.assert_not_called()
    assert result["rows"] == 0
