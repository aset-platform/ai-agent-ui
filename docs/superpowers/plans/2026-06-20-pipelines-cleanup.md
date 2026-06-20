# Pipelines Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Iceberg maintenance/backup bloat from recurring and cut admin-UI latency — via a size-aware compaction guard, real snapshot expiry, backup dedup + auto-cleanup, and data-health/backups endpoint fixes.

**Architecture:** Backend Python (FastAPI, PyIceberg 0.11.1) maintenance/backup modules + two admin route handlers + two frontend SWR hooks. One-time data reclaims (backups −43 GB, algo.events metadata chain) already done operationally; this plan is the durable code + a final operational compaction run.

**Tech Stack:** Python 3.12, PyIceberg 0.11.1, pytest, Next.js/SWR (vitest), Docker Compose.

## Global Constraints

- Line length 79 (black/isort/flake8). `X | None` not `Optional`. No bare `except` (`except Exception` or specific). `_logger`, never `print`. (CLAUDE.md §4.2)
- Never `rm` Iceberg files; use `cleanup_orphans_v2` / `overwrite` APIs. (§4.3 #20)
- Backup is fail-closed step 0 before destructive maintenance (§6.4) — only *dedupe* backups; never remove the precondition.
- Redis cache: constants `TTL_VOLATILE=60`, `TTL_STABLE=300` (`backend/cache.py`); `get_cache()`; `cache.set(key, json, ttl=...)`; key schema `cache:<area>:<endpoint>:<scope>`. No-Redis returns `_NoOpCache`. (§5.13)
- Tests run in-container: `docker compose exec -T backend python -m pytest <path> -v`. Frontend: `cd frontend && npx vitest run <path>`.
- Commit messages end with `Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>`. No push until the end. Branch: `feature/pipelines-cleanup` (worktree, already created off `dev`).
- Size-aware compaction threshold: `_SMALL_TABLE_COMPACT_BYTES = 512 * 1024 * 1024`.

---

## File Structure

- `backend/maintenance/iceberg_maintenance.py` — size-aware guard + byte helper (Task 1); real `expire_snapshots` (Task 2).
- `backend/algo/jobs/intraday_bars_retention.py`, `backend/algo/jobs/algo_events_retention.py` — route through `verify_or_backup` (Task 3).
- `backend/maintenance/backup.py` — `backup_table` same-day dedup guard (Task 4); `list_backups` full-only filter (Task 6).
- `backend/jobs/executor.py` — `execute_backups_daily` auto-cleanup (Task 5).
- `backend/routes.py` — data-health (Task 7), pipeline-assertions cache (Task 8).
- `frontend/hooks/useAdminData.ts`, `frontend/hooks/usePipelineAssertions.ts` — SWR intervals (Task 9).
- Tests under `tests/backend/`; frontend tests under `frontend/hooks/__tests__/` (follow existing layout).

---

## Task 1: Size-aware compaction guard (Thread A2)

**Files:**
- Modify: `backend/maintenance/iceberg_maintenance.py` (add `_SMALL_TABLE_COMPACT_BYTES` + a `_table_data_bytes()` helper near `_avg_files_per_partition:795`; amend the `avg > _MAX_AVG_FILES_PER_PARTITION` skip in `compact_table:442`).
- Test: `tests/backend/test_compaction_size_aware.py` (new).

**Interfaces:**
- Produces: `_table_data_bytes(table_dir: Path) -> int` (sum of `*.parquet` sizes under the dir).
- Produces: constant `_SMALL_TABLE_COMPACT_BYTES = 512 * 1024 * 1024`.
- `compact_table(table_name)` no longer returns `skipped_deep_manifest` when total data bytes ≤ 512 MB.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_compaction_size_aware.py`:
```python
from unittest.mock import patch

import backend.maintenance.iceberg_maintenance as im


def test_small_table_compacts_despite_high_avg(monkeypatch):
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
    # Fail the read path AFTER the guard so we only assert the
    # guard let us through (not a full compaction).
    with patch.object(
        im, "_require_repo", side_effect=RuntimeError("past-guard")
    ):
        res = im.compact_table("algo.events")
    assert "skipped_deep_manifest" not in res
    assert res.get("error") == "read failed"


def test_large_table_still_skips_deep_manifest(monkeypatch):
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
```
(If `_require_repo` is imported inside `compact_table` via `from tools._stock_shared import _require_repo`, patch it at that source: `patch("tools._stock_shared._require_repo", ...)`. Verify the import style before running.)

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_compaction_size_aware.py -v`
Expected: FAIL — `_table_data_bytes` does not exist / small-table test currently skips.

- [ ] **Step 3: Add the byte helper + constant**

In `iceberg_maintenance.py`, after `_MAX_AVG_FILES_PER_PARTITION = 50` (line 792) add:
```python
# A table this small (total parquet bytes) is always safe to
# compact in-process regardless of files/partition — reading it
# into Arrow can't OOM. Lets algo.events (~70 MB) and
# nse_delivery self-compact despite a high avg files/partition,
# while genuinely large fragmented tables still defer.
_SMALL_TABLE_COMPACT_BYTES = 512 * 1024 * 1024
```
After `_avg_files_per_partition` (ends ~line 815) add:
```python
def _table_data_bytes(table_dir: Path) -> int:
    """Total bytes of all ``*.parquet`` under the table dir."""
    if not table_dir.exists():
        return 0
    return sum(
        p.stat().st_size for p in table_dir.rglob("*.parquet")
    )
```

- [ ] **Step 4: Make the deep-manifest skip size-aware**

In `compact_table`, replace the guard block at lines 441-462 with:
```python
    files, partitions, avg = _avg_files_per_partition(table_dir)
    if partitions > 0 and avg > _MAX_AVG_FILES_PER_PARTITION:
        total_bytes = _table_data_bytes(table_dir)
        if total_bytes > _SMALL_TABLE_COMPACT_BYTES:
            _logger.warning(
                "[maint] %s has avg %.1f files/partition "
                "(> %d safe limit, %d files across %d partitions, "
                "%.0f MB) — manifest chain too deep; skipping "
                "compaction to avoid freezing uvicorn. Run "
                "retention first to reduce commit count.",
                table_name,
                avg,
                _MAX_AVG_FILES_PER_PARTITION,
                files,
                partitions,
                total_bytes / (1024 * 1024),
            )
            return {
                "table": table_name,
                "before": before,
                "after": before,
                "skipped_deep_manifest": True,
                "partitions": partitions,
                "avg_files_per_partition": avg,
            }
        _logger.info(
            "[maint] %s avg %.1f files/partition but only "
            "%.0f MB (≤ %.0f MB) — compacting in-process",
            table_name,
            avg,
            total_bytes / (1024 * 1024),
            _SMALL_TABLE_COMPACT_BYTES / (1024 * 1024),
        )
```
(The `_MAX_SAFE_COMPACT_FILES = 40_000` absolute ceiling above this block is unchanged — hard OOM backstop.)

- [ ] **Step 5: Run to verify pass**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_compaction_size_aware.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Lint + commit**

```bash
black backend/maintenance/iceberg_maintenance.py tests/backend/test_compaction_size_aware.py
flake8 backend/maintenance/iceberg_maintenance.py
git add backend/maintenance/iceberg_maintenance.py tests/backend/test_compaction_size_aware.py
git commit -m "feat(maint): size-aware compaction guard (compact small tables despite high avg files/partition)"
```

---

## Task 2: Real snapshot expiry — fix the `expire_snapshots` no-op (Thread A1)

**Files:**
- Modify: `backend/maintenance/iceberg_maintenance.py:281-348` (`expire_snapshots`).
- Test: `tests/backend/test_expire_snapshots_real.py` (new).

**Context:** `execute_iceberg_maintenance` (the path pipelines use) already calls `cleanup_orphans_v2` per table — correct. The legacy no-op only affects `run_maintenance()` (line 714) and the post-pipeline tail (`backend/jobs/pipeline_executor.py:138`). Fix: make `expire_snapshots()` delegate to the proven real path so those two callers stop being no-ops.

**Interfaces:**
- `expire_snapshots(table_name, keep=SNAPSHOT_KEEP) -> dict` performs real expiry via `cleanup_orphans_v2(table_name, retain_snapshots=keep, skip_backup=True)`; returns `{"table","expired","verified"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_expire_snapshots_real.py`:
```python
import backend.maintenance.iceberg_maintenance as im


def test_expire_snapshots_delegates_to_cleanup(monkeypatch):
    calls = {}

    def fake_cleanup(table_name, **kw):
        calls["table"] = table_name
        calls["kw"] = kw
        return {"expired_snapshots": 7, "verified": True}

    monkeypatch.setattr(im, "cleanup_orphans_v2", fake_cleanup)
    res = im.expire_snapshots("algo.events", keep=5)
    assert calls["table"] == "algo.events"
    assert calls["kw"]["retain_snapshots"] == 5
    assert calls["kw"]["skip_backup"] is True
    assert res["expired"] == 7
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_expire_snapshots_real.py -v`
Expected: FAIL — no-op never calls `cleanup_orphans_v2` (`calls` empty → KeyError).

- [ ] **Step 3: Replace the no-op body**

Replace `expire_snapshots` (lines 281-348) with:
```python
def expire_snapshots(
    table_name: str,
    keep: int = SNAPSHOT_KEEP,
) -> dict:
    """Expire old snapshots (keep latest ``keep``) via the real
    PyIceberg path. Delegates to ``cleanup_orphans_v2`` which
    performs native expire + referenced-set orphan sweep.

    Caller contract: a step-0 backup must already have been taken
    upstream (``skip_backup=True`` here). ``run_maintenance`` and
    the post-pipeline expiry tail satisfy this via the daily
    snapshot + Iceberg snapshot retention.
    """
    res = cleanup_orphans_v2(
        table_name,
        retain_snapshots=keep,
        skip_backup=True,
    )
    return {
        "table": table_name,
        "expired": int(res.get("expired_snapshots", 0)),
        "verified": res.get("verified", True),
    }
```
(`cleanup_orphans_v2` is defined later in the same module; the forward reference resolves at call time.)

- [ ] **Step 4: Run to verify pass**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_expire_snapshots_real.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
black backend/maintenance/iceberg_maintenance.py tests/backend/test_expire_snapshots_real.py
flake8 backend/maintenance/iceberg_maintenance.py
git add backend/maintenance/iceberg_maintenance.py tests/backend/test_expire_snapshots_real.py
git commit -m "fix(maint): expire_snapshots performs real expiry via cleanup_orphans_v2 (was no-op)"
```

---

## Task 3: Route retention jobs through `verify_or_backup` (Thread B1)

**Files:**
- Modify: `backend/algo/jobs/intraday_bars_retention.py:~189-201`, `backend/algo/jobs/algo_events_retention.py:~178-194` (+ their imports).
- Test: `tests/backend/test_retention_backup_dedup.py` (new).

**Interfaces:**
- Consumes: `verify_or_backup(tables: list[str]) -> dict` (`{"mode","snapshot": str|None,"paths": list[str]}`).
- Retention jobs call `verify_or_backup([TABLE])` instead of `backup_table(TABLE)`; `backup_path` derives from `snapshot` or `paths[0]`.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_retention_backup_dedup.py`. Use whichever stubbing seam exists so the test is behavioral, not tautological — patch `verify_or_backup` to raise a sentinel and assert it is reached (proving the job calls it, not `backup_table`):
```python
import pytest
import backend.algo.jobs.algo_events_retention as aer


def test_algo_events_retention_routes_through_verify(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        aer, "verify_or_backup",
        lambda tables: seen.setdefault("t", tables)
        or (_ for _ in ()).throw(RuntimeError("reached-vob")),
    )
    monkeypatch.setattr(
        aer, "backup_table",
        lambda *a, **k: pytest.fail("backup_table called directly"),
    )
    with pytest.raises(Exception):
        aer.run_algo_events_retention_job({"dry_run": False})
    assert seen["t"] == [aer.ALGO_EVENTS_TABLE]
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_retention_backup_dedup.py -v`
Expected: FAIL — job calls `backup_table`, so the `pytest.fail` fires.

- [ ] **Step 3: Edit `algo_events_retention.py`**

Replace the backup block (lines 178-194):
```python
    backup_path: str | None = None
    if not skip_backup:
        try:
            _vob = verify_or_backup([ALGO_EVENTS_TABLE])
            backup_path = _vob.get("snapshot") or (
                _vob["paths"][0] if _vob.get("paths") else None
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "algo-events-retention: pre-delete backup "
                "failed for %s — aborting: %s",
                ALGO_EVENTS_TABLE,
                exc,
                exc_info=True,
            )
            return {
                "status": "error",
                "today": today.isoformat(),
                "error": f"backup_failed: {exc!s}"[:200],
            }
```
Update the import: `from backend.maintenance.backup import verify_or_backup` (keep `backup_table` import only if still referenced; otherwise remove).

- [ ] **Step 4: Edit `intraday_bars_retention.py`**

Replace the backup block (lines 189-201):
```python
    skip_backup = bool(payload.get("skip_backup"))
    backup_path: str | None = None
    if not skip_backup:
        try:
            _vob = verify_or_backup([INTRADAY_BARS_TABLE])
            backup_path = _vob.get("snapshot") or (
                _vob["paths"][0] if _vob.get("paths") else None
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "intraday-retention: pre-delete backup failed "
                "for %s — aborting delete: %s",
                INTRADAY_BARS_TABLE,
                exc,
                exc_info=True,
            )
            return {
                "status": "error",
                "cutoff": cutoff_iso,
                "today": today.isoformat(),
            }
```
Update its import to `verify_or_backup`.

- [ ] **Step 5: Run to verify pass + existing retention tests**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_retention_backup_dedup.py backend/algo/jobs/tests/test_algo_events_retention.py backend/algo/jobs/tests/test_intraday_bars_retention.py -v`
Expected: PASS. If existing tests patched `backup_table`, repoint them to `verify_or_backup`.

- [ ] **Step 6: Lint + commit**

```bash
black backend/algo/jobs/intraday_bars_retention.py backend/algo/jobs/algo_events_retention.py tests/backend/test_retention_backup_dedup.py
flake8 backend/algo/jobs/intraday_bars_retention.py backend/algo/jobs/algo_events_retention.py
git add backend/algo/jobs/ tests/backend/test_retention_backup_dedup.py
git commit -m "fix(backup): retention jobs dedup via verify_or_backup (skip per-table backup when daily snapshot fresh)"
```

---

## Task 4: `backup_table` same-day dedup guard (Thread B3)

**Files:**
- Modify: `backend/maintenance/backup.py:121-122` (`backup_table`).
- Test: `tests/backend/test_backup_table_dedup.py` (new).

**Interfaces:** `backup_table(table_id, ...)` returns the existing dest path without rsync when the dest dir already exists and is non-empty.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_backup_table_dedup.py`:
```python
from datetime import date
from unittest.mock import patch

from backend.maintenance import backup as bk


def test_backup_table_skips_when_dest_nonempty(tmp_path):
    src = tmp_path / "wh" / "algo" / "events"
    src.mkdir(parents=True)
    (src / "x.parquet").write_text("data")
    root = tmp_path / "backups"
    today = date.today().isoformat()
    dest = root / f"backup-{today}-algo-events"
    dest.mkdir(parents=True)
    (dest / "existing").write_text("prior")
    with patch.object(bk, "subprocess") as sp:
        out = bk.backup_table(
            "algo.events",
            warehouse=str(tmp_path / "wh"),
            backup_root=str(root),
        )
    assert out == str(dest)
    sp.run.assert_not_called()  # no rsync
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_backup_table_dedup.py -v`
Expected: FAIL — `subprocess.run` IS called.

- [ ] **Step 3: Add the guard**

In `backup_table`, between `dest = ...` (line 121) and `dest.mkdir(...)` (line 122):
```python
    dest = root / f"backup-{today}-{ns}-{name}"
    if dest.exists() and any(dest.iterdir()):
        _logger.info(
            "Per-table backup for %s already exists today "
            "(%s) — skipping re-rsync", table_id, dest,
        )
        return str(dest)
    dest.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_backup_table_dedup.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
black backend/maintenance/backup.py tests/backend/test_backup_table_dedup.py
flake8 backend/maintenance/backup.py
git add backend/maintenance/backup.py tests/backend/test_backup_table_dedup.py
git commit -m "fix(backup): backup_table skips re-rsync when today's per-table dir already populated"
```

---

## Task 5: Auto-cleanup per-table backups in daily pipeline (Thread B2)

**Files:**
- Modify: `backend/jobs/executor.py:~2742` (`execute_backups_daily`, after `write_manifest`).
- Test: `tests/backend/test_backups_daily_cleanup.py` (new).

**Interfaces:** Consumes `scripts.cleanup_per_table_backups.main(dry_run: bool = False)`.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_backups_daily_cleanup.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_backups_daily_cleanup.py -v`
Expected: FAIL — cleanup never called.

- [ ] **Step 3: Add the cleanup call**

In `execute_backups_daily`, after the `write_manifest(...)` + its log (after line 2742), before `if repo is not None:`:
```python
    # Prune accumulated per-table fallback backup dirs so the
    # backups folder can't re-bloat (the 463-dir / 47 GB incident,
    # 2026-06-20). Non-fatal: backup success must not depend on it.
    try:
        from scripts.cleanup_per_table_backups import (
            main as cleanup_per_table_backups,
        )

        cleanup_per_table_backups(dry_run=False)
    except Exception:
        _logger.error(
            "[backups_daily] per-table cleanup failed",
            exc_info=True,
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_backups_daily_cleanup.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
black backend/jobs/executor.py tests/backend/test_backups_daily_cleanup.py
flake8 backend/jobs/executor.py
git add backend/jobs/executor.py tests/backend/test_backups_daily_cleanup.py
git commit -m "feat(backup): daily pipeline auto-prunes per-table backup dirs after manifest"
```

---

## Task 6: `list_backups` full-only filter before `du` (Thread C)

**Files:**
- Modify: `backend/maintenance/backup.py:308-361` (`list_backups`), `backend/routes.py:144-148` (caller).
- Test: `tests/backend/test_list_backups_filter.py` (new).

**Interfaces:** `list_backups(backup_root=None, *, full_only: bool = False) -> list[dict]`. When `full_only`, only `_FULL_SNAPSHOT_NAME_RE` dirs are sized/returned.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_list_backups_filter.py`:
```python
from unittest.mock import patch

from backend.maintenance import backup as bk


def test_list_backups_full_only_sizes_only_snapshots(tmp_path):
    root = tmp_path
    (root / "backup-2026-06-20").mkdir()
    (root / "backup-2026-06-19").mkdir()
    for i in range(5):
        (root / f"backup-2026-06-19-stocks-t{i}").mkdir()
    with patch.object(bk, "_dir_size_mb", return_value=1.0) as sz:
        res = bk.list_backups(str(root), full_only=True)
    assert {b["date"] for b in res} == {"2026-06-20", "2026-06-19"}
    assert sz.call_count == 2  # not 7
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_list_backups_filter.py -v`
Expected: FAIL — unexpected `full_only` kwarg / `_dir_size_mb` called 7×.

- [ ] **Step 3: Add `full_only` to `list_backups`**

Change the signature (line 308):
```python
def list_backups(
    backup_root: str | None = None,
    *,
    full_only: bool = False,
) -> list[dict]:
```
In the loop (lines 332-335), filter BEFORE `_dir_size_mb`:
```python
    for d in sorted(root.iterdir(), reverse=True):
        if not (d.is_dir() and d.name.startswith("backup-")):
            continue
        if full_only and not _FULL_SNAPSHOT_NAME_RE.match(d.name):
            continue
        dt = d.name.replace("backup-", "")
        # ... existing completed_at + append block unchanged
```

- [ ] **Step 4: Update the route caller**

In `backend/routes.py:144-148`:
```python
    backups = await asyncio.to_thread(
        lambda: list_backups(backup_root, full_only=True)
    )
```
(Remove the now-redundant `_is_full_snapshot_dir_name` comprehension filter.)

- [ ] **Step 5: Run to verify pass**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_list_backups_filter.py -v`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
black backend/maintenance/backup.py backend/routes.py tests/backend/test_list_backups_filter.py
flake8 backend/maintenance/backup.py backend/routes.py
git add backend/maintenance/backup.py backend/routes.py tests/backend/test_list_backups_filter.py
git commit -m "perf(admin): /admin/backups sizes only full snapshots (drop 465 wasted du spawns)"
```

---

## Task 7: Data-health — drop cache-nuke + collapse ohlcv NaN scans (Thread D1+D2)

**Files:**
- Modify: `backend/routes.py:2412-2418` (delete `invalidate_metadata()` block), `backend/routes.py:2498-2529` (`_ohlcv_health` NaN queries).
- Test: `tests/backend/test_data_health_perf.py` (new).

**Interfaces:** `_ohlcv_health` issues 2 iceberg queries (1 NaN GROUP BY + 1 freshness) instead of 3; data-health handler no longer calls no-arg `invalidate_metadata()`.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_data_health_perf.py`:
```python
import inspect

import backend.routes as routes


def test_data_health_source_no_global_invalidate():
    src = inspect.getsource(routes)
    assert "invalidate_metadata()\n" not in src
```
(Source-level regression guard — the handler is a nested closure. If `_ohlcv_health` can be exercised via a `query_iceberg_df` mock seam, add a behavioral test asserting 2 calls; otherwise this guard plus manual verification at Step 5 suffices.)

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_data_health_perf.py -v`
Expected: FAIL — bare `invalidate_metadata()` present at line 2418.

- [ ] **Step 3: Remove the gratuitous cache-nuke**

Delete lines 2412-2418 (the `── Invalidate DuckDB cache ──` comment, the `from db.duckdb_engine import invalidate_metadata` import added solely for it, and the `invalidate_metadata()` call). Targeted per-table invalidations from write paths already keep reads fresh.

- [ ] **Step 4: Collapse the 2 NaN-close scans into 1**

In `_ohlcv_health`, replace lines 2499-2529 (count query + conditional DISTINCT-ticker query) with one `GROUP BY`:
```python
                nan_df = query_iceberg_df(
                    "stocks.ohlcv",
                    "SELECT ticker, count(*) AS cnt "
                    "FROM ohlcv "
                    "WHERE close IS NULL OR isnan(close) "
                    "GROUP BY ticker",
                )
                if not nan_df.empty:
                    o["nan_close_count"] = int(
                        nan_df["cnt"].sum()
                    )
                    o["nan_close_tickers"] = sorted(
                        nan_df["ticker"].tolist()
                    )
```
(The freshness `MAX(date) GROUP BY ticker` query at line 2534 is unchanged.)

- [ ] **Step 5: Run to verify pass + manual smoke**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_data_health_perf.py -v` → PASS.
Manual: after deploy (Task 10), `curl` the admin data-health route (auth) and confirm it returns the same shape (`ohlcv.nan_close_count`, `nan_close_tickers`).

- [ ] **Step 6: Lint + commit**

```bash
black backend/routes.py tests/backend/test_data_health_perf.py
flake8 backend/routes.py
git add backend/routes.py tests/backend/test_data_health_perf.py
git commit -m "perf(admin): data-health drops per-request metadata-cache nuke; ohlcv 3 scans -> 2"
```

---

## Task 8: Cache the pipeline-assertions endpoint (Thread D4)

**Files:**
- Modify: `backend/routes.py:3643-3697` (`_admin_pipeline_assertions`).
- Test: `tests/backend/test_pipeline_assertions_cache.py` (new).

**Interfaces:** endpoint reads/writes Redis with key `cache:admin:pipeline-assertions:{days}:{severity}:{cap}`, TTL `TTL_VOLATILE` (60).

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_pipeline_assertions_cache.py`:
```python
import inspect

import backend.routes as routes


def test_pipeline_assertions_uses_cache():
    src = inspect.getsource(routes)
    assert "cache:admin:pipeline-assertions" in src
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_pipeline_assertions_cache.py -v`
Expected: FAIL — key absent.

- [ ] **Step 3: Add Redis cache around the query**

In `_admin_pipeline_assertions`, after `window_days = ...` (line 3646) add the read; cache the dict before return (line 3697):
```python
        from cache import get_cache, TTL_VOLATILE
        import json as _json
        _cache = get_cache()
        _ck = (
            f"cache:admin:pipeline-assertions:{window_days}:"
            f"{severity or 'all'}:{cap}"
        )
        _hit = _cache.get(_ck)
        if _hit:
            return _json.loads(_hit)
```
Replace the final `return {"rows": rows, "counts": counts}` with:
```python
        _out = {"rows": rows, "counts": counts}
        _cache.set(_ck, _json.dumps(_out), ttl=TTL_VOLATILE)
        return _out
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_pipeline_assertions_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
black backend/routes.py tests/backend/test_pipeline_assertions_cache.py
flake8 backend/routes.py
git add backend/routes.py tests/backend/test_pipeline_assertions_cache.py
git commit -m "perf(admin): cache pipeline-assertions (algo.events) query, TTL 60s"
```

---

## Task 9: Frontend SWR dedup interval (Thread D3)

**Files:**
- Modify: `frontend/hooks/useAdminData.ts:679-692` (`useDataHealth`).
- Test: `frontend/hooks/__tests__/useDataHealth.test.ts` (new, or extend existing).

**Interfaces:** exported `DATA_HEALTH_SWR_OPTS` with `dedupingInterval: 60_000`, `revalidateOnFocus: false`.

- [ ] **Step 1: Write the failing test**

Create `frontend/hooks/__tests__/useDataHealth.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import { DATA_HEALTH_SWR_OPTS } from "../useAdminData";

describe("useDataHealth SWR opts", () => {
  it("dedupes for 60s to match Redis TTL", () => {
    expect(DATA_HEALTH_SWR_OPTS.dedupingInterval).toBe(60_000);
    expect(DATA_HEALTH_SWR_OPTS.revalidateOnFocus).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run hooks/__tests__/useDataHealth.test.ts`
Expected: FAIL — `DATA_HEALTH_SWR_OPTS` not exported.

- [ ] **Step 3: Extract + bump the interval**

In `useAdminData.ts`, above `useDataHealth` add:
```typescript
export const DATA_HEALTH_SWR_OPTS = {
  revalidateOnFocus: false,
  dedupingInterval: 60_000,
} as const;
```
Replace the inline options object (lines 688-691) with `DATA_HEALTH_SWR_OPTS`.

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run hooks/__tests__/useDataHealth.test.ts`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd frontend && npx eslint hooks/useAdminData.ts --fix && cd ..
git add frontend/hooks/useAdminData.ts frontend/hooks/__tests__/useDataHealth.test.ts
git commit -m "perf(admin-ui): data-health SWR dedupingInterval 5s -> 60s (match Redis TTL)"
```

---

## Task 10: Operational compaction of algo.events + nse_delivery (Thread A3)

**Files:** none (operational). Run AFTER Tasks 1-9 are merged on the branch.

- [ ] **Step 1: Deploy + restart**

```bash
docker compose restart backend
docker compose exec -T redis redis-cli FLUSHALL
until curl -sf http://localhost:8181/v1/health >/dev/null 2>&1; do sleep 3; done
```

- [ ] **Step 2: Compact through the now-size-aware path**

```bash
docker compose exec -T backend python -c "
import logging; logging.basicConfig(level=logging.INFO, format='%(message)s')
from backend.maintenance.iceberg_maintenance import compact_table
print(compact_table('algo.events'))
print(compact_table('stocks.nse_delivery'))
" 2>&1 | grep -iE "Compacted|already optimal|skipping|error"
```
Expected: both report `Compacted … N → M files` (M small), NOT `skipping`.

- [ ] **Step 3: Verify file collapse**

```bash
W=/Users/abhay/.ai-agent-ui/data/iceberg/warehouse
for t in algo/events stocks/nse_delivery; do
  echo "$t: $(find $W/$t/data -name '*.parquet'|wc -l) files"; done
```
Expected: `algo/events` low double digits; `nse_delivery` near its partition count.

---

## Task 11: Docs + memory + push

**Files:** `PROGRESS.md`, Serena memory `algo-events-iceberg-bloat-remediation`.

- [ ] **Step 1: Update PROGRESS.md** with a dated 2026-06-20 entry (size-aware guard, expire-snapshots fix, backup dedup/auto-cleanup, backups+data-health UI perf, the −43 GB + algo.events reclaims).

- [ ] **Step 2: Update the Serena memory** `algo-events-iceberg-bloat-remediation` (size-aware compaction guard + the operational chain-collapse; backup-folder auto-cleanup).

- [ ] **Step 3: Run the full affected suite**

```bash
docker compose exec -T backend python -m pytest tests/backend/ -k "compaction_size_aware or expire_snapshots or retention_backup or backup_table_dedup or backups_daily_cleanup or list_backups or data_health or pipeline_assertions" -v
cd frontend && npx vitest run hooks/__tests__/useDataHealth.test.ts
```
Expected: all PASS.

- [ ] **Step 4: Commit + push**

```bash
git add PROGRESS.md .serena/
git commit -m "docs: pipelines cleanup — size-aware compaction, backup dedup, UI perf"
git push -u origin feature/pipelines-cleanup
```

---

## Self-Review Notes

- **Spec coverage:** A2 (Task 1), A1 (Task 2), A3 (Task 10), B1 (Task 3), B3 (Task 4), B2 (Task 5), C (Task 6), D1+D2 (Task 7), D4 (Task 8), D3 (Task 9), docs (Task 11). All threads covered.
- **Refinement vs spec:** A1 narrowed — `execute_iceberg_maintenance` already calls real `cleanup_orphans_v2`; the no-op only affected `run_maintenance` + the post-pipeline tail, so Task 2 fixes `expire_snapshots` itself. D2 is 3→2 queries (freshness scan stays), not 3→1.
- **Type consistency:** `_table_data_bytes(Path)->int`, `_SMALL_TABLE_COMPACT_BYTES`, `verify_or_backup(list)->dict`, `list_backups(..., full_only=)`, `DATA_HEALTH_SWR_OPTS` used identically across tasks.
- **Open confirmations for the implementer:** the `_require_repo` patch target in Task 1 (module-level vs in-function import); the retention delete-seam for the Task 3 behavioral test; whether `useAdminData.ts` hook tests already exist (extend vs create); that `cleanup_orphans_v2` is importable at `expire_snapshots` call time (same module).
- **D3 no-Redis in-process fallback:** spec mentioned it; deferred as YAGNI (SWR dedup + 60 s Redis path cover the common case). Flag to reviewer if wanted.
