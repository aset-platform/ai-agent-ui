"""Tests for the time-based snapshot retention floor
(ASETPLTFRM-429).

``cleanup_orphans_v2`` previously expired snapshots by count only
(``SNAPSHOT_KEEP=5``). On a high-commit table that burns through 5
snapshots in hours, a morning snapshot got expired by evening and its
manifest-list deleted out from under a daily reader's metadata cache —
``IO Error: No files found ... snap-*.avro``. ``_snapshots_to_expire``
adds an age floor so recent snapshots are never expired.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.maintenance.iceberg_maintenance import (
    _snapshots_to_expire,
)

_HOUR_MS = 3600 * 1000
_NOW = 1_000 * _HOUR_MS  # arbitrary fixed "now" in ms


def _snap(sid: int, age_hours: float):
    """Snapshot stub aged *age_hours* before ``_NOW``."""
    return SimpleNamespace(
        snapshot_id=sid,
        timestamp_ms=_NOW - int(age_hours * _HOUR_MS),
    )


def test_count_floor_when_all_old():
    """All snapshots older than the age floor -> only the latest
    N survive (pure count behaviour)."""
    snaps = [_snap(i, age_hours=100 + i) for i in range(8)]
    expire = _snapshots_to_expire(
        snaps, retain_count=5, min_age_ms=48 * _HOUR_MS, now_ms=_NOW,
    )
    # 8 total, keep newest 5 -> expire the 3 oldest.
    assert len(expire) == 3
    # Oldest = largest age = ids 5,6,7 here.
    assert set(expire) == {5, 6, 7}


def test_age_floor_protects_recent_beyond_count():
    """Snapshots younger than the floor are kept even when they
    fall outside the latest-N count window."""
    # 10 snapshots, 1h apart, all within 48h -> none expirable.
    snaps = [_snap(i, age_hours=i) for i in range(10)]
    expire = _snapshots_to_expire(
        snaps, retain_count=5, min_age_ms=48 * _HOUR_MS, now_ms=_NOW,
    )
    assert expire == []


def test_mixed_keeps_recent_and_newest_n():
    """The kept set is (latest N) UNION (younger than floor);
    only snapshots outside BOTH are expired."""
    # ids 0..2 recent (1-3h), ids 3..9 old (60h+).
    snaps = [_snap(i, age_hours=1 + i) for i in range(3)]
    snaps += [_snap(i, age_hours=60 + i) for i in range(3, 10)]
    expire = _snapshots_to_expire(
        snaps, retain_count=5, min_age_ms=48 * _HOUR_MS, now_ms=_NOW,
    )
    # Keep: newest 5 by ts (ids 0,1,2 + two newest of the old set =
    # ids 3,4) PLUS anything <48h (already 0,1,2). So kept =
    # {0,1,2,3,4}; expire the 5 oldest old ones {5,6,7,8,9}.
    assert set(expire) == {5, 6, 7, 8, 9}


def test_no_expiry_when_under_count():
    """Fewer snapshots than the count floor -> nothing expired."""
    snaps = [_snap(i, age_hours=100 + i) for i in range(3)]
    expire = _snapshots_to_expire(
        snaps, retain_count=5, min_age_ms=48 * _HOUR_MS, now_ms=_NOW,
    )
    assert expire == []
