# Backup Redesign — Manifest-Driven Daily Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 30+ per-pipeline per-table backups/day with one nightly warehouse snapshot + manifest.json, fixing the Admin Backup Health card's broken size aggregate and reclaiming ~5–6 GB of disk.

**Architecture:** A new `backups_daily` scheduler job at 00:30 IST writes `backup-YYYY-MM-DD/{warehouse,catalog.db,manifest.json}`. Pipelines call a new `verify_or_backup()` helper that returns "verified" when the manifest is fresh and covers their scoped tables, falling back to per-table backup otherwise. Admin endpoints read sizes / table lists from the manifest. The manifest format is the cloud-migration contract.

**Tech Stack:** Python 3.12 (FastAPI backend, pytest, monkeypatch fixtures), SQLAlchemy 2.0 async (pipeline seeds), Next.js 16 + React 19 (Admin UI), Vitest (frontend tests), rsync (warehouse copy).

**Reference spec:** `docs/superpowers/specs/2026-05-23-backup-redesign-design.md`.

**Branch:** Work on `feature/backup-redesign` (already created off `dev`). Squash merge per CLAUDE.md §4.4 #27.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `backend/maintenance/backup_manifest.py` | create | Pure functions: `build_manifest`, `write_manifest`, `read_manifest`, schema constants |
| `backend/maintenance/tests/test_backup_manifest.py` | create | Unit tests for manifest module |
| `backend/maintenance/backup.py` | modify | Add `verify_or_backup()` helper alongside existing `run_backup` / `backup_table` |
| `backend/maintenance/tests/test_verify_or_backup.py` | create | Unit tests for the new helper |
| `backend/jobs/executor.py` | modify | Add `@register_job("backups_daily")` body; refactor `iceberg_maintenance` step-0 to use `verify_or_backup` |
| `backend/jobs/tests/test_backups_daily.py` | create | Integration test for the new job |
| `backend/jobs/tests/test_iceberg_maintenance_step0.py` | create | Integration test for the step-0 refactor |
| `scripts/seed_backups_daily_pipeline.py` | create | Seed the new pipeline / cron entry |
| `scripts/cleanup_per_table_backups.py` | create | One-shot cleanup of legacy per-table dirs |
| `backend/routes.py` | modify | `_admin_backups_*` handlers read manifest |
| `backend/tests/test_admin_backup_routes.py` | create | HTTP-level tests for the three admin endpoints |
| `frontend/components/admin/BackupHealthPanel.tsx` | modify | Read `warehouse_size_mb` + `table_count` from manifest payload; add TABLES tile |
| `frontend/components/admin/__tests__/BackupHealthPanel.test.tsx` | create | Vitest snapshot of new tile layout |
| `PROGRESS.md` | modify | Dated session entry |

The implementation lives in nine tasks, each ending in a commit. Tasks 1–4 are backend-pure and can be reviewed independently; task 5 wires them into the pipeline; tasks 6–7 surface the change in the Admin UI; tasks 8–9 schedule and clean up.

---

## Task 1: Manifest writer module

**Files:**
- Create: `backend/maintenance/backup_manifest.py`
- Create: `backend/maintenance/tests/test_backup_manifest.py`

**Why first:** Pure-function module with no dependencies on the rest of the redesign. Everything downstream uses these three functions.

- [ ] **Step 1.1: Write failing test for `build_manifest`**

Create `backend/maintenance/tests/test_backup_manifest.py`:

```python
"""Tests for backend.maintenance.backup_manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.maintenance.backup_manifest import (
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    build_manifest,
    read_manifest,
    write_manifest,
)


def _make_snapshot(
    tmp_path: Path,
    tables: dict[str, list[tuple[str, int]]],
    *,
    with_catalog: bool = True,
) -> Path:
    """Build a fixture snapshot.

    ``tables`` maps ``"<ns>.<name>"`` to a list of
    ``(partition_dir, file_bytes)`` tuples.  One parquet file is
    written per tuple so file_count and size_mb are predictable.
    """
    root = tmp_path / "backup-2026-05-23"
    wh = root / "warehouse"
    for table_id, parts in tables.items():
        ns, name = table_id.split(".", 1)
        data_root = wh / ns / name / "data"
        for part_dir, nbytes in parts:
            part = data_root / part_dir
            part.mkdir(parents=True, exist_ok=True)
            (part / "00000.parquet").write_bytes(
                b"x" * nbytes,
            )
    if with_catalog:
        (root / "catalog.db").write_bytes(b"x" * 1024)
    return root


def test_build_manifest_lists_all_tables(tmp_path):
    root = _make_snapshot(
        tmp_path,
        tables={
            "stocks.ohlcv": [
                ("date_month=2026-05", 2_000_000),
                ("date_month=2026-04", 1_500_000),
            ],
            "algo.events": [
                ("mode=paper", 500_000),
            ],
        },
    )
    m = build_manifest(
        root,
        created_by="test",
        rsync_duration_s=12,
    )
    assert m["schema_version"] == SCHEMA_VERSION
    ids = sorted(t["id"] for t in m["tables"])
    assert ids == ["algo.events", "stocks.ohlcv"]
    ohlcv = next(
        t for t in m["tables"] if t["id"] == "stocks.ohlcv"
    )
    assert ohlcv["partition_count"] == 2
    assert ohlcv["file_count"] == 2
    assert ohlcv["size_mb"] == pytest.approx(3.3, abs=0.2)
    assert m["catalog_present"] is True
    assert m["rsync_duration_s"] == 12
    assert m["created_by"] == "test"
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/maintenance/tests/test_backup_manifest.py \
  -v
```

Expected: `ImportError` on `backend.maintenance.backup_manifest` (module doesn't exist yet).

- [ ] **Step 1.3: Implement the module**

Create `backend/maintenance/backup_manifest.py`:

```python
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
```

- [ ] **Step 1.4: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/maintenance/tests/test_backup_manifest.py::test_build_manifest_lists_all_tables \
  -v
```

Expected: PASS.

- [ ] **Step 1.5: Add `write_manifest` + `read_manifest` tests**

Append to `backend/maintenance/tests/test_backup_manifest.py`:

```python
def test_write_manifest_atomic_no_partial_file(tmp_path):
    root = tmp_path / "backup-2026-05-23"
    root.mkdir()
    m = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": "2026-05-23",
        "tables": [],
        "created_by": "test",
        "created_at": "2026-05-23T00:30:00Z",
        "warehouse_size_mb": 0.0,
        "catalog_present": False,
    }
    final = write_manifest(root, m)
    assert final.exists()
    # No tmp leftover
    assert not list(root.glob(".manifest-*.json"))


def test_read_manifest_returns_none_when_absent(tmp_path):
    assert read_manifest(tmp_path) is None


def test_read_manifest_returns_none_on_invalid_json(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text("not json")
    assert read_manifest(tmp_path) is None


def test_read_manifest_returns_none_on_wrong_schema(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        json.dumps({"schema_version": 999, "tables": []}),
    )
    assert read_manifest(tmp_path) is None


def test_read_manifest_round_trip(tmp_path):
    root = _make_snapshot(
        tmp_path,
        tables={
            "stocks.ohlcv": [
                ("date_month=2026-05", 1000),
            ],
        },
    )
    m = build_manifest(
        root, created_by="rt", rsync_duration_s=5,
    )
    write_manifest(root, m)
    again = read_manifest(root)
    assert again is not None
    assert again["snapshot_id"] == "2026-05-23"
    assert len(again["tables"]) == 1
```

- [ ] **Step 1.6: Run full test file**

```bash
docker compose exec backend python -m pytest \
  backend/maintenance/tests/test_backup_manifest.py -v
```

Expected: 5 PASS.

- [ ] **Step 1.7: Commit**

```bash
git add backend/maintenance/backup_manifest.py \
        backend/maintenance/tests/test_backup_manifest.py
git commit -m "$(cat <<'EOF'
feat(backup): manifest writer / reader module

Pure-function module that walks a snapshot root and writes the
manifest.json the Admin UI + step-0 freshness check will read.
Atomic writes via tmp + rename; schema_version-gated reads.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: `backups_daily` scheduler job

**Files:**
- Modify: `backend/jobs/executor.py` (add new `@register_job` block)
- Create: `backend/jobs/tests/test_backups_daily.py`

**Why second:** With the manifest module in place, the job is `run_backup()` + `build_manifest()` + `write_manifest()` glued together with timing and observability.

- [ ] **Step 2.1: Write failing integration test**

Create `backend/jobs/tests/test_backups_daily.py`:

```python
"""Integration test for the backups_daily scheduler job."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.jobs.executor import JOB_EXECUTORS
from backend.maintenance.backup_manifest import (
    MANIFEST_FILENAME,
    read_manifest,
)


def test_backups_daily_writes_manifest(tmp_path, monkeypatch):
    """The job rsyncs the warehouse and writes manifest.json."""
    # Arrange — fake warehouse with two tables
    warehouse = tmp_path / "warehouse"
    (warehouse / "stocks" / "ohlcv" / "data" / "p=1").mkdir(
        parents=True,
    )
    (warehouse / "stocks" / "ohlcv" / "data" / "p=1" / "a.parquet").write_bytes(b"x" * 2048)
    (warehouse / "algo" / "events" / "data" / "p=2").mkdir(
        parents=True,
    )
    (warehouse / "algo" / "events" / "data" / "p=2" / "b.parquet").write_bytes(b"x" * 1024)
    (tmp_path / "catalog.db").write_bytes(b"x" * 512)

    backup_root = tmp_path / "backups"

    monkeypatch.setattr(
        "backend.maintenance.backup.WAREHOUSE_DIR",
        str(warehouse),
    )
    monkeypatch.setattr(
        "backend.maintenance.backup.BACKUP_ROOT",
        str(backup_root),
    )

    repo = MagicMock()
    fn = JOB_EXECUTORS["backups_daily"]

    # Act
    fn(repo=repo, run_id="run-1", payload={}, cancel_event=None)

    # Assert — exactly one full snapshot
    snapshots = list(backup_root.glob("backup-*"))
    assert len(snapshots) == 1
    manifest = read_manifest(snapshots[0])
    assert manifest is not None
    ids = sorted(t["id"] for t in manifest["tables"])
    assert ids == ["algo.events", "stocks.ohlcv"]
    assert manifest["catalog_present"] is True
    assert manifest["created_by"] == "backups_daily"
    # rsync_duration_s is a real measurement, just ensure it's >= 0
    assert manifest["rsync_duration_s"] >= 0


def test_backups_daily_fails_closed_on_rsync_error(
    tmp_path, monkeypatch,
):
    """If run_backup raises, the manifest is NOT written."""
    backup_root = tmp_path / "backups"
    monkeypatch.setattr(
        "backend.maintenance.backup.WAREHOUSE_DIR",
        str(tmp_path / "missing-warehouse"),
    )
    monkeypatch.setattr(
        "backend.maintenance.backup.BACKUP_ROOT",
        str(backup_root),
    )

    repo = MagicMock()
    fn = JOB_EXECUTORS["backups_daily"]

    # rsync against a nonexistent source returns rc=23
    with patch(
        "backend.maintenance.backup.subprocess.run"
    ) as mock_run:
        mock_run.return_value.returncode = 23
        mock_run.return_value.stderr = "rsync: link_stat failed"
        with pytest.raises(RuntimeError, match="Backup failed"):
            fn(
                repo=repo,
                run_id="run-2",
                payload={},
                cancel_event=None,
            )
    # No manifest left behind
    assert list(backup_root.rglob(MANIFEST_FILENAME)) == []
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/jobs/tests/test_backups_daily.py -v
```

Expected: `KeyError: 'backups_daily'` (job not registered yet).

- [ ] **Step 2.3: Implement the job in `backend/jobs/executor.py`**

Locate the existing `@register_job("iceberg_maintenance")` around line 2685. Insert the new job IMMEDIATELY BEFORE it (so related jobs cluster). Add:

```python
@register_job("backups_daily")
def execute_backups_daily(
    *,
    repo,
    run_id,
    payload,
    cancel_event=None,
):
    """Once-a-day full warehouse snapshot + manifest write.

    Replaces per-pipeline ``backup_table()`` calls. Runs at
    00:30 IST via the ``Backups Daily`` pipeline (seeded by
    ``scripts/seed_backups_daily_pipeline.py``).

    Steps:

    1. ``run_backup()``  — rsync warehouse → ``backup-YYYY-MM-DD/``
       + copy catalog.db + rotate (keep MAX_BACKUPS=2).
    2. ``build_manifest()`` walks the snapshot, returns the
       summary dict.
    3. ``write_manifest()`` atomically writes manifest.json
       at the snapshot root.

    Fail-closed: any exception aborts the job; the manifest is
    not written, so step-0 verify_or_backup() will see "stale"
    and fall back to per-table backup on the next pipeline run.
    """
    import time
    from pathlib import Path

    from backend.maintenance.backup import run_backup
    from backend.maintenance.backup_manifest import (
        build_manifest,
        write_manifest,
    )

    _logger.info("[backups_daily] starting full snapshot")
    t0 = time.monotonic()
    snapshot_path = run_backup()
    elapsed = int(time.monotonic() - t0)
    _logger.info(
        "[backups_daily] rsync complete in %ds: %s",
        elapsed,
        snapshot_path,
    )

    manifest = build_manifest(
        Path(snapshot_path),
        created_by="backups_daily",
        rsync_duration_s=elapsed,
    )
    write_manifest(Path(snapshot_path), manifest)
    _logger.info(
        "[backups_daily] manifest written: %d tables, "
        "warehouse %.1f MB",
        len(manifest["tables"]),
        manifest["warehouse_size_mb"],
    )

    try:
        repo.update_scheduler_run(
            run_id,
            {
                "tickers_done": len(manifest["tables"]),
                "tickers_total": len(manifest["tables"]),
            },
        )
    except Exception:
        pass
```

- [ ] **Step 2.4: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/jobs/tests/test_backups_daily.py -v
```

Expected: 2 PASS.

- [ ] **Step 2.5: Restart backend** (new `@register_job` decorator — per CLAUDE.md §6.2)

```bash
docker compose restart backend
sleep 5
```

- [ ] **Step 2.6: Commit**

```bash
git add backend/jobs/executor.py \
        backend/jobs/tests/test_backups_daily.py
git commit -m "$(cat <<'EOF'
feat(backup): backups_daily scheduler job

Once-a-day full warehouse snapshot + manifest write. Replaces
per-pipeline backup_table() loops. Fail-closed: rsync error
aborts before manifest write, so step-0 verify_or_backup sees
stale and falls back per-table.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: `verify_or_backup()` helper

**Files:**
- Modify: `backend/maintenance/backup.py` (append the new function)
- Create: `backend/maintenance/tests/test_verify_or_backup.py`

- [ ] **Step 3.1: Write failing tests**

Create `backend/maintenance/tests/test_verify_or_backup.py`:

```python
"""Tests for verify_or_backup()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.maintenance.backup_manifest import (
    SCHEMA_VERSION,
    write_manifest,
)


def _today_root(backup_root: Path) -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return backup_root / f"backup-{today}"


def _seed_manifest(
    backup_root: Path,
    *,
    table_ids: list[str],
    age_hours: float = 0.5,
):
    root = _today_root(backup_root)
    root.mkdir(parents=True, exist_ok=True)
    created = (
        datetime.now(timezone.utc)
        - timedelta(hours=age_hours)
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": root.name.replace("backup-", ""),
        "created_at": created.isoformat().replace(
            "+00:00", "Z",
        ),
        "created_by": "test",
        "warehouse_size_mb": 100.0,
        "catalog_present": True,
        "tables": [
            {"id": tid, "size_mb": 1.0} for tid in table_ids
        ],
    }
    write_manifest(root, manifest)
    return root


def test_verify_returns_verified_when_fresh_and_covers(
    tmp_path,
):
    from backend.maintenance.backup import verify_or_backup

    _seed_manifest(
        tmp_path,
        table_ids=["stocks.ohlcv", "stocks.dividends"],
        age_hours=0.5,
    )
    with patch(
        "backend.maintenance.backup.backup_table"
    ) as bt:
        result = verify_or_backup(
            ["stocks.ohlcv"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "verified"
    assert result["snapshot"] == str(_today_root(tmp_path))
    bt.assert_not_called()


def test_verify_falls_back_when_no_manifest(tmp_path):
    from backend.maintenance.backup import verify_or_backup

    with patch(
        "backend.maintenance.backup.backup_table"
    ) as bt:
        bt.return_value = "/tmp/fake"
        result = verify_or_backup(
            ["stocks.ohlcv"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "fallback_per_table"
    assert bt.call_count == 1
    assert result["paths"] == ["/tmp/fake"]


def test_verify_falls_back_when_stale(tmp_path):
    from backend.maintenance.backup import verify_or_backup

    _seed_manifest(
        tmp_path,
        table_ids=["stocks.ohlcv"],
        age_hours=30,  # > 24h default
    )
    with patch(
        "backend.maintenance.backup.backup_table"
    ) as bt:
        bt.return_value = "/tmp/stale-fallback"
        result = verify_or_backup(
            ["stocks.ohlcv"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "fallback_per_table"
    bt.assert_called_once_with("stocks.ohlcv")


def test_verify_falls_back_when_table_missing_from_manifest(
    tmp_path,
):
    from backend.maintenance.backup import verify_or_backup

    _seed_manifest(
        tmp_path,
        table_ids=["stocks.ohlcv"],  # missing dividends
    )
    with patch(
        "backend.maintenance.backup.backup_table"
    ) as bt:
        bt.return_value = "/tmp/x"
        result = verify_or_backup(
            ["stocks.ohlcv", "stocks.dividends"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "fallback_per_table"
    assert bt.call_count == 2


def test_verify_swallows_filenotfound_in_fallback(tmp_path):
    """Per-table fallback handles "table never written"
    the same way the existing scoped-maintenance path does
    — log + continue."""
    from backend.maintenance.backup import verify_or_backup

    def _bt(t):
        if t == "stocks.never_written":
            raise FileNotFoundError("no data")
        return f"/tmp/{t}"

    with patch(
        "backend.maintenance.backup.backup_table",
        side_effect=_bt,
    ):
        result = verify_or_backup(
            ["stocks.ohlcv", "stocks.never_written"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "fallback_per_table"
    assert result["paths"] == ["/tmp/stocks.ohlcv"]
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest \
  backend/maintenance/tests/test_verify_or_backup.py -v
```

Expected: `ImportError: cannot import name 'verify_or_backup'`.

- [ ] **Step 3.3: Implement the helper**

Append to `backend/maintenance/backup.py` (after `list_backups`, before `_rotate_backups`):

```python
def verify_or_backup(
    tables: list[str],
    *,
    max_age_h: float = 24.0,
    backup_root: str | None = None,
) -> dict:
    """Check today's snapshot covers ``tables``; fall back
    to per-table ``backup_table()`` if not.

    Returns:
        ``{"mode": "verified",
           "snapshot": "<path>",
           "paths": []}``
        when the manifest is fresh AND lists every requested
        table.

        ``{"mode": "fallback_per_table",
           "snapshot": None,
           "paths": [<per-table-backup-paths>]}``
        otherwise.

    The fallback path swallows ``FileNotFoundError`` (table
    never written) the same way the legacy scoped-maintenance
    branch does — log + continue.
    """
    from datetime import datetime, timezone

    from backend.maintenance.backup_manifest import read_manifest

    root = Path(backup_root or BACKUP_ROOT)
    today = date.today().isoformat()
    snapshot_root = root / f"backup-{today}"

    manifest = read_manifest(snapshot_root)
    if manifest is not None:
        created_iso = manifest.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(
                created_iso.replace("Z", "+00:00"),
            )
        except ValueError:
            created_at = None
        if created_at is not None:
            age_h = (
                datetime.now(timezone.utc) - created_at
            ).total_seconds() / 3600
            listed = {
                t["id"] for t in manifest.get("tables", [])
            }
            if age_h <= max_age_h and set(tables) <= listed:
                return {
                    "mode": "verified",
                    "snapshot": str(snapshot_root),
                    "paths": [],
                }

    paths: list[str] = []
    for t in tables:
        try:
            paths.append(backup_table(t))
        except FileNotFoundError:
            _logger.info(
                "[verify_or_backup] %s: no on-disk data, "
                "skipping per-table backup",
                t,
            )
    return {
        "mode": "fallback_per_table",
        "snapshot": None,
        "paths": paths,
    }
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/maintenance/tests/test_verify_or_backup.py -v
```

Expected: 5 PASS.

- [ ] **Step 3.5: Commit**

```bash
git add backend/maintenance/backup.py \
        backend/maintenance/tests/test_verify_or_backup.py
git commit -m "$(cat <<'EOF'
feat(backup): verify_or_backup helper

Reads today's manifest.json; if fresh (<24h) and covers all
requested tables, returns mode=verified with no I/O. Else
falls back to per-table backup_table() loop — same safety
rail the scoped-maintenance path enforces today.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Pipeline step-0 refactor

**Files:**
- Modify: `backend/jobs/executor.py` (the `iceberg_maintenance` body around line 2805–2840)
- Create: `backend/jobs/tests/test_iceberg_maintenance_step0.py`

- [ ] **Step 4.1: Write failing test**

Create `backend/jobs/tests/test_iceberg_maintenance_step0.py`:

```python
"""Step-0 refactor: scoped maintenance defers to
verify_or_backup() instead of looping backup_table()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def _fake_repo():
    repo = MagicMock()
    repo.get_scheduler_run.return_value = {"status": "running"}
    return repo


def test_step0_uses_verify_or_backup_when_scoped(_fake_repo):
    """Scoped runs delegate to verify_or_backup, NOT
    backup_table directly."""
    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["iceberg_maintenance"]
    payload = {"tables": ["stocks.ohlcv"]}

    with patch(
        "backend.maintenance.backup.verify_or_backup",
        return_value={
            "mode": "verified",
            "snapshot": "/tmp/backup-2026-05-23",
            "paths": [],
        },
    ) as vob, patch(
        "backend.maintenance.backup.backup_table"
    ) as bt, patch(
        "backend.maintenance.iceberg_maintenance.compact_table"
    ), patch(
        "backend.maintenance.iceberg_maintenance.cleanup_orphans_v2"
    ):
        fn(
            repo=_fake_repo,
            run_id="run-1",
            payload=payload,
            cancel_event=None,
        )
    vob.assert_called_once()
    args, _ = vob.call_args
    assert list(args[0]) == ["stocks.ohlcv"]
    bt.assert_not_called()


def test_step0_full_warehouse_path_unchanged_when_unscoped(
    _fake_repo,
):
    """Empty payload still triggers run_backup()."""
    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["iceberg_maintenance"]

    with patch(
        "backend.maintenance.backup.run_backup",
        return_value="/tmp/backup-2026-05-23",
    ) as rb, patch(
        "backend.maintenance.backup.verify_or_backup"
    ) as vob, patch(
        "backend.maintenance.iceberg_maintenance.compact_table"
    ), patch(
        "backend.maintenance.iceberg_maintenance.cleanup_orphans_v2"
    ):
        fn(
            repo=_fake_repo,
            run_id="run-2",
            payload={},
            cancel_event=None,
        )
    rb.assert_called_once()
    vob.assert_not_called()
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/jobs/tests/test_iceberg_maintenance_step0.py -v
```

Expected: the scoped test fails because the current code calls `backup_table()` directly, not `verify_or_backup`.

- [ ] **Step 4.3: Refactor `backend/jobs/executor.py`**

Locate the block in `@register_job("iceberg_maintenance")` from approximately line 2814 to line 2840 (the `try: if scoped: paths: list[str] = [] for t in tables: ... backup_path = "; ".join(paths) ... else: backup_path = run_backup()` block).

Replace it with:

```python
    backup_path: str | None = None
    try:
        if scoped:
            from backend.maintenance.backup import (
                verify_or_backup,
            )

            result = verify_or_backup(tables)
            if result["mode"] == "verified":
                backup_path = result["snapshot"]
                _logger.info(
                    "[maint] Verified today's snapshot "
                    "covers %d scoped table(s) — skipping "
                    "per-table backup",
                    len(tables),
                )
            else:
                paths = result["paths"]
                backup_path = "; ".join(paths) if paths else "<none>"
                _logger.info(
                    "[maint] Snapshot stale/missing — "
                    "per-table backup of %d table(s)",
                    len(tables),
                )
        else:
            backup_path = run_backup()
            _logger.info(
                "[maint] Backup complete: %s",
                backup_path,
            )
        done += 1
        try:
            repo.update_scheduler_run(
                run_id,
                {"tickers_done": done},
            )
        except Exception:
            pass
    except Exception as exc:
        _logger.error(
            "[maint] Backup failed — aborting "
            "maintenance to preserve recoverability",
            exc_info=True,
        )
        errors.append(f"backup: {str(exc)[:200]}")
        # …existing error-finalisation block continues
        # unchanged below this point.
```

Leave the existing imports of `backup_table` and `run_backup` near line 2740 — `verify_or_backup` lives in the same module and the unscoped `run_backup` path still needs it. The local re-import inside the `try` is intentional so a stale import cache during reload doesn't break the new helper (matches the existing local-import style in this function).

- [ ] **Step 4.4: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/jobs/tests/test_iceberg_maintenance_step0.py -v
```

Expected: 2 PASS.

- [ ] **Step 4.5: Restart backend** (changed registered job body)

```bash
docker compose restart backend
sleep 5
```

- [ ] **Step 4.6: Commit**

```bash
git add backend/jobs/executor.py \
        backend/jobs/tests/test_iceberg_maintenance_step0.py
git commit -m "$(cat <<'EOF'
refactor(maint): step-0 uses verify_or_backup for scoped runs

Cuts ~30 redundant rsyncs/day. Scoped pipelines now check
today's manifest first; only fall back to per-table backup
when the snapshot is stale (>24h) or missing the requested
tables. Unscoped admin "Run Maintenance" path unchanged.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: Admin endpoints read manifest

**Files:**
- Modify: `backend/routes.py` (the three `_admin_backups_*` handlers around lines 3720–3954)
- Create: `backend/tests/test_admin_backup_routes.py`

- [ ] **Step 5.1: Write failing route test**

Create `backend/tests/test_admin_backup_routes.py`:

```python
"""HTTP-level tests for /admin/backups endpoints reading
manifest.json."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient

from backend.maintenance.backup_manifest import (
    SCHEMA_VERSION,
    write_manifest,
)


def _seed_full_snapshot(
    backup_root: Path,
    date_str: str,
    *,
    tables: list[tuple[str, float]],
    catalog: bool = True,
):
    root = backup_root / f"backup-{date_str}"
    (root / "warehouse").mkdir(parents=True, exist_ok=True)
    if catalog:
        (root / "catalog.db").write_bytes(b"x" * 1024)
    total = sum(s for _, s in tables)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": date_str,
        "created_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "created_by": "backups_daily",
        "warehouse_size_mb": total,
        "catalog_present": catalog,
        "tables": [
            {
                "id": tid,
                "namespace": tid.split(".")[0],
                "name": tid.split(".")[1],
                "size_mb": s,
                "partition_count": 1,
                "file_count": 1,
                "last_modified_ns": 0,
            }
            for tid, s in tables
        ],
    }
    write_manifest(root, manifest)
    return root


@pytest.mark.asyncio
async def test_admin_health_reports_warehouse_size_from_manifest(
    tmp_path, monkeypatch, superuser_client: AsyncClient,
):
    """Health card SIZE = manifest.warehouse_size_mb,
    NOT latest single per-table dir."""
    monkeypatch.setattr(
        "backend.maintenance.backup.BACKUP_ROOT",
        str(tmp_path),
    )
    today = datetime.now(timezone.utc).date().isoformat()
    _seed_full_snapshot(
        tmp_path,
        today,
        tables=[
            ("stocks.ohlcv", 98.4),
            ("stocks.dividends", 1.2),
            ("algo.events", 9.0),
        ],
    )

    r = await superuser_client.get(
        "/v1/admin/backups/health",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["warehouse_size_mb"] == pytest.approx(
        108.6, abs=0.5,
    )
    assert body["table_count"] == 3


@pytest.mark.asyncio
async def test_admin_list_filters_per_table_dirs(
    tmp_path, monkeypatch, superuser_client: AsyncClient,
):
    """list/ endpoint returns full-snapshot dirs only."""
    monkeypatch.setattr(
        "backend.maintenance.backup.BACKUP_ROOT",
        str(tmp_path),
    )
    today = datetime.now(timezone.utc).date().isoformat()
    _seed_full_snapshot(
        tmp_path, today,
        tables=[("stocks.ohlcv", 50.0)],
    )
    # Legacy per-table cruft that should be hidden
    (tmp_path / f"backup-{today}-stocks-ohlcv").mkdir()

    r = await superuser_client.get(
        "/v1/admin/backups",
    )
    assert r.status_code == 200
    dates = [b["date"] for b in r.json()["backups"]]
    assert dates == [today]


@pytest.mark.asyncio
async def test_admin_contents_reads_manifest_tables(
    tmp_path, monkeypatch, superuser_client: AsyncClient,
):
    """Browse drill-down returns manifest.tables list."""
    monkeypatch.setattr(
        "backend.maintenance.backup.BACKUP_ROOT",
        str(tmp_path),
    )
    today = datetime.now(timezone.utc).date().isoformat()
    _seed_full_snapshot(
        tmp_path, today,
        tables=[
            ("stocks.ohlcv", 98.4),
            ("algo.events", 9.0),
        ],
    )

    r = await superuser_client.get(
        f"/v1/admin/backups/{today}/contents",
    )
    assert r.status_code == 200
    body = r.json()
    ids = sorted(t["name"] for t in body["tables"])
    assert ids == ["algo.events", "stocks.ohlcv"]
```

If a `superuser_client` fixture doesn't exist yet, follow the auth pattern from any existing `backend/tests/test_admin_*` file — typically a fixture that loads storage-state auth cookies. Check `backend/conftest.py` or `backend/tests/conftest.py`.

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_admin_backup_routes.py -v
```

Expected: failures because endpoints don't yet read the manifest (the contents/health responses lack `warehouse_size_mb` / `table_count`; the list endpoint still returns per-table dirs).

- [ ] **Step 5.3: Bust the response cache schema**

Increment the cache keys in `backend/routes.py` so old payloads in Redis don't survive the rollout. Find each occurrence around lines 3725, 3791, and the corresponding `_c.set(...)` lines, and replace:

```python
_bk = "cache:admin:backups-list"
```

with:

```python
_bk = "cache:admin:backups-list:v2"
```

Likewise:

```python
_bk2 = "cache:admin:backups-health"
```

→ `"cache:admin:backups-health:v2"`. There's no per-date contents cache key today; leave that as-is.

- [ ] **Step 5.4: Update `_admin_backups_health`**

Replace the body of `_admin_backups_health` (line 3786 onwards) with:

```python
    async def _admin_backups_health():
        """GET /admin/backups/health."""
        import json
        from datetime import datetime
        from pathlib import Path

        from cache import get_cache

        from backend.maintenance.backup import (
            BACKUP_ROOT,
            list_backups,
        )
        from backend.maintenance.backup_manifest import (
            read_manifest,
        )

        _c2 = get_cache()
        _bk2 = "cache:admin:backups-health:v2"
        if _c2:
            _h2 = _c2.get(_bk2)
            if _h2:
                return json.loads(_h2)

        backups = [
            b for b in list_backups()
            if _is_full_snapshot_dir_name(b["date"])
        ]
        if not backups:
            return {
                "status": "missing",
                "latest_date": None,
                "completed_at": None,
                "age_hours": None,
                "backup_count": 0,
                "table_count": 0,
                "warehouse_size_mb": None,
                "has_catalog": False,
            }

        latest = backups[0]
        bp = Path(latest["path"])
        completed_iso = latest.get("completed_at")
        now_ = __import__("time").time()
        completed_epoch = None
        if completed_iso:
            try:
                completed_epoch = datetime.fromisoformat(
                    completed_iso.replace("Z", "+00:00"),
                ).timestamp()
            except Exception:
                pass
        if completed_epoch is None:
            try:
                completed_epoch = bp.stat().st_mtime
            except Exception:
                completed_epoch = now_
        age_h = (now_ - completed_epoch) / 3600
        if age_h < 24:
            status = "healthy"
        elif age_h < 72:
            status = "stale"
        else:
            status = "critical"

        manifest = read_manifest(bp)
        if manifest is not None:
            warehouse_size_mb = manifest.get(
                "warehouse_size_mb",
            )
            table_count = len(manifest.get("tables", []))
            has_catalog = manifest.get(
                "catalog_present", False,
            )
        else:
            warehouse_size_mb = latest["size_mb"]
            table_count = 0
            has_catalog = (bp / "catalog.db").exists()

        result = {
            "status": status,
            "latest_date": latest["date"],
            "completed_at": completed_iso,
            "age_hours": round(age_h, 1),
            "backup_count": len(backups),
            "table_count": table_count,
            "warehouse_size_mb": warehouse_size_mb,
            "has_catalog": has_catalog,
        }
        if _c2:
            _c2.set(_bk2, json.dumps(result), 120)
        return result
```

Add the helper next to it (above `_admin_backups_list`):

```python
    import re as _re_backup

    _FULL_SNAPSHOT_DATE_RE = _re_backup.compile(
        r"^\d{4}-\d{2}-\d{2}$",
    )

    def _is_full_snapshot_dir_name(date_field: str) -> bool:
        """list_backups returns the suffix after 'backup-'.
        Full snapshots are bare YYYY-MM-DD; per-table dirs
        are YYYY-MM-DD-<ns>-<name>."""
        return bool(
            _FULL_SNAPSHOT_DATE_RE.match(date_field),
        )
```

- [ ] **Step 5.5: Update `_admin_backups_list`**

Filter using the helper. Replace the line `backups = list_backups()` (line 3737) with:

```python
        backups = [
            b for b in list_backups()
            if _is_full_snapshot_dir_name(b["date"])
        ]
```

Also bump the cache key in this handler to `"cache:admin:backups-list:v2"` (already done in Step 5.3 above).

Inside the loop adding `age_hours`, ALSO populate `table_count` and `has_manifest` from the manifest when present:

```python
            from backend.maintenance.backup_manifest import (
                read_manifest,
            )

            manifest = read_manifest(bp)
            if manifest is not None:
                b["table_count"] = len(
                    manifest.get("tables", []),
                )
                b["has_manifest"] = True
            else:
                b["table_count"] = 0
                b["has_manifest"] = False
```

- [ ] **Step 5.6: Update `_admin_backup_contents`**

Replace the body of `_admin_backup_contents` (line 3871) with:

```python
    async def _admin_backup_contents(
        request: Request,
    ):
        """GET /admin/backups/{date}/contents."""
        dt = request.path_params["date"]
        from pathlib import Path

        from backend.maintenance.backup import (
            BACKUP_ROOT,
        )
        from backend.maintenance.backup_manifest import (
            read_manifest,
        )

        bp = Path(BACKUP_ROOT) / f"backup-{dt}"
        if not bp.exists():
            raise HTTPException(
                status_code=404,
                detail=f"No backup for {dt}",
            )

        manifest = read_manifest(bp)
        if manifest is not None:
            tables = [
                {
                    "name": t["id"],
                    "partitions": t.get(
                        "partition_count", 0,
                    ),
                    "files": t.get("file_count", 0),
                    "size_mb": t.get("size_mb", 0.0),
                }
                for t in manifest.get("tables", [])
            ]
            return {
                "date": dt,
                "tables": tables,
                "catalog_present": manifest.get(
                    "catalog_present", False,
                ),
            }

        # Legacy fallback: walk filesystem.
        warehouse = bp / "warehouse"
        if not warehouse.exists():
            warehouse = bp
        tables: list[dict] = []
        stocks_dir = warehouse / "stocks"
        if stocks_dir.exists():
            import subprocess as _sp

            for tbl_dir in sorted(stocks_dir.iterdir()):
                if not tbl_dir.is_dir():
                    continue
                data_dir = tbl_dir / "data"
                parts = 0
                files = 0
                size_mb = 0.0
                if data_dir.exists():
                    parts = sum(
                        1
                        for d in data_dir.iterdir()
                        if d.is_dir()
                    )
                    try:
                        r = _sp.run(
                            ["du", "-sk", str(data_dir)],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if r.returncode == 0:
                            kb = int(r.stdout.split()[0])
                            size_mb = kb / 1024.0
                    except Exception:
                        pass
                    files = parts or 1
                tables.append({
                    "name": f"stocks.{tbl_dir.name}",
                    "partitions": parts,
                    "files": files,
                    "size_mb": round(size_mb, 1),
                })

        return {
            "date": dt,
            "tables": tables,
            "catalog_present": (
                bp / "catalog.db"
            ).exists(),
        }
```

- [ ] **Step 5.7: Run tests to verify they pass**

```bash
docker compose restart backend && sleep 5
docker compose exec backend python -m pytest \
  backend/tests/test_admin_backup_routes.py -v
```

Expected: 3 PASS.

- [ ] **Step 5.8: Flush Redis** (cache touched per CLAUDE.md §4.5 #34)

```bash
docker compose exec redis redis-cli FLUSHALL
```

- [ ] **Step 5.9: Commit**

```bash
git add backend/routes.py backend/tests/test_admin_backup_routes.py
git commit -m "$(cat <<'EOF'
fix(admin): backup endpoints read manifest.json

Health card now shows manifest.warehouse_size_mb (was reading
latest single per-table dir = 0 MB). list/ filters out
per-table fallback dirs. contents/ returns manifest.tables
when present; falls back to filesystem walk for legacy
backups. Cache keys bumped to v2.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: Frontend BackupHealthPanel

**Files:**
- Modify: `frontend/components/admin/BackupHealthPanel.tsx`
- Create: `frontend/components/admin/__tests__/BackupHealthPanel.test.tsx`

- [ ] **Step 6.1: Write failing Vitest snapshot**

Create `frontend/components/admin/__tests__/BackupHealthPanel.test.tsx`:

```typescript
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import BackupHealthPanel from "../BackupHealthPanel";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/apiFetch";

describe("BackupHealthPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders warehouse_size_mb on SIZE tile", async () => {
    (apiFetch as ReturnType<typeof vi.fn>).mockImplementation(
      (url: string) => {
        if (url.endsWith("/admin/backups/health")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                status: "healthy",
                latest_date: "2026-05-23",
                completed_at: "2026-05-23T00:30:00Z",
                age_hours: 5.2,
                backup_count: 2,
                table_count: 27,
                warehouse_size_mb: 2347.6,
                has_catalog: true,
              }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ backups: [] }),
        });
      },
    );

    render(<BackupHealthPanel />);

    // SIZE tile should display ~2.3 GB (auto-converts from MB)
    await waitFor(() => {
      expect(screen.getByText(/2\.3 GB/)).toBeInTheDocument();
    });
    // TABLES tile new in this PR
    expect(screen.getByText("27")).toBeInTheDocument();
    expect(screen.getByText(/Tables/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 6.2: Run test to verify it fails**

```bash
cd frontend && npx vitest run \
  components/admin/__tests__/BackupHealthPanel.test.tsx
```

Expected: FAIL — the panel doesn't render a TABLES tile yet; SIZE may show "0 MB" because the type doesn't have `warehouse_size_mb`.

- [ ] **Step 6.3: Update the `BackupHealth` TypeScript type**

Edit `frontend/components/admin/BackupHealthPanel.tsx` lines 30–38:

```typescript
interface BackupHealth {
  status: "healthy" | "stale" | "critical" | "missing";
  latest_date: string | null;
  completed_at?: string | null;
  age_hours: number | null;
  backup_count: number;
  has_catalog: boolean;
  // size_mb retained for back-compat; warehouse_size_mb is the
  // new aggregate-from-manifest field the SIZE tile prefers.
  size_mb?: number;
  warehouse_size_mb?: number;
  table_count?: number;
}
```

- [ ] **Step 6.4: Change SIZE tile to prefer `warehouse_size_mb`**

Around lines 304–316, replace the SIZE tile's value expression:

```typescript
              {health.size_mb != null
                ? health.size_mb >= 1024
                  ? `${(health.size_mb / 1024).toFixed(1)} GB`
                  : `${health.size_mb.toFixed(0)} MB`
                : "—"}
```

with:

```typescript
              {(() => {
                const v =
                  health.warehouse_size_mb ?? health.size_mb;
                if (v == null) return "—";
                return v >= 1024
                  ? `${(v / 1024).toFixed(1)} GB`
                  : `${v.toFixed(0)} MB`;
              })()}
```

- [ ] **Step 6.5: Add the TABLES tile**

Bump the grid (line 245) from `sm:grid-cols-4` to `sm:grid-cols-2 lg:grid-cols-5`:

```typescript
        <div
          className="grid grid-cols-2
            sm:grid-cols-3 lg:grid-cols-5 gap-3"
        >
```

After the Catalog tile (line 349, immediately before its closing `</div>`-closing-the-grid), add:

```typescript
          <div
            className="rounded-lg border
              border-gray-200 dark:border-gray-700
              p-3"
          >
            <p
              className="text-[10px] uppercase
                tracking-wider text-gray-400"
            >
              Tables
            </p>
            <p
              className="text-sm font-semibold
                text-gray-800 dark:text-gray-100
                mt-0.5"
            >
              {health.table_count ?? "—"}
            </p>
          </div>
```

- [ ] **Step 6.6: Run test to verify it passes**

```bash
cd frontend && npx vitest run \
  components/admin/__tests__/BackupHealthPanel.test.tsx
```

Expected: PASS.

- [ ] **Step 6.7: Lint**

```bash
cd frontend && npx eslint \
  components/admin/BackupHealthPanel.tsx \
  components/admin/__tests__/BackupHealthPanel.test.tsx \
  --fix
```

- [ ] **Step 6.8: Commit**

```bash
git add frontend/components/admin/BackupHealthPanel.tsx \
        frontend/components/admin/__tests__/BackupHealthPanel.test.tsx
git commit -m "$(cat <<'EOF'
fix(admin-ui): Backup Health SIZE tile + new Tables tile

SIZE now reads warehouse_size_mb (manifest aggregate) with
fallback to legacy size_mb. New TABLES tile counts tables in
the latest snapshot. Grid widens to 5 columns on lg+.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Cleanup migration script

**Files:**
- Create: `scripts/cleanup_per_table_backups.py`

**Important:** Only run this AFTER the new `backups_daily` job has produced its first successful snapshot. The script preserves today's per-table dirs as a defensive measure.

- [ ] **Step 7.1: Write the script**

Create `scripts/cleanup_per_table_backups.py`:

```python
"""One-shot cleanup: remove legacy per-table backup
directories created by the pre-ASETPLTFRM-backup-redesign
scoped-maintenance loop.

Safe to run after the new ``backups_daily`` job has produced
its first successful full-warehouse snapshot. Preserves
today's per-table dirs as belt-and-braces — they're deleted
on tomorrow's run.

Usage::

    python scripts/cleanup_per_table_backups.py
    python scripts/cleanup_per_table_backups.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
from datetime import date
from pathlib import Path

_logger = logging.getLogger(__name__)

BACKUP_ROOT = "/Users/abhay/Documents/projects/ai-agent-ui-backups"

PER_TABLE_PATTERN = re.compile(
    r"^backup-\d{4}-\d{2}-\d{2}-(stocks|algo)-.+$",
)


def main(dry_run: bool = False) -> None:
    root = Path(BACKUP_ROOT)
    if not root.exists():
        _logger.error("Backup root missing: %s", root)
        return

    today_prefix = f"backup-{date.today().isoformat()}-"
    removed_bytes = 0
    removed_count = 0
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if not PER_TABLE_PATTERN.match(d.name):
            continue
        if d.name.startswith(today_prefix):
            _logger.info(
                "preserving today's per-table dir: %s",
                d.name,
            )
            continue
        size = sum(
            f.stat().st_size
            for f in d.rglob("*")
            if f.is_file()
        )
        removed_bytes += size
        removed_count += 1
        action = "would remove" if dry_run else "removing"
        _logger.info(
            "%s %s (%.1f MB)", action, d, size / (1024 * 1024),
        )
        if not dry_run:
            shutil.rmtree(d)

    _logger.info(
        "%s %d directories, %.1f MB",
        "would reclaim" if dry_run else "reclaimed",
        removed_count,
        removed_bytes / (1024 * 1024),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
```

- [ ] **Step 7.2: Dry-run smoke test**

```bash
python scripts/cleanup_per_table_backups.py --dry-run
```

Expected output (something like):

```
... INFO would remove /Users/.../backup-2026-05-19-stocks-daily_factors (311.0 MB)
... INFO would remove /Users/.../backup-2026-05-19-stocks-intraday_features (1100.0 MB)
... INFO ... preserving today's per-table dir: backup-2026-05-23-stocks-universe_snapshot
... INFO would reclaim N directories, M MB
```

(Don't run without `--dry-run` until task 8 has run successfully — at least one full snapshot must exist.)

- [ ] **Step 7.3: Commit**

```bash
git add scripts/cleanup_per_table_backups.py
git commit -m "$(cat <<'EOF'
chore(backup): one-shot cleanup of legacy per-table dirs

Run AFTER the new backups_daily job has produced its first
full snapshot. Preserves today's per-table dirs as a safety
copy; deletes everything older.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: Seed the `backups_daily` scheduler pipeline

**Files:**
- Create: `scripts/seed_backups_daily_pipeline.py`

- [ ] **Step 8.1: Write the seed script**

Create `scripts/seed_backups_daily_pipeline.py`:

```python
"""Seed the Backups Daily pipeline.

One step, one job — ``backups_daily`` — scheduled at 00:30
IST every day. Replaces the per-pipeline backup loop
introduced in ASETPLTFRM-418.

Idempotent: re-running upserts the pipeline + replaces the
steps.

Usage::

    docker compose exec backend python \\
        scripts/seed_backups_daily_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from db.engine import get_session_factory
from sqlalchemy import text

_logger = logging.getLogger(__name__)

_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")
PIPELINE_NAME = "Backups Daily"
PIPELINE_ID = str(uuid.uuid5(_NS, PIPELINE_NAME))

STEPS = [
    {
        "step_order": 1,
        "job_type": "backups_daily",
        "job_name": "Full Iceberg warehouse snapshot",
        "payload": {},
    },
]


async def seed() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO pipelines "
                "(pipeline_id, name, scope, cron_days, "
                " cron_time, cron_dates, enabled) "
                "VALUES (:pid, :name, 'india', "
                "        'mon,tue,wed,thu,fri,sat,sun',"
                "        '00:30', NULL, TRUE) "
                "ON CONFLICT (name) DO UPDATE SET "
                "  scope = EXCLUDED.scope, "
                "  cron_days = EXCLUDED.cron_days, "
                "  cron_time = EXCLUDED.cron_time, "
                "  cron_dates = EXCLUDED.cron_dates, "
                "  enabled = EXCLUDED.enabled, "
                "  updated_at = NOW()"
            ),
            {"pid": PIPELINE_ID, "name": PIPELINE_NAME},
        )
        await session.execute(
            text(
                "DELETE FROM pipeline_steps "
                "WHERE pipeline_id = :pid"
            ),
            {"pid": PIPELINE_ID},
        )
        for step in STEPS:
            await session.execute(
                text(
                    "INSERT INTO pipeline_steps "
                    "(pipeline_id, step_order, "
                    " job_type, job_name, payload) "
                    "VALUES (:pid, :order, :jt, :name,"
                    "        CAST(:payload AS jsonb))"
                ),
                {
                    "pid": PIPELINE_ID,
                    "order": step["step_order"],
                    "jt": step["job_type"],
                    "name": step["job_name"],
                    "payload": json.dumps(
                        step.get("payload") or {},
                    ),
                },
            )
        await session.commit()
    _logger.info(
        "%s seeded — cron 00:30 IST daily",
        PIPELINE_NAME,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(seed())
```

- [ ] **Step 8.2: Run the seed**

```bash
docker compose exec backend python \
    scripts/seed_backups_daily_pipeline.py
```

Expected: `Backups Daily seeded — cron 00:30 IST daily`.

- [ ] **Step 8.3: Verify via PG**

```bash
docker compose exec postgres psql -U postgres \
  -d ai_agent -c \
  "SELECT name, cron_time, enabled FROM pipelines WHERE name='Backups Daily';"
```

Expected:

```
     name      | cron_time | enabled
---------------+-----------+---------
 Backups Daily | 00:30     | t
```

- [ ] **Step 8.4: Trigger one immediate run via admin** (or wait for 00:30 IST)

In the Admin UI, navigate to Scheduler → find "Backups Daily" → Run Now. Verify in logs:

```bash
docker compose logs backend --tail 200 | grep backups_daily
```

Expected: `[backups_daily] manifest written: N tables, warehouse XXXX MB`.

- [ ] **Step 8.5: Verify the manifest landed on disk**

```bash
ls -lh ~/Documents/projects/ai-agent-ui-backups/backup-$(date +%F)/
cat ~/Documents/projects/ai-agent-ui-backups/backup-$(date +%F)/manifest.json | head -30
```

Expected: `manifest.json` present; `tables` list populated; `created_by: "backups_daily"`.

- [ ] **Step 8.6: Now safe to actually clean up legacy dirs**

```bash
python scripts/cleanup_per_table_backups.py
```

Expected: per-table dirs from prior days deleted, today's preserved. ~5-6 GB reclaimed.

- [ ] **Step 8.7: Commit**

```bash
git add scripts/seed_backups_daily_pipeline.py
git commit -m "$(cat <<'EOF'
feat(scheduler): seed Backups Daily pipeline at 00:30 IST

One-step pipeline running backups_daily every day. Schedule
chosen so the snapshot completes before any data pipeline
(earliest is 03:00 IST) is allowed to depend on it.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 9: PROGRESS.md + final cleanup

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 9.1: Add a dated session entry**

Open `PROGRESS.md` and add at the TOP (after any existing 2026-05-23 entry):

```markdown
### 2026-05-23 — Backup redesign (manifest-driven daily snapshot)

Replaced ASETPLTFRM-418's per-pipeline per-table backup loop
with one nightly `backups_daily` job at 00:30 IST that writes
`backup-YYYY-MM-DD/{warehouse,catalog.db,manifest.json}`.
Pipelines call a new `verify_or_backup()` helper — if today's
manifest is fresh (<24h) and covers their scoped tables they
skip the backup; otherwise they fall back to the old per-table
loop (safety rail preserved).

Admin Backup Health card now reads `warehouse_size_mb` and
`table_count` from the manifest — the SIZE tile was previously
stuck at 0 MB because the query read whichever single per-table
dir sorted first. New TABLES tile counts tables in the latest
snapshot. Browse drill-down reads `manifest.tables[]`.

Disk reclaimed: ~5–6 GB. CPU saved: ~5 minutes/day of redundant
rsync (~30 per-table calls → 1 full snapshot + verify checks).

The manifest format is the contract for the cloud (S3)
migration in the next two weeks: same fields, different storage
backend.

Shipped slices:

- PR 1: manifest writer / reader module (`backup_manifest.py`)
- PR 2: `backups_daily` scheduler job
- PR 3: `verify_or_backup` helper
- PR 4: pipeline step-0 refactor
- PR 5: admin endpoints + cleanup migration script
- PR 6: BackupHealthPanel SIZE + TABLES tiles
- PR 7: seed `Backups Daily` pipeline

Spec: `docs/superpowers/specs/2026-05-23-backup-redesign-design.md`
Plan: `docs/superpowers/plans/2026-05-23-backup-redesign.md`
```

- [ ] **Step 9.2: Stage `.serena/` per CLAUDE.md §4.4 #25**

```bash
git add .serena/ 2>/dev/null
git status --short
```

- [ ] **Step 9.3: Commit**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
docs: PROGRESS.md backup redesign session entry

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

- [ ] **Step 9.4: Push branch + open PR**

```bash
git push -u origin feature/backup-redesign
gh pr create \
  --base dev \
  --title "Backup redesign: manifest-driven daily snapshot" \
  --body "$(cat <<'EOF'
## Summary

- Replaces ASETPLTFRM-418's per-pipeline per-table backup loop with one nightly `backups_daily` job at 00:30 IST
- Manifest.json becomes the single source of truth for the Admin Backup Health card (fixes the stuck 0 MB SIZE tile) and the pipeline step-0 freshness check
- Reclaims ~5-6 GB disk; cuts ~30 redundant rsyncs/day to 1; preserves fail-closed safety via `verify_or_backup` fallback
- Manifest format is the cloud-migration contract for the upcoming S3 cut-over

Spec: `docs/superpowers/specs/2026-05-23-backup-redesign-design.md`
Plan: `docs/superpowers/plans/2026-05-23-backup-redesign.md`

## Test plan

- [ ] `docker compose exec backend python -m pytest backend/maintenance/tests/test_backup_manifest.py backend/maintenance/tests/test_verify_or_backup.py backend/jobs/tests/test_backups_daily.py backend/jobs/tests/test_iceberg_maintenance_step0.py backend/tests/test_admin_backup_routes.py -v` → all green
- [ ] `cd frontend && npx vitest run components/admin/__tests__/BackupHealthPanel.test.tsx` → green
- [ ] `docker compose exec backend python scripts/seed_backups_daily_pipeline.py` → pipeline seeded
- [ ] Trigger `Backups Daily` from admin Scheduler → manifest written, logs show table count + size
- [ ] Admin UI Backup Health card shows correct GB / table count
- [ ] `python scripts/cleanup_per_table_backups.py --dry-run` reports expected dirs; actual run reclaims ~5-6 GB

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- All 7 components from the spec are covered by tasks 1–8. Component 7 (cleanup script) is its own task 7, paired in PR 5 per the spec by being part of the same merge train.
- Manifest field names are consistent across backend (`warehouse_size_mb`, `table_count`, `tables[].id`, `tables[].size_mb`, `tables[].partition_count`, `tables[].file_count`) and frontend (`warehouse_size_mb`, `table_count`).
- The frontend `apiFetch` mock pattern matches the existing codebase convention.
- `verify_or_backup` signature matches its callsite in the refactored `iceberg_maintenance` body.
- `MAX_BACKUPS=2` retention is preserved by inheriting `run_backup()` rotation — no changes needed.
- `BACKUP_RSYNC_TIMEOUT_S` env var is preserved (the new job calls `run_backup()` which already honours it).
- No backwards-compat shims needed: pipelines that were calling `backup_table` directly now route through `verify_or_backup` which falls back to `backup_table` automatically.
- No new database migration: pipelines/steps live in existing PG tables; the seed script uses the same INSERT/ON CONFLICT pattern as other seeds.
