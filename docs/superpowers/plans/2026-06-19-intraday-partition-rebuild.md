# Intraday Tables Partition-Spec Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the parquet-file explosion (37k–68k files) on the three EOD intraday Iceberg tables by replacing the `IdentityTransform(ticker) + IdentityTransform(year_month)` partition spec with `BucketTransform(16, ticker) + MonthTransform(bar_date_d)` + a `SortOrder`, via a lossless rebuild+swap migration.

**Architecture:** Add a `bar_date_d` `DateType` column to each table's schema (one-way type change → forces a rebuild, not in-place evolution). Add a new partition-spec helper and per-table sort orders in `stocks/create_tables.py`. Writers are left structurally unchanged (delete+append / overwrite-by-bar_date) and only learn to populate `bar_date_d`; readers are unchanged (the `year_month`/`bar_date` string columns are retained as residual filter columns). A one-time migration script rebuilds each table into a `_v2` with the new spec, derives `bar_date_d = date.fromisoformat(bar_date)`, then atomically renames `_v2` into the canonical name. The existing daily compaction step stays enrolled but becomes trivial (~700-file floor, can never reach the 40k `_MAX_SAFE_COMPACT_FILES` cliff).

**Tech Stack:** Python 3.12, PyIceberg 0.11.1 (`SqlCatalog`, `BucketTransform`, `MonthTransform`, `SortOrder`/`SortField`, `rename_table`, `drop_table`), PyArrow, pytest, Docker Compose.

## Global Constraints

- Affected tables (EOD-only, NOT live tick path): `stocks.intraday_bars`, `stocks.index_intraday_bars`, `stocks.intraday_features`. The live `algo.intraday_bars` (append-only, `bars_writer.py`) is OUT OF SCOPE and untouched.
- Partition spec (every affected table): `BucketTransform(16)` on `ticker` (field_id 1000, name `ticker_bucket`) + `MonthTransform()` on `bar_date_d` (field_id 1001, name `bar_month`). (§4.3 #22.a/#22.b)
- `bar_date_d` is `DateType`, `required=True`; PyArrow `pa.date32()`, `nullable=False`; value = `date.fromisoformat(bar_date_str)`. (§4.3 #22.d)
- Retain `bar_date` (`StringType`) and `year_month` (`StringType`) columns unchanged — readers depend on them.
- Iceberg `TimestampType` is tz-naive; `written_at` keeps existing tz-strip logic (§5.1).
- Line length 79 chars (black/isort/flake8). `X | None` not `Optional`. No bare `except`. (§4.2)
- NEVER `rm` Iceberg files; orphans from the swap are reclaimed by `cleanup_orphans_v2()` (§4.3 #20).
- Iceberg schema change → backend restart + `redis-cli FLUSHALL` (§6.2).
- Code change (Task 1) MUST be deployed before the migration (Task 6) runs, and no writes may hit the tables between migration and restart (next EOD job is 15:45 IST — safe window).
- Branch off `dev`; never push to `dev`/`qa`/`release`/`main`. Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>`. (§4.4)

---

## File Structure

- `stocks/create_tables.py` — add `bar_date_d` to 3 schemas; add `_ticker_bucket_month_partition_spec()`; add 3 sort-order helpers; update 3 `_create_table()` call sites.
- `backend/algo/backtest/intraday_backfill.py` — `_arrow_schema()` + `_bars_to_arrow()` populate `bar_date_d`.
- `backend/algo/backtest/index_intraday_backfill.py` — `_arrow_schema()` + `_bars_to_arrow()` populate `bar_date_d`.
- `backend/algo/jobs/intraday_features_daily_compute.py` — `_features_arrow_schema()` + `_panel_to_arrow_rows()` populate `bar_date_d`.
- `scripts/migrate_intraday_partition_spec.py` — NEW one-time rebuild+swap migration (chunked by `year_month`).
- `tests/backend/test_intraday_bars_table.py`, `test_index_intraday_bars_table.py`, `test_intraday_features_table.py` — assert new spec + `bar_date_d`.
- `tests/backend/test_iceberg_design_rule_guard.py` — remove `stocks.create_tables` from `GRANDFATHERED_MODULES`; wire it into the scan.
- `scripts/tests/test_migrate_intraday_partition_spec.py` — NEW migration round-trip test (temp catalog).
- Existing writer tests updated for the new `bar_date_d` column.

---

## Task 0: Branch setup (pre-flight, no TDD)

**Files:** none (git only)

- [ ] **Step 1: Confirm working tree state and branch off dev**

Run:
```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
git status --short
git stash list
```
Expected: review any uncommitted changes (from prior session) with the user before branching. If clean enough, create the feature branch:
```bash
git checkout dev && git pull
git checkout -b feature/intraday-partition-rebuild
```
If the current branch has unrelated uncommitted work, STOP and confirm with the user how to handle it before proceeding.

---

## Task 1: Schema + partition spec + sort order (`stocks/create_tables.py`)

**Files:**
- Modify: `stocks/create_tables.py` (3 schema fns, new spec helper, 3 sort-order helpers, 3 create call sites)
- Test: `tests/backend/test_intraday_bars_table.py`, `tests/backend/test_index_intraday_bars_table.py`, `tests/backend/test_intraday_features_table.py`

**Interfaces:**
- Produces: `_ticker_bucket_month_partition_spec(schema: Schema, *, buckets: int = 16) -> PartitionSpec`
- Produces: `bar_date_d` field present in `_intraday_bars_schema()` (field_id 13), `_index_intraday_bars_schema()` (field_id 13), `_intraday_features_schema()` (field_id 10).
- Produces: `_intraday_bars_sort_order(schema)`, `_index_intraday_bars_sort_order(schema)`, `_intraday_features_sort_order(schema)` returning `SortOrder`.

- [ ] **Step 1: Write the failing spec/schema test**

In `tests/backend/test_intraday_bars_table.py` add:
```python
from pyiceberg.transforms import BucketTransform, MonthTransform
from stocks.create_tables import (
    _intraday_bars_schema,
    _ticker_bucket_month_partition_spec,
)


def test_intraday_bars_has_bar_date_d_datetype():
    from pyiceberg.types import DateType
    schema = _intraday_bars_schema()
    f = schema.find_field("bar_date_d")
    assert isinstance(f.field_type, DateType)
    assert f.required is True


def test_intraday_bars_partition_spec_is_bucket_month():
    schema = _intraday_bars_schema()
    spec = _ticker_bucket_month_partition_spec(schema)
    by_name = {pf.name: pf for pf in spec.fields}
    assert isinstance(by_name["ticker_bucket"].transform, BucketTransform)
    assert isinstance(by_name["bar_month"].transform, MonthTransform)
    tk = schema.find_field("ticker").field_id
    bd = schema.find_field("bar_date_d").field_id
    assert by_name["ticker_bucket"].source_id == tk
    assert by_name["bar_month"].source_id == bd
```
Add the analogous two tests to `test_index_intraday_bars_table.py` (using `_index_intraday_bars_schema`) and `test_intraday_features_table.py` (using `_intraday_features_schema`).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/backend/test_intraday_bars_table.py -k "bar_date_d or bucket_month" -v`
Expected: FAIL — `bar_date_d` field not found / `_ticker_bucket_month_partition_spec` does not exist.

- [ ] **Step 3: Add `bar_date_d` to the three schemas**

In `_intraday_bars_schema()`, after the `year_month` field (field_id 12), add:
```python
        NestedField(
            field_id=13,
            name="bar_date_d",
            field_type=DateType(),
            required=True,
        ),
```
In `_index_intraday_bars_schema()`, after `year_month` (field_id 12), add the identical field with `field_id=13`.

In `_intraday_features_schema()`, after `written_at` (field_id 9), add the identical field with `field_id=10`.

Ensure `DateType` is imported at the top of `stocks/create_tables.py` (add to the existing `from pyiceberg.types import (...)` block if missing).

- [ ] **Step 4: Add the new partition-spec helper**

Add near `_ticker_year_month_partition_spec` (after line ~1999):
```python
def _ticker_bucket_month_partition_spec(
    schema: Schema,
    *,
    buckets: int = 16,
) -> PartitionSpec:
    """Return a partition spec bucketing ``ticker`` into ``buckets``
    and folding ``bar_date_d`` by month (CLAUDE.md §4.3 #22.a/b).

    Replaces the legacy ``(ticker, year_month)`` identity grid which
    produced one partition cell per (ticker, month) — ~22.7k cells
    for 515 tickers × ~44 months, flooring the file count at ~22.7k.
    ``BucketTransform(16) + MonthTransform`` yields 16 × N_months
    cells (~700/yr), 32× fewer files.
    """
    ticker_fid = schema.find_field("ticker").field_id
    bar_date_d_fid = schema.find_field("bar_date_d").field_id
    return PartitionSpec(
        PartitionField(
            source_id=ticker_fid,
            field_id=1000,
            transform=BucketTransform(buckets),
            name="ticker_bucket",
        ),
        PartitionField(
            source_id=bar_date_d_fid,
            field_id=1001,
            transform=MonthTransform(),
            name="bar_month",
        ),
    )
```
Add imports at top: `from pyiceberg.transforms import BucketTransform, MonthTransform` (extend existing `IdentityTransform` import line).

- [ ] **Step 5: Add three sort-order helpers**

Add after the new spec helper:
```python
def _intraday_bars_sort_order(schema: Schema) -> SortOrder:
    """Sort (ticker, interval_sec, bar_open_ts_ns) within each
    bucket-month partition — drives compaction layout + predicate
    pushdown (CLAUDE.md §4.3 #22.c)."""
    return SortOrder(
        SortField(
            source_id=schema.find_field("ticker").field_id,
            transform=IdentityTransform(),
        ),
        SortField(
            source_id=schema.find_field("interval_sec").field_id,
            transform=IdentityTransform(),
        ),
        SortField(
            source_id=schema.find_field("bar_open_ts_ns").field_id,
            transform=IdentityTransform(),
        ),
    )


def _index_intraday_bars_sort_order(schema: Schema) -> SortOrder:
    """Identical layout to ``_intraday_bars_sort_order`` — the index
    table mirrors the equity bars table column-for-column."""
    return _intraday_bars_sort_order(schema)


def _intraday_features_sort_order(schema: Schema) -> SortOrder:
    """Sort (ticker, interval_sec, bar_open_ts_ns, feature_name)
    within each bucket-month partition."""
    return SortOrder(
        SortField(
            source_id=schema.find_field("ticker").field_id,
            transform=IdentityTransform(),
        ),
        SortField(
            source_id=schema.find_field("interval_sec").field_id,
            transform=IdentityTransform(),
        ),
        SortField(
            source_id=schema.find_field("bar_open_ts_ns").field_id,
            transform=IdentityTransform(),
        ),
        SortField(
            source_id=schema.find_field("feature_name").field_id,
            transform=IdentityTransform(),
        ),
    )
```
Add imports at top: `from pyiceberg.table.sorting import SortOrder, SortField`.

- [ ] **Step 6: Update the three `_create_table` call sites**

In `create_tables()`:
- `intraday_bars`: change `_ticker_year_month_partition_spec(intraday_bars_schema)` →
  `_ticker_bucket_month_partition_spec(intraday_bars_schema)` and add
  `sort_order=_intraday_bars_sort_order(intraday_bars_schema)` to the `_create_table(...)` call.
- `intraday_features`: same swap → `_ticker_bucket_month_partition_spec(intraday_features_schema)`,
  `sort_order=_intraday_features_sort_order(intraday_features_schema)`.
- `index_intraday_bars`: same swap → `_ticker_bucket_month_partition_spec(index_intraday_bars_schema)`,
  `sort_order=_index_intraday_bars_sort_order(index_intraday_bars_schema)`.

(Note: `_create_table` is idempotent and skips existing tables, so this only affects *fresh* creation; the live tables are converted by the migration in Task 6.)

- [ ] **Step 7: Run tests to verify pass**

Run: `python -m pytest tests/backend/test_intraday_bars_table.py tests/backend/test_index_intraday_bars_table.py tests/backend/test_intraday_features_table.py -v`
Expected: PASS.

- [ ] **Step 8: Lint + commit**

```bash
black stocks/create_tables.py tests/backend/test_intraday_bars_table.py tests/backend/test_index_intraday_bars_table.py tests/backend/test_intraday_features_table.py
isort stocks/create_tables.py --profile black
flake8 stocks/create_tables.py
git add stocks/create_tables.py tests/backend/
git commit -m "feat(iceberg): bucket+month spec + bar_date_d for intraday tables"
```

---

## Task 2: `intraday_backfill.py` writer — populate `bar_date_d`

**Files:**
- Modify: `backend/algo/backtest/intraday_backfill.py:86-171` (`_arrow_schema`, `_bars_to_arrow`)
- Test: `backend/algo/backtest/tests/test_intraday_backfill.py`

**Interfaces:**
- Produces: `_bars_to_arrow()` output now includes a `bar_date_d` `date32` column equal to `date.fromisoformat(bar_date)`.

- [ ] **Step 1: Write the failing test**

In `backend/algo/backtest/tests/test_intraday_backfill.py` add (adjust the `BarData` import/constructor to the real dataclass first):
```python
from datetime import date


def test_bars_to_arrow_includes_bar_date_d():
    from backend.algo.backtest.intraday_backfill import _bars_to_arrow
    from backend.algo.backtest.data_source import BarData
    bars = [
        BarData(
            ticker="RELIANCE.NS",
            date=date(2026, 6, 19),
            bar_open_ts_ns=1,
            open=1.0, high=2.0, low=0.5, close=1.5, volume=10,
        )
    ]
    tbl = _bars_to_arrow(bars, interval_sec=900, source="kite")
    assert "bar_date_d" in tbl.schema.names
    assert tbl.column("bar_date_d")[0].as_py() == date(2026, 6, 19)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest backend/algo/backtest/tests/test_intraday_backfill.py -k bar_date_d -v`
Expected: FAIL — `bar_date_d` not in schema names.

- [ ] **Step 3: Add the column to `_arrow_schema()`**

After the `year_month` field (line ~110), add:
```python
            pa.field("bar_date_d", pa.date32(), nullable=False),
```

- [ ] **Step 4: Populate it in `_bars_to_arrow()`**

In the row dict (after `"year_month": bar_date_str[:7],`), add:
```python
                "bar_date_d": date.fromisoformat(bar_date_str),
```
Add `date` to the existing `from datetime import ...` line at the top of the module.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest backend/algo/backtest/tests/test_intraday_backfill.py -v`
Expected: PASS (new + existing tests).

- [ ] **Step 6: Lint + commit**

```bash
black backend/algo/backtest/intraday_backfill.py backend/algo/backtest/tests/test_intraday_backfill.py
flake8 backend/algo/backtest/intraday_backfill.py
git add backend/algo/backtest/intraday_backfill.py backend/algo/backtest/tests/test_intraday_backfill.py
git commit -m "feat(algo): populate bar_date_d in intraday_bars writer"
```

---

## Task 3: `index_intraday_backfill.py` writer — populate `bar_date_d`

**Files:**
- Modify: `backend/algo/backtest/index_intraday_backfill.py:57-140` (`_arrow_schema`, `_bars_to_arrow`)
- Test: `backend/algo/backtest/tests/test_index_intraday_backfill.py`

**Interfaces:**
- Produces: `_bars_to_arrow()` output includes `bar_date_d` `date32` = `date.fromisoformat(bar_date)`.

- [ ] **Step 1: Write the failing test** — add `test_index_bars_to_arrow_includes_bar_date_d` mirroring Task 2 Step 1 but importing from `index_intraday_backfill` and using an index symbol (e.g. `"NIFTY 50"`).

- [ ] **Step 2: Run to verify failure** — `python -m pytest backend/algo/backtest/tests/test_index_intraday_backfill.py -k bar_date_d -v` → FAIL.

- [ ] **Step 3: Add column to `_arrow_schema()`** — add `pa.field("bar_date_d", pa.date32(), nullable=False),` after the `year_month` field (line ~78).

- [ ] **Step 4: Populate in `_bars_to_arrow()`** — add `"bar_date_d": date.fromisoformat(bar_date_str),` after the `year_month` entry (line ~133). Ensure `from datetime import date`.

- [ ] **Step 5: Run to verify pass** — `python -m pytest backend/algo/backtest/tests/test_index_intraday_backfill.py -v` → PASS.

- [ ] **Step 6: Lint + commit**

```bash
black backend/algo/backtest/index_intraday_backfill.py backend/algo/backtest/tests/test_index_intraday_backfill.py
flake8 backend/algo/backtest/index_intraday_backfill.py
git add backend/algo/backtest/index_intraday_backfill.py backend/algo/backtest/tests/test_index_intraday_backfill.py
git commit -m "feat(algo): populate bar_date_d in index_intraday_bars writer"
```

---

## Task 4: `intraday_features_daily_compute.py` writer — populate `bar_date_d`

**Files:**
- Modify: `backend/algo/jobs/intraday_features_daily_compute.py:81-100` (`_features_arrow_schema`), `:237-287` (`_panel_to_arrow_rows`)
- Test: `backend/algo/jobs/tests/test_intraday_features_daily_compute.py`

**Interfaces:**
- Produces: `_panel_to_arrow_rows()` rows include `bar_date_d`; `_features_arrow_schema()` includes the `date32` column.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
import pyarrow as pa


def test_features_arrow_schema_has_bar_date_d():
    from backend.algo.jobs.intraday_features_daily_compute import (
        _features_arrow_schema,
    )
    assert "bar_date_d" in _features_arrow_schema().names
    assert _features_arrow_schema().field("bar_date_d").type == pa.date32()
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest backend/algo/jobs/tests/test_intraday_features_daily_compute.py -k bar_date_d -v` → FAIL.

- [ ] **Step 3: Add column to `_features_arrow_schema()`** — add `pa.field("bar_date_d", pa.date32(), nullable=False),` after the `year_month` field (line ~93).

- [ ] **Step 4: Populate in `_panel_to_arrow_rows()`** — in the row dict (after `"year_month": year_month,`), add `"bar_date_d": date.fromisoformat(bar_date_str),`. Ensure `from datetime import date`.

- [ ] **Step 5: Run to verify pass** — `python -m pytest backend/algo/jobs/tests/test_intraday_features_daily_compute.py -v` → PASS.

- [ ] **Step 6: Lint + commit**

```bash
black backend/algo/jobs/intraday_features_daily_compute.py backend/algo/jobs/tests/test_intraday_features_daily_compute.py
flake8 backend/algo/jobs/intraday_features_daily_compute.py
git add backend/algo/jobs/intraday_features_daily_compute.py backend/algo/jobs/tests/test_intraday_features_daily_compute.py
git commit -m "feat(algo): populate bar_date_d in intraday_features writer"
```

---

## Task 5: Migration script (rebuild + swap, chunked by month)

**Files:**
- Create: `scripts/migrate_intraday_partition_spec.py`
- Test: `scripts/tests/test_migrate_intraday_partition_spec.py` (create `scripts/tests/__init__.py` if absent)

**Interfaces:**
- Consumes: `_get_catalog`, the three `_*_schema()` fns, `_ticker_bucket_month_partition_spec`, the three `_*_sort_order` fns from `stocks.create_tables`.
- Produces: `migrate_table(catalog, canonical, schema_fn, sort_order_fn) -> dict` returning `{"old_rows": int, "new_rows": int, "swapped": bool}`. `_add_bar_date_d(arrow_tbl) -> pa.Table` deriving the date column from the `bar_date` string column.

- [ ] **Step 1: Write the failing round-trip test**

In `scripts/tests/test_migrate_intraday_partition_spec.py`:
```python
import pyarrow as pa
from datetime import date


def test_add_bar_date_d_derives_from_bar_date_string():
    from scripts.migrate_intraday_partition_spec import _add_bar_date_d
    src = pa.table({
        "ticker": ["A.NS"],
        "bar_date": ["2026-06-19"],
        "year_month": ["2026-06"],
    })
    out = _add_bar_date_d(src)
    assert "bar_date_d" in out.schema.names
    assert out.column("bar_date_d")[0].as_py() == date(2026, 6, 19)
    assert out.num_rows == src.num_rows
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest scripts/tests/test_migrate_intraday_partition_spec.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement the migration script**

```python
"""One-time rebuild of the three EOD intraday Iceberg tables onto the
``BucketTransform(16, ticker) + MonthTransform(bar_date_d)`` spec.

Lossless: reads every row from the legacy table, derives
``bar_date_d`` from the ``bar_date`` string, writes into a ``_v2``
table with the new spec (chunked by ``year_month`` to bound memory),
verifies row-count parity, then atomically renames ``_v2`` into the
canonical name. The old catalog entry is dropped (NOT purged) so its
now-orphan files are reclaimed later by ``cleanup_orphans_v2()`` —
never ``rm`` (CLAUDE.md §4.3 #20).

Run INSIDE the backend container AFTER deploying the Task 1-4 code and
BEFORE the next EOD write:
    docker compose exec -T backend python -m scripts.migrate_intraday_partition_spec
"""
from __future__ import annotations

import logging
from datetime import date

import pyarrow as pa
from pyiceberg.expressions import EqualTo

from backend.maintenance.backup import backup_table
from stocks.create_tables import (
    _get_catalog,
    _index_intraday_bars_schema,
    _index_intraday_bars_sort_order,
    _intraday_bars_schema,
    _intraday_bars_sort_order,
    _intraday_features_schema,
    _intraday_features_sort_order,
    _ticker_bucket_month_partition_spec,
)

_logger = logging.getLogger(__name__)

_TABLES = [
    (
        "stocks.intraday_bars",
        _intraday_bars_schema,
        _intraday_bars_sort_order,
    ),
    (
        "stocks.index_intraday_bars",
        _index_intraday_bars_schema,
        _index_intraday_bars_sort_order,
    ),
    (
        "stocks.intraday_features",
        _intraday_features_schema,
        _intraday_features_sort_order,
    ),
]


def _add_bar_date_d(arrow_tbl: pa.Table) -> pa.Table:
    """Append a ``bar_date_d`` date32 column derived from the existing
    ``bar_date`` YYYY-MM-DD string column."""
    bar_dates = [
        date.fromisoformat(s)
        for s in arrow_tbl.column("bar_date").to_pylist()
    ]
    arr = pa.array(bar_dates, type=pa.date32())
    return arrow_tbl.append_column(
        pa.field("bar_date_d", pa.date32(), nullable=False), arr
    )


def migrate_table(catalog, canonical, schema_fn, sort_order_fn) -> dict:
    v2 = f"{canonical}_v2"
    old = f"{canonical}_old"
    schema = schema_fn()
    spec = _ticker_bucket_month_partition_spec(schema)
    sort_order = sort_order_fn(schema)

    src = catalog.load_table(canonical)
    old_rows = src.scan().to_arrow().num_rows
    _logger.info("[migrate] %s: %d rows to copy", canonical, old_rows)

    try:
        catalog.drop_table(v2)  # clean any aborted prior run
    except Exception:  # noqa: BLE001
        pass
    catalog.create_table(
        identifier=v2,
        schema=schema,
        partition_spec=spec,
        sort_order=sort_order,
    )
    v2_tbl = catalog.load_table(v2)

    months = sorted(set(
        src.scan(selected_fields=("year_month",))
        .to_arrow().column("year_month").to_pylist()
    ))
    new_rows = 0
    for ym in months:
        chunk = src.scan(row_filter=EqualTo("year_month", ym)).to_arrow()
        if chunk.num_rows == 0:
            continue
        chunk = _add_bar_date_d(chunk)
        chunk = chunk.cast(v2_tbl.schema().as_arrow())
        v2_tbl.append(chunk)
        new_rows += chunk.num_rows
        _logger.info(
            "[migrate] %s %s: +%d rows", canonical, ym, chunk.num_rows
        )

    if new_rows != old_rows:
        raise RuntimeError(
            f"[migrate] {canonical} row mismatch: old={old_rows} "
            f"new={new_rows} — aborting swap, _v2 left for inspection"
        )

    catalog.rename_table(canonical, old)
    catalog.rename_table(v2, canonical)
    catalog.drop_table(old)  # catalog entry only; files swept later
    _logger.info("[migrate] %s: swapped, %d rows", canonical, new_rows)
    return {"old_rows": old_rows, "new_rows": new_rows, "swapped": True}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    catalog = _get_catalog()
    for canonical, schema_fn, sort_order_fn in _TABLES:
        _logger.info("[migrate] backing up %s", canonical)
        backup_table(canonical)  # fail-closed step 0 (§6.4)
        res = migrate_table(catalog, canonical, schema_fn, sort_order_fn)
        _logger.info("[migrate] %s done: %s", canonical, res)


if __name__ == "__main__":
    main()
```
Before running: confirm `backup_table(canonical)` signature against `backend/maintenance/backup.py:79` (adjust if it needs a destination-dir arg), and confirm `v2_tbl.schema().as_arrow()` exists in PyIceberg 0.11.1 (fallback: build the arrow schema explicitly from the writer's `_arrow_schema()` + `bar_date_d`).

- [ ] **Step 4: Run to verify the unit test passes** — `python -m pytest scripts/tests/test_migrate_intraday_partition_spec.py -v` → PASS.

- [ ] **Step 5: Lint + commit**

```bash
black scripts/migrate_intraday_partition_spec.py scripts/tests/test_migrate_intraday_partition_spec.py
isort scripts/migrate_intraday_partition_spec.py --profile black
flake8 scripts/migrate_intraday_partition_spec.py
git add scripts/migrate_intraday_partition_spec.py scripts/tests/
git commit -m "feat(migrate): intraday partition-spec rebuild+swap script"
```

---

## Task 6: Remove grandfather exemption + run migration + verify (operational)

**Files:**
- Modify: `tests/backend/test_iceberg_design_rule_guard.py`

- [ ] **Step 1: Un-grandfather `stocks.create_tables`**

Remove `"stocks.create_tables"` from `GRANDFATHERED_MODULES`. Since the scan walks `iceberg_init.py` files only, add an explicit assertion test:
```python
def test_intraday_tables_use_bucket_not_identity():
    import stocks.create_tables as ct
    from pyiceberg.transforms import IdentityTransform
    for fn in (
        ct._intraday_bars_schema,
        ct._index_intraday_bars_schema,
        ct._intraday_features_schema,
    ):
        schema = fn()
        spec = ct._ticker_bucket_month_partition_spec(schema)
        for pf in spec.fields:
            col = schema.find_field(pf.source_id).name
            if col == "ticker":
                assert not isinstance(pf.transform, IdentityTransform)
```

- [ ] **Step 2: Run the full guard + per-table suite**

Run: `python -m pytest tests/backend/test_iceberg_design_rule_guard.py tests/backend/test_intraday_bars_table.py tests/backend/test_index_intraday_bars_table.py tests/backend/test_intraday_features_table.py -v`
Expected: PASS.

- [ ] **Step 3: Commit the guard change**

```bash
black tests/backend/test_iceberg_design_rule_guard.py
git add tests/backend/test_iceberg_design_rule_guard.py
git commit -m "test(iceberg): un-grandfather intraday tables; assert bucket spec"
```

- [ ] **Step 4: Deploy code to the running backend + restart**

```bash
docker compose restart backend
until curl -sf http://localhost:8181/v1/health >/dev/null 2>&1; do sleep 3; done
```

- [ ] **Step 5: Pre-migration snapshot of row counts**

```bash
docker compose exec -T backend python -c "
from stocks.create_tables import _get_catalog
c=_get_catalog()
for t in ['stocks.intraday_bars','stocks.index_intraday_bars','stocks.intraday_features']:
    print(t, c.load_table(t).scan().to_arrow().num_rows)
"
```
Expected: `intraday_bars` ≈ 11,340,562; record all three numbers.

- [ ] **Step 6: Run the migration**

```bash
docker compose exec -T backend python -m scripts.migrate_intraday_partition_spec
```
Expected: per-table `swapped` with `new_rows == old_rows`. A mismatch raises and leaves `_v2` unswapped (no data loss).

- [ ] **Step 7: Restart backend + FLUSHALL (§6.2)**

```bash
docker compose restart backend
docker compose exec -T redis redis-cli FLUSHALL
until curl -sf http://localhost:8181/v1/health >/dev/null 2>&1; do sleep 3; done
sleep 5
```

- [ ] **Step 8: Verify file collapse, spec, and today's data intact**

```bash
docker compose exec -T backend python -c "
from stocks.create_tables import _get_catalog
import pyarrow.compute as pc
c=_get_catalog()
for t in ['stocks.intraday_bars','stocks.index_intraday_bars','stocks.intraday_features']:
    tbl=c.load_table(t)
    print(t,'spec=',[(f.name,str(f.transform)) for f in tbl.spec().fields])
    df=tbl.scan(selected_fields=('bar_date','ticker')).to_arrow()
    print('  rows=',df.num_rows,'max_date=',pc.max(df.column('bar_date')).as_py())
"
W=/Users/abhay/.ai-agent-ui/data/iceberg/warehouse/stocks
for t in intraday_bars index_intraday_bars intraday_features; do
  echo "$t files: $(find $W/$t/data -name '*.parquet'|wc -l)"; done
```
Expected: spec shows `ticker_bucket`/`bar_month`; row counts == pre-migration; `max_date` == latest trading day; file counts collapse to low hundreds–~1k each. Old-table orphan dirs may linger until the sweep (Task 7) — expected and safe.

- [ ] **Step 9: Smoke-test reader paths**

Run: `python -m pytest backend/algo/backtest/tests/test_load_intraday_bars_window.py backend/algo/features/tests/test_loader.py -v`
Expected: PASS — readers return rows under the new spec (residual `year_month`/`bar_date` filters still valid).

---

## Task 7: Docs + memory + orphan sweep + push

**Files:** `PROGRESS.md`, Serena memory `iceberg-ticker-partition-file-explosion`

- [ ] **Step 1: Update `PROGRESS.md`** with a dated entry (37k/68k → ~1k files, bucket+month spec, lossless migration, 60/40 grid/churn diagnosis).

- [ ] **Step 2: Update the Serena memory** `iceberg-ticker-partition-file-explosion` with the fix (bucket(16)+month + `bar_date_d`, migration script path) and the grid-dominant root cause.

- [ ] **Step 3: Reclaim dropped `_old` files via orphan sweep**

```bash
docker compose exec -T backend python -c "
from backend.maintenance.iceberg_maintenance import cleanup_orphans_v2
for t in ['stocks.intraday_bars','stocks.index_intraday_bars','stocks.intraday_features']:
    print(t, cleanup_orphans_v2(t, dry_run=False))
"
```
Expected: orphan files reclaimed (first run 5–15 min).

- [ ] **Step 4: Commit + push**

```bash
git add PROGRESS.md .serena/
git commit -m "docs: intraday partition rebuild — file explosion fixed"
git push -u origin feature/intraday-partition-rebuild
```

---

## Self-Review Notes

- **Spec coverage:** schema (Task 1), spec+sort (Task 1), 3 writers (Tasks 2-4), migration (Task 5), guard+run+verify (Task 6), docs/cleanup (Task 7). All scope items covered.
- **Readers:** intentionally unchanged — `year_month`/`bar_date` retained as residual filters; partition pruning now by month on `bar_date_d`, residual filters keep correctness. Loader-pruning optimization is OUT OF SCOPE (≤1k files makes full scan cheap).
- **Type consistency:** `bar_date_d` is `DateType`/`pa.date32()`/`date.fromisoformat(...)` everywhere; spec helper `_ticker_bucket_month_partition_spec` named identically in create_tables, migration, and guard test.
- **Sequencing risk:** code (Tasks 1-4) deploys before migration (Task 6) so `_v2` is created with the `bar_date_d` schema and writers match the new table; next EOD write is 15:45 IST — safe window.
- **Open confirmations before running:** exact `BarData` constructor in writer tests; `backup_table()` signature; `v2_tbl.schema().as_arrow()` availability in PyIceberg 0.11.1.
