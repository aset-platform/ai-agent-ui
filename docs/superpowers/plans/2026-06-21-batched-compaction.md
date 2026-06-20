# Batched per-month Compaction Implementation Plan (ASETPLTFRM-442)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Byte-heavy intraday tables (>1 GB, e.g. `stocks.intraday_features`) compact one month at a time (bounded memory) instead of being skipped by the `_MAX_SAFE_COMPACT_BYTES` ceiling — no OOM.

**Architecture:** New `_compact_table_by_month(table_name)` in `backend/maintenance/iceberg_maintenance.py`: enumerate non-optimal months from `inspect.partitions()` (metadata, no scan), then per non-optimal month read+overwrite only that month's rows scoped on the `year_month` data column. `compact_table` routes >1 GB tables that have a `year_month` column to it; others keep the skip.

**Tech Stack:** Python 3.12, PyIceberg 0.11.1 (`inspect.partitions()`, `overwrite(overwrite_filter=)`; no native compaction), PyArrow, pytest, Docker.

## Global Constraints

- Line ≤79 (black/isort/flake8). `X | None` not `Optional`. No bare `except` (`except Exception`/specific). `_logger`, never `print`. (§4.2)
- Never `rm` Iceberg files; the maintenance loop's `cleanup_orphans_v2` (already runs after `compact_table`) reclaims superseded files. (§4.3 #20)
- `bar_month` (MonthTransform int, months since 1970-01) → `year_month` string: `year = 1970 + m//12`, `month = m%12 + 1`, `f"{year}-{month:02d}"`. VERIFIED: `677 → "2026-06"`, `576 → "2018-01"`.
- A month is **non-optimal** when `sum(file_count) > partition_count` for that `bar_month`.
- `retry_iceberg_op(identifier: str, operation: Callable[[], T]) -> T` from `backend.algo._iceberg_retry`.
- Tests: one-off worktree container `docker run --rm -e HOME=/root -e PYTHONPATH=/app:/app/backend -v /Users/abhay/Documents/projects/ai-agent-ui-batched-compaction:/app -w /app ai-agent-ui-backend:latest python -m pytest <path> -q`. (→ memory `worktree-docker-testing`)
- Branch `feature/batched-compaction` (worktree, off dev). Commits end `Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>`. No push until end. NO whole-file black (hand-edit only; `iceberg_maintenance.py` edits stay minimal).

## File Structure

- `backend/maintenance/iceberg_maintenance.py` — add `_bar_month_to_year_month()` (Task 1), `_compact_table_by_month()` (Task 2), route in `compact_table` (Task 3).
- `tests/backend/test_batched_compaction.py` — new (Tasks 1, 2).
- `tests/backend/test_compaction_byte_ceiling.py` — update for the new routing (Task 3).

---

## Task 1: `_bar_month_to_year_month` helper

**Files:**
- Modify: `backend/maintenance/iceberg_maintenance.py` (add helper near `_table_data_bytes`).
- Test: `tests/backend/test_batched_compaction.py` (new).

**Interfaces:**
- Produces: `_bar_month_to_year_month(bar_month: int) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_batched_compaction.py`:
```python
import backend.maintenance.iceberg_maintenance as im


def test_bar_month_to_year_month():
    f = im._bar_month_to_year_month
    assert f(677) == "2026-06"   # current month (verified vs inspect)
    assert f(576) == "2018-01"
    assert f(675) == "2026-04"
    assert f(0) == "1970-01"
    assert f(11) == "1970-12"
    assert f(12) == "1971-01"
```

- [ ] **Step 2: Run to verify failure**

Run: `docker run --rm -e HOME=/root -e PYTHONPATH=/app:/app/backend -v /Users/abhay/Documents/projects/ai-agent-ui-batched-compaction:/app -w /app ai-agent-ui-backend:latest python -m pytest tests/backend/test_batched_compaction.py -q`
Expected: FAIL — `_bar_month_to_year_month` does not exist.

- [ ] **Step 3: Implement**

In `iceberg_maintenance.py`, after `_table_data_bytes` (ends ~line 805):
```python
def _bar_month_to_year_month(bar_month: int) -> str:
    """MonthTransform value (months since 1970-01) → 'YYYY-MM'."""
    year = 1970 + bar_month // 12
    month = bar_month % 12 + 1
    return f"{year}-{month:02d}"
```

- [ ] **Step 4: Run to verify pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/maintenance/iceberg_maintenance.py tests/backend/test_batched_compaction.py
git commit -m "feat(maint): _bar_month_to_year_month helper for batched compaction"
```

---

## Task 2: `_compact_table_by_month` helper

**Files:**
- Modify: `backend/maintenance/iceberg_maintenance.py` (add helper after `_bar_month_to_year_month`).
- Test: `tests/backend/test_batched_compaction.py`.

**Interfaces:**
- Consumes: `_bar_month_to_year_month` (Task 1), `_count_parquet_files`, `invalidate_metadata`, `WAREHOUSE_DIR`, `retry_iceberg_op`, `_require_repo`.
- Produces: `_compact_table_by_month(table_name: str) -> dict` returning `{"table","before","after","months_rewritten","months_skipped","errors","batched": True}` (or `{"table","error"}` / `{"table","before","after","skipped_too_large_bytes": True}` for the no-`year_month` fallback).

- [ ] **Step 1: Write the failing tests**

Append to `tests/backend/test_batched_compaction.py`:
```python
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pyarrow as pa


def _fake_table(*, partitions, file_counts, has_ym=True):
    tbl = MagicMock()
    names = ["year_month"] if has_ym else ["ticker"]
    fields = []
    for n in names:
        fld = MagicMock()
        fld.name = n
        fields.append(fld)
    sch = MagicMock()
    sch.fields = fields
    sch.as_arrow.return_value = pa.schema([("year_month", pa.string())])
    tbl.schema.return_value = sch
    insp = MagicMock()
    insp.partitions.return_value.to_pydict.return_value = {
        "partition": partitions, "file_count": file_counts,
    }
    tbl.inspect = insp
    tbl.scan.return_value.to_arrow.return_value.cast.return_value = (
        pa.table({"year_month": ["2026-06"]})
    )
    return tbl


def _patches(tbl, tmp_path):
    repo = MagicMock()
    repo.load_table.return_value = tbl
    return [
        patch("tools._stock_shared._require_repo", return_value=repo),
        patch.object(im, "retry_iceberg_op", lambda ident, op: op()),
        patch.object(im, "invalidate_metadata", lambda *a, **k: None),
        patch.object(im, "_count_parquet_files", lambda d: 0),
        patch.object(im, "WAREHOUSE_DIR", tmp_path),
    ]


def test_by_month_rewrites_only_non_optimal(tmp_path):
    # bar_month 677: 2 partitions / 5 files (non-optimal);
    # bar_month 676: 2 partitions / 2 files (optimal).
    tbl = _fake_table(
        partitions=[
            {"ticker_bucket": 0, "bar_month": 677},
            {"ticker_bucket": 1, "bar_month": 677},
            {"ticker_bucket": 0, "bar_month": 676},
            {"ticker_bucket": 1, "bar_month": 676},
        ],
        file_counts=[3, 2, 1, 1],
    )
    with ExitStack() as es:
        for p in _patches(tbl, tmp_path):
            es.enter_context(p)
        res = im._compact_table_by_month("stocks.intraday_features")
    assert res["months_rewritten"] == 1
    assert res["months_skipped"] == 1
    assert res["errors"] == []
    assert tbl.overwrite.call_count == 1
    _, kw = tbl.overwrite.call_args
    flt = kw["overwrite_filter"]
    assert flt.term.name == "year_month"
    assert flt.literal.value == "2026-06"


def test_by_month_isolates_per_month_errors(tmp_path):
    tbl = _fake_table(
        partitions=[
            {"ticker_bucket": 0, "bar_month": 677},
            {"ticker_bucket": 0, "bar_month": 676},
        ],
        file_counts=[5, 5],  # both non-optimal (1 partition / 5 files)
    )
    tbl.overwrite.side_effect = [RuntimeError("boom"), None]
    with ExitStack() as es:
        for p in _patches(tbl, tmp_path):
            es.enter_context(p)
        res = im._compact_table_by_month("stocks.intraday_features")
    assert res["months_rewritten"] == 1
    assert len(res["errors"]) == 1
    assert tbl.overwrite.call_count == 2  # did not abort after error


def test_by_month_no_year_month_falls_back_to_skip(tmp_path):
    tbl = _fake_table(partitions=[], file_counts=[], has_ym=False)
    with ExitStack() as es:
        for p in _patches(tbl, tmp_path):
            es.enter_context(p)
        res = im._compact_table_by_month("stocks.huge_no_ym")
    assert res.get("skipped_too_large_bytes") is True
    assert tbl.overwrite.call_count == 0
```
(Adjust `flt.term.name` / `flt.literal.value` if the installed PyIceberg `EqualTo` exposes different attribute names — verify against `python -c "from pyiceberg.expressions import EqualTo; e=EqualTo('year_month','x'); print(dir(e))"`.)

- [ ] **Step 2: Run to verify failure** — pytest the file. Expected: FAIL (`_compact_table_by_month` missing).

- [ ] **Step 3: Implement**

In `iceberg_maintenance.py`, after `_bar_month_to_year_month`:
```python
def _compact_table_by_month(table_name: str) -> dict:
    """Compact a byte-heavy table one month at a time so the whole
    table never loads into memory. Reads inspect.partitions() to find
    non-optimal months (sum file_count > partition count), then for
    each rewrites only that month's rows scoped on the ``year_month``
    data column. Used by compact_table for tables over
    _MAX_SAFE_COMPACT_BYTES that carry a ``year_month`` column.
    """
    from collections import defaultdict

    from pyiceberg.expressions import EqualTo

    from tools._stock_shared import _require_repo

    table_dir = WAREHOUSE_DIR / table_name.replace(".", "/")
    before = _count_parquet_files(table_dir)
    try:
        repo = _require_repo()
        tbl = repo.load_table(table_name)
    except Exception:
        _logger.error(
            "[maint] batched: failed to load %s",
            table_name,
            exc_info=True,
        )
        return {"table": table_name, "error": "read failed"}

    names = [f.name for f in tbl.schema().fields]
    if "year_month" not in names:
        _logger.warning(
            "[maint] %s exceeds byte limit but has no year_month "
            "column — skipping (needs generic batched compaction)",
            table_name,
        )
        return {
            "table": table_name,
            "before": before,
            "after": before,
            "skipped_too_large_bytes": True,
        }

    try:
        pdict = tbl.inspect.partitions().to_pydict()
    except Exception:
        _logger.error(
            "[maint] batched: inspect.partitions failed for %s",
            table_name,
            exc_info=True,
        )
        return {"table": table_name, "error": "inspect failed"}

    parts = pdict.get("partition", [])
    fcs = pdict.get("file_count", [])
    by_month: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for p, c in zip(parts, fcs):
        bm = p["bar_month"]
        by_month[bm][0] += 1
        by_month[bm][1] += int(c)
    non_optimal = sorted(
        bm for bm, (nparts, nfiles) in by_month.items()
        if nfiles > nparts
    )
    skipped = len(by_month) - len(non_optimal)

    target = tbl.schema().as_arrow()
    rewritten = 0
    errors: list[str] = []
    for bm in non_optimal:
        ym = _bar_month_to_year_month(bm)
        flt = EqualTo("year_month", ym)
        try:
            arrow = tbl.scan(row_filter=flt).to_arrow().cast(target)

            def _do(_t=tbl, _a=arrow, _f=flt) -> None:
                _t.overwrite(_a, overwrite_filter=_f)

            retry_iceberg_op(table_name, _do)
            invalidate_metadata(table_name)
            rewritten += 1
            _logger.info(
                "[maint] %s month %s compacted", table_name, ym,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[maint] %s month %s compaction failed: %s",
                table_name,
                ym,
                exc,
                exc_info=True,
            )
            errors.append(f"{ym}: {str(exc)[:120]}")

    after = _count_parquet_files(table_dir)
    _logger.info(
        "[maint] %s batched compaction: %d months rewritten, "
        "%d skipped, %d → %d files, %d errors",
        table_name,
        rewritten,
        skipped,
        before,
        after,
        len(errors),
    )
    return {
        "table": table_name,
        "before": before,
        "after": after,
        "months_rewritten": rewritten,
        "months_skipped": skipped,
        "errors": errors,
        "batched": True,
    }
```
Grep first: if `retry_iceberg_op` is NOT already imported at module top, add `from backend.algo._iceberg_retry import retry_iceberg_op` with the other `backend.algo` imports (so the test's `patch.object(im, "retry_iceberg_op", ...)` resolves).

- [ ] **Step 4: Run to verify pass** — pytest the file. Expected: PASS (Task 1 + Task 2).

- [ ] **Step 5: Commit**
```bash
git add backend/maintenance/iceberg_maintenance.py tests/backend/test_batched_compaction.py
git commit -m "feat(maint): _compact_table_by_month — per-month batched compaction"
```

---

## Task 3: Route byte-heavy tables in `compact_table` + update ceiling tests

**Files:**
- Modify: `backend/maintenance/iceberg_maintenance.py` (the byte-ceiling block, lines ~442-463).
- Modify: `tests/backend/test_compaction_byte_ceiling.py`.

**Interfaces:**
- Consumes: `_compact_table_by_month` (Task 2).

- [ ] **Step 1: Update the existing byte-ceiling tests to the new routing**

In `tests/backend/test_compaction_byte_ceiling.py`, replace `test_large_table_skips_compaction_by_bytes` with a routing test:
```python
def test_large_table_routes_to_batched(monkeypatch, tmp_path):
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
    called = {}
    monkeypatch.setattr(
        im, "_compact_table_by_month",
        lambda t: called.setdefault("t", t) or {"batched": True},
    )
    res = im.compact_table("stocks.intraday_features")
    assert called["t"] == "stocks.intraday_features"
    assert res.get("batched") is True
```
Keep `test_under_byte_ceiling_still_compacts` unchanged (200 MB → routing not triggered, reaches the read path).

- [ ] **Step 2: Run to verify failure**

Run pytest on `tests/backend/test_compaction_byte_ceiling.py`.
Expected: FAIL — `compact_table` returns `skipped_too_large_bytes` instead of calling `_compact_table_by_month`.

- [ ] **Step 3: Route in `compact_table`**

Replace the byte-ceiling skip block (lines ~442-463) with:
```python
    safe_bytes = _table_data_bytes(table_dir)
    if safe_bytes > _MAX_SAFE_COMPACT_BYTES:
        _logger.info(
            "[maint] %s is %.0f MB (> %d MB in-process limit) — "
            "routing to batched per-month compaction",
            table_name,
            safe_bytes / (1024 * 1024),
            _MAX_SAFE_COMPACT_BYTES // (1024 * 1024),
        )
        return _compact_table_by_month(table_name)
```
(`_compact_table_by_month` returns `skipped_too_large_bytes` itself when the table has no `year_month` column — preserving the old behavior for that case.)

- [ ] **Step 4: Run to verify pass**

Run pytest on `tests/backend/test_compaction_byte_ceiling.py tests/backend/test_batched_compaction.py tests/backend/test_compaction_size_aware.py backend/maintenance/tests/`.
Expected: PASS (routing + batched + size-aware + maintenance suites).

- [ ] **Step 5: Commit**
```bash
git add backend/maintenance/iceberg_maintenance.py tests/backend/test_compaction_byte_ceiling.py
git commit -m "feat(maint): route byte-heavy tables to batched per-month compaction"
```

---

## Task 4: Integration verify + docs (operational)

**Files:** `PROGRESS.md`. Run AFTER Tasks 1-3 merged + deployed.

- [ ] **Step 1: Deploy** — merge the PR + `docker compose restart backend` + wait healthy.

- [ ] **Step 2: Verify on the real table (compose env)**
```bash
docker compose exec -T backend python -c "
from stocks.create_tables import _get_catalog
c=_get_catalog(); t=c.load_table('stocks.intraday_features')
n0=t.scan(selected_fields=('ticker',)).to_arrow().num_rows
from backend.maintenance.iceberg_maintenance import compact_table
print(compact_table('stocks.intraday_features'))
t2=c.load_table('stocks.intraday_features')
print('rows before/after:', n0, t2.scan(selected_fields=('ticker',)).to_arrow().num_rows)
"
docker inspect ai-agent-ui-backend-1 --format 'OOMKilled={{.State.OOMKilled}}'
```
Expected: result `batched: True`, `months_rewritten >= 1`, no OOM; row count unchanged; `OOMKilled=false`.

- [ ] **Step 3: PROGRESS.md** — dated 2026-06-21 entry (batched per-month compaction; intraday_features self-compacts without OOM; ASETPLTFRM-442).

- [ ] **Step 4: Commit + push**
```bash
git add PROGRESS.md
git commit -m "docs: batched per-month compaction (ASETPLTFRM-442)"
git push -u origin feature/batched-compaction
```

---

## Self-Review Notes

- **Spec coverage:** helper (Tasks 1+2), routing (Task 3), error isolation + skip-optimal + no-year_month fallback (Task 2 tests), verify+docs (Task 4). All spec items covered.
- **`bar_month→year_month`:** verified against live `inspect.partitions()` (677→2026-06); the spec's "verify during implementation" item is resolved.
- **Type consistency:** `_compact_table_by_month(table_name)->dict`, `_bar_month_to_year_month(int)->str`, partition dict key `"bar_month"`, return keys (`months_rewritten`/`months_skipped`/`batched`) used identically across tasks + tests.
- **retry_iceberg_op:** the `_do` closure overwrites the loaded `tbl` directly (testable; matches the maintenance no-concurrent-writer context) via the commit-lock wrapper.
- **Open confirmations for implementer:** whether `retry_iceberg_op` is already imported in `iceberg_maintenance.py` (grep; add if missing); the exact `EqualTo` attribute names (`.term.name`, `.literal.value`) — verify against the installed PyIceberg and adjust the Task 2 assertion if needed.
