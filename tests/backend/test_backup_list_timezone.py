"""Backup ``list_backups`` timezone correctness.

The admin backup health UI computes ``age_hours`` from
``completed_at``. Prior implementation parsed the
folder-name date as a naive midnight in the container's
local TZ — at 09:00 IST, a backup taken 1.5h earlier
showed "9h ago" because the calculation walked from
midnight forward, not from the actual completion time.

These tests lock in:

1. ``completed_at`` is always present and stamped with
   the ISO 8601 UTC ``Z`` suffix (the convention used
   by other admin endpoints — needed so the FE's
   ``new Date()`` parses unambiguously).
2. The timestamp matches the directory mtime — which
   is what rsync sets at backup completion.
3. Folder-name suffixes (e.g. ``backup-2026-04-22-pre-dedupe``)
   don't break the function; they pass through as
   ``date`` verbatim and ``completed_at`` is still
   correct.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.maintenance.backup import list_backups


def _mkbackup(
    root: Path, name: str, mtime: float,
) -> Path:
    """Create a backup-shaped directory and stamp its
    mtime."""
    d = root / name
    d.mkdir(parents=True)
    # Sentinel content so size_mb is non-zero.
    (d / "marker").write_text("x")
    os.utime(d, (mtime, mtime))
    return d


def test_completed_at_iso_utc_with_z_suffix(tmp_path):
    expected = datetime(
        2026, 4, 25, 2, 46, 7, tzinfo=timezone.utc,
    )
    _mkbackup(
        tmp_path, "backup-2026-04-25",
        expected.timestamp(),
    )
    [b] = list_backups(backup_root=str(tmp_path))

    assert b["date"] == "2026-04-25"
    assert "completed_at" in b
    # ISO 8601 UTC with Z suffix
    assert re.match(
        r"^2026-04-25T02:46:07(\.\d+)?Z$",
        b["completed_at"],
    ), b["completed_at"]


def test_completed_at_matches_directory_mtime(
    tmp_path,
):
    """Stamping the dir at 08:16 IST (02:46 UTC) on
    2026-04-25 must surface the same time — never
    midnight, never local-naive."""
    ist_8_16_in_utc = datetime(
        2026, 4, 25, 2, 46, 7, tzinfo=timezone.utc,
    ).timestamp()
    _mkbackup(
        tmp_path, "backup-2026-04-25", ist_8_16_in_utc,
    )
    [b] = list_backups(backup_root=str(tmp_path))

    parsed = datetime.fromisoformat(
        b["completed_at"].replace("Z", "+00:00"),
    )
    assert parsed.tzinfo is not None
    assert (
        abs(parsed.timestamp() - ist_8_16_in_utc)
        < 1.0
    )


def test_suffixed_folder_name_preserved(tmp_path):
    """Manual-maintenance backups (e.g. pre-dedupe)
    keep their full date-suffix string in ``date``
    and still get a correct mtime-based timestamp."""
    when = datetime(
        2026, 4, 22, 0, 0, 0, tzinfo=timezone.utc,
    ).timestamp()
    _mkbackup(
        tmp_path,
        "backup-2026-04-22-pre-dedupe",
        when,
    )
    [b] = list_backups(backup_root=str(tmp_path))

    assert b["date"] == "2026-04-22-pre-dedupe"
    assert b["completed_at"].endswith("Z")


def test_age_calculation_no_longer_uses_midnight(
    tmp_path,
):
    """Regression: a backup completed at 08:16 IST
    (02:46 UTC) should not report '9h ago' when
    queried at 08:34 IST. The age should equal the
    actual elapsed wall-clock seconds, regardless of
    container TZ.
    """
    backup_completion = datetime(
        2026, 4, 25, 2, 46, 0, tzinfo=timezone.utc,
    ).timestamp()
    _mkbackup(
        tmp_path, "backup-2026-04-25",
        backup_completion,
    )

    [b] = list_backups(backup_root=str(tmp_path))
    completed = datetime.fromisoformat(
        b["completed_at"].replace("Z", "+00:00"),
    )

    # Simulate "now" 18 minutes after completion.
    now_18min_later = (
        backup_completion + (18 * 60)
    )
    age_hours = (
        now_18min_later - completed.timestamp()
    ) / 3600

    # Must be ~0.3, NOT ~8.5 (which is what midnight-
    # based calculation would have given at 08:34 IST).
    assert 0.25 < age_hours < 0.35, age_hours
