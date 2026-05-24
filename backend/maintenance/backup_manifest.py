"""Daily-snapshot manifest writer / reader.

The manifest is the single source of truth for the Admin
Backup Health card and the pipeline step-0 freshness check.
It also IS the contract the cloud (S3) backup adopts after
cut-over — same fields, different storage backend.

Layout::

    backup-YYYY-MM-DD/
    ├── warehouse/
    │   └── <ns>/<table>/data/<partition>/*.parquet
    ├── catalog.db                (optional)
    └── manifest.json             ← this module owns it
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = 1


def _du_mb(path: Path) -> float:
    """Directory size in MB via ``du -sk`` (fast)."""
    try:
        r = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            kb = int(r.stdout.split()[0])
            return round(kb / 1024.0, 1)
    except Exception:
        pass
    total = sum(
        f.stat().st_size
        for f in path.rglob("*")
        if f.is_file()
    )
    return round(total / (1024 * 1024), 1)


def _walk_table(
    table_dir: Path,
) -> tuple[int, int, int]:
    """Return ``(partition_count, file_count,
    last_modified_ns)`` for an Iceberg table directory.
    """
    data = table_dir / "data"
    if not data.exists():
        return (0, 0, 0)
    partitions = 0
    files = 0
    latest_ns = 0
    for child in data.iterdir():
        if not child.is_dir():
            continue
        partitions += 1
        for p in child.rglob("*.parquet"):
            files += 1
            try:
                mt = p.stat().st_mtime_ns
                if mt > latest_ns:
                    latest_ns = mt
            except OSError:
                continue
    return (partitions, files, latest_ns)


def build_manifest(
    snapshot_root: Path,
    *,
    created_by: str,
    rsync_duration_s: int,
) -> dict:
    """Walk a snapshot root and return a manifest dict.

    Args:
        snapshot_root: ``backup-YYYY-MM-DD`` directory.
        created_by: Job identifier, e.g. ``"backups_daily"``.
        rsync_duration_s: How long the rsync took. Used for
            telemetry; informational only.

    Returns:
        Dict ready to be JSON-serialised.  See
        ``docs/superpowers/specs/2026-05-23-backup-redesign-design.md``
        for the full schema.
    """
    wh = snapshot_root / "warehouse"
    tables: list[dict] = []
    if wh.exists():
        for ns_dir in sorted(wh.iterdir()):
            if not ns_dir.is_dir():
                continue
            for tbl_dir in sorted(ns_dir.iterdir()):
                if not tbl_dir.is_dir():
                    continue
                parts, files, last_ns = _walk_table(tbl_dir)
                tables.append(
                    {
                        "id": f"{ns_dir.name}.{tbl_dir.name}",
                        "namespace": ns_dir.name,
                        "name": tbl_dir.name,
                        "size_mb": _du_mb(tbl_dir),
                        "partition_count": parts,
                        "file_count": files,
                        "last_modified_ns": last_ns,
                    }
                )
    catalog = snapshot_root / "catalog.db"
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_root.name.replace(
            "backup-", "",
        ),
        "created_at": (
            datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "created_by": created_by,
        "warehouse_root": str(wh),
        "warehouse_size_mb": _du_mb(wh) if wh.exists() else 0.0,
        "rsync_duration_s": rsync_duration_s,
        "catalog_present": catalog.exists(),
        "catalog_size_mb": (
            _du_mb(catalog.parent / catalog.name)
            if catalog.exists()
            else 0.0
        ),
        "tables": tables,
    }


def write_manifest(
    snapshot_root: Path,
    manifest: dict,
) -> Path:
    """Atomically write ``manifest.json`` (tmp + rename)."""
    snapshot_root.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".manifest-",
        suffix=".json",
        dir=str(snapshot_root),
    )
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
        final = snapshot_root / MANIFEST_FILENAME
        os.replace(tmp_path, final)
        return final
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_manifest(
    snapshot_root: Path,
) -> dict | None:
    """Read ``manifest.json``.  Returns None on missing /
    invalid JSON / wrong schema_version."""
    p = snapshot_root / MANIFEST_FILENAME
    if not p.exists():
        return None
    try:
        with open(p) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        _logger.warning(
            "manifest at %s is unreadable", p,
        )
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        _logger.warning(
            "manifest at %s has schema_version=%r, expected %d",
            p,
            data.get("schema_version"),
            SCHEMA_VERSION,
        )
        return None
    return data
