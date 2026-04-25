# Maintenance & Data Health

The Maintenance tab (**Admin > Maintenance**) provides data quality
monitoring, automated fixes, and system cleanup tools.

---

## Data Health Dashboard

Five health cards scan the entire data pipeline for issues.
Auto-scans on page load, manual re-scan via button.

### OHLCV Data

Checks the `stocks.ohlcv` Iceberg table (1.4M+ rows, 752 tickers).

| Check | Threshold | Status |
|-------|-----------|--------|
| NaN/NULL close values | >0 rows | Red |
| Missing latest trading date | >10 tickers | Red |
| Stale data (>3 days old) | >0 tickers | Yellow |
| All up to date | — | Green |

**Fix buttons:**

- **Clean NaN Rows**: Deletes all OHLCV rows where `close IS NULL
  OR isnan(close)`. Uses PyIceberg `Or(IsNull, IsNaN)` expression.
- **Backfill from yfinance**: Finds tickers missing yesterday's
  data, batch-downloads via `yf.download()`, appends to Iceberg.

**Common causes of NaN:**

- yfinance pre-market flat candles (fetched before settlement)
- Pipeline ran before US market close (scheduled too early)
- yfinance transient API failures (silent empty response)

**Post-fix action**: Re-run Data Refresh pipeline to ensure
downstream analytics (indicators, forecasts) are updated.

### Analytics

Checks `stocks.analysis_summary` — technical indicators and
price movement analysis per ticker.

| Check | Threshold | Status |
|-------|-----------|--------|
| Missing tickers | >10 | Yellow |
| All computed | — | Green |

**Fix**: Run Compute Analytics pipeline (`pipeline.runner analytics`).

### Sentiment

Checks `stocks.sentiment_scores` — LLM-scored news headline
sentiment per ticker.

| Check | Threshold | Status |
|-------|-----------|--------|
| Missing tickers | >10 | Yellow |
| Stale scores (>7 days) | >50 tickers | Yellow |
| All scored | — | Green |

**Fix**: Run Sentiment pipeline (`pipeline.runner sentiment`).

**View details**: click "View details →" on the Sentiment data-health
card to open the Sentiment Details modal. It lists today's scoring
breakdown by source (`finbert` / `llm` / `market_fallback` / `none`)
with counts + average score per category, plus a filterable +
paginated table of scored tickers (excluding fallback rows) with
CSV download and scope tabs (all / india / us). Endpoint:
`GET /v1/admin/data-health/sentiment-details?scope=all|india|us`
(superuser, 60s Redis cache).

**Scoring strategy:**

- Hot tickers (>10 headlines): re-scored every run
- Learning tickers (5-10 headlines): **capped at top-50 by market
  cap** per run — the tail drops into market-fallback. Cap keeps the
  batch runtime bounded (~30s for ~85 tickers vs hours for 800+).
- Cold tickers (<5 headlines): use market-level fallback score
- Fresh tickers (scored <24h ago): skipped (unless `force=true`,
  which upserts and overrides today's row)

**Safety net (added Sprint 7):**

- Per-source 10s HTTP timeout (`_run_with_timeout`) on all three
  headline fetchers — protects against `yf.Ticker().news` deadlocks.
- `invalidate_metadata("stocks.sentiment_scores")` before the Step-5
  gap-fill re-query — prevents the DuckDB metadata cache from
  masking pool-inserted rows and double-counting fallback inserts.
- Accurate `source` provenance: rows tagged `finbert` vs `llm` vs
  `market_fallback` vs `none` based on the scorer that actually
  produced the value.

### Piotroski F-Score

Checks `stocks.piotroski_scores` — fundamental scoring based
on quarterly results.

| Check | Threshold | Status |
|-------|-----------|--------|
| Missing tickers | >10 | Yellow |
| Stale scores (>30 days) | >0 | Yellow |
| All scored | — | Green |

**Fix**: Run Piotroski pipeline (`pipeline.runner screen`).

!!! note "Scoped delete"
    India and US pipelines delete only their own tickers before
    inserting. Running US pipeline won't wipe Indian scores.

### Forecasts

Checks `stocks.forecast_runs` — Prophet price forecasts with
cross-validation accuracy.

| Check | Threshold | Status |
|-------|-----------|--------|
| Extreme predictions (>50% deviation) | >50 tickers | Yellow |
| High MAPE (>25%) | >100 tickers | Yellow |
| Missing forecasts | >50 tickers | Red |
| Stale forecasts (>30 days) | >0 | Yellow |
| All fresh and normal | — | Green |

**Fix**: Run Forecast pipeline with `--force` flag to recompute
all models including CV accuracy.

!!! warning "Extreme predictions"
    ~97 tickers (13%) have broken Prophet models due to parabolic
    price histories. These need model tuning (logistic growth,
    changepoint dampening). Tracked in ASETPLTFRM-302.

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/data-health` | GET | Full health scan (all 5 sections) |
| `/admin/data-health/fix-ohlcv` | POST | Fix OHLCV issues |

### GET /admin/data-health

Returns:

```json
{
  "total_registry": 752,
  "ohlcv": {
    "nan_close_count": 0,
    "nan_close_tickers": [],
    "missing_latest_count": 5,
    "stale_count": 0,
    "stale_tickers": []
  },
  "forecasts": {
    "total_tickers": 752,
    "missing_tickers": [],
    "extreme_predictions": 164,
    "high_mape": 101,
    "stale_count": 0
  },
  "sentiment": { "total_tickers": 752, "missing_tickers": [], "stale_count": 0 },
  "piotroski": { "total_tickers": 751, "missing_tickers": ["SKFINDUS.NS"], "stale_count": 0 },
  "analytics": { "total_tickers": 752, "missing_tickers": [] }
}
```

### POST /admin/data-health/fix-ohlcv

Body: `{ "action": "backfill_nan" | "backfill_missing" }`

Returns: `{ "status": "ok", "fixed": 204, "errors": [] }`

---

## Other Maintenance Tools

### Razorpay Subscription Cleanup

Scans active Razorpay subscriptions and classifies:

- **Matched**: current subscription linked to user
- **Orphaned**: same customer, wrong subscription — safe to cancel
- **Unlinked**: no user found — manual review needed

Risk level: Medium. Supports dry-run scan before execution.

### Monthly Usage Counter Reset

Reset monthly API usage counters for users. Supports:

- Individual user reset
- Bulk selected reset
- Reset all

Risk level: Low.

### Iceberg Data Retention Cleanup

Scan Iceberg tables for data that can be cleaned up:

- Old snapshots and orphan data files
- Protected tables (stocks.registry) are never touched
- Supports individual table or bulk cleanup

Risk level: High — data deletion is irreversible.

### Query Gap Analysis

Read-only analysis showing:

- Unresolved data gaps (tickers with missing data)
- External API usage (yfinance, jugaad-data call counts)
- Local data sufficiency rate

Risk level: None (read-only).

---

## Daily auto-backup + auto-compaction (Apr 23+)

Both `India Daily Pipeline` and `USA Daily Pipeline` end with **step 6 = `iceberg_maintenance`**, which runs automatically as part of the daily cron. Sequence inside the step:

1. **Step 0 — backup** (`run_backup()`). Fail-closed: if backup fails, the maintenance step exits with `failed` status and **skips compaction** — preserves the "backup before maintenance" hard rule. No auto-compact without a fresh restore point.
2. **Step 1..N — compact each hot table** via `compact_table()` (read all via DuckDB → `tbl.overwrite()` writes back as one file per partition):
   - `stocks.ohlcv`
   - `stocks.sentiment_scores`
   - `stocks.company_info`
   - `stocks.analysis_summary`
3. After each table: best-effort `expire_snapshots()` + `cleanup_orphans()`. Failures here are logged but don't fail the run.

### Container changes for backup

`run_backup()` shells to `rsync` (~14 GB warehouse) which wasn't in the runtime image. `Dockerfile.backend` now installs both `curl` (for healthcheck) and `rsync` (for backup):

```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl rsync && \
    rm -rf /var/lib/apt/lists/*
```

The host backup directory is mounted at the same path inside the container via `docker-compose.override.yml`, so `run_backup()` writes to `~/Documents/projects/ai-agent-ui-backups/` and the file appears on host transparently. Rotation policy `MAX_BACKUPS=2` (default in `backend/maintenance/backup.py`).

### Why this matters

OHLCV file count grew to **16,156 parquets** within a week of the original ASETPLTFRM-315 compaction (was 817 → grew unbounded from per-ticker daily writes). Reads slowed to 5+ seconds; the `Clean NaN Rows` admin button took **5+ minutes**. Daily auto-compaction prevents recurrence:

| Metric | Pre-compaction | Post-compaction |
|---|---|---|
| OHLCV parquet files | 16,156 | ~800 (1 per ticker partition) |
| Full-count of 1.5M rows | ~5 s | **0.50 s** (~10× faster) |
| `Clean NaN Rows` action | 5+ min | seconds |

Old parquets become orphans on disk after `overwrite()` (Iceberg references only the new files). `cleanup_orphans` removes empty partition dirs but per the "NEVER delete parquet directly" rule, orphan parquets themselves stay until snapshot expiry releases their references.

> **Status (2026-04-25):** orphan files are accumulating without cleanup — ohlcv has 22 722 parquets on disk vs 817 referenced by the live snapshot (96% orphans), and the trend is +2 500 parquets/day. `compact_table()` is logically correct (live reads stay fast, scanning only 817 files via DuckDB) but disk usage and metadata count grow unbounded. The current `expire_snapshots()` is also a no-op based on an outdated assumption — PyIceberg 0.11.1 actually ships `tbl.maintenance.expire_snapshots().by_ids(...).commit()`. Sprint-8 ticket **ASETPLTFRM-338** (5 SP, due 2026-04-29) implements a proper orphan-parquet sweep: backup → expire old snapshots → compute referenced set via `tbl.inspect.all_files()` + `all_manifests()` + catalog `metadata_location` + last K metadata.json → walk on-disk → mtime grace 30 min → paranoid catalog-pointer assertion → unlink → `tbl.scan(limit=1)` verify. Phased rollout: synthetic tests → `analysis_summary` dry-run → `company_info` → `sentiment_scores` → `ohlcv` → weekly schedule. Design in `.serena/memories/shared/architecture/iceberg-orphan-sweep-design.md`. Estimated reclaim: ~10-12 GB / ~50K orphan files.

### Dropping dead Iceberg tables

`backend/maintenance/iceberg_maintenance.py::drop_dead_tables()` removes tables flagged in the `DEAD_TABLES` constant — currently empty after the 2026-04-25 cleanup that retired `stocks.scheduler_runs` and `stocks.scheduled_jobs` (migrated to PG in Sprint 4) plus `stocks.technical_indicators` (replaced by compute-on-demand via `backend/tools/_analysis_indicators.py`).

Hardened by **ASETPLTFRM-328** with three guards:

1. **Fail-closed `run_backup()`** at function entry — if the rsync backup fails, the function returns `{"error": "backup failed: ..."}` without touching the catalog or filesystem.
2. **Per-table rmtree gating** — `shutil.rmtree(table_dir)` only runs for tables whose `catalog.drop_table()` succeeded. Previously a partial catalog failure could wipe on-disk files for a table the catalog still referenced (FileNotFoundError on next read).
3. **Idempotent `NoSuchTableError` handling** — re-running on an already-dropped table is safe; counts as success and proceeds to dir cleanup.

Returns a dict: `{"backup": str(path), "dropped": [...], "skipped": [...], "dirs_removed": [...], "error": str?}`. Tests in `tests/backend/test_iceberg_drop_dead_tables.py`.

### NaN-replaceable OHLCV upsert (Apr 23+)

Both write paths now treat NaN-close rows as "absent" for dedup purposes, so a stuck NaN row from a Yahoo upstream gap doesn't block future re-fetches:

- **Existing-keys query** filters `WHERE close IS NOT NULL AND NOT isnan(close)`
- **Pre-delete** scoped delete of NaN rows for the to-be-inserted `(ticker, date)` set before append

Pattern in both `stocks/repository.py::insert_ohlcv` and `backend/jobs/batch_refresh.py::batch_data_refresh`. `Clean NaN Rows` admin button is now mostly redundant (kept as escape hatch for permanent gap days where Yahoo never publishes a close at all).

---

## Recommended Pipeline Execution Order

For a full data refresh (e.g., after initial setup or data cleanup):

```bash
# 1. Fetch OHLCV + company info + dividends + quarterly
PYTHONPATH=.:backend python -m backend.pipeline.runner refresh --scope india --force

# 2. Or run individual steps:
PYTHONPATH=.:backend python -m backend.pipeline.runner download       # Nifty 500 CSV
PYTHONPATH=.:backend python -m backend.pipeline.runner seed --csv ... # seed stock_master
PYTHONPATH=.:backend python -m backend.pipeline.runner bulk-download  # batch OHLCV
PYTHONPATH=.:backend python -m backend.pipeline.runner analytics --scope india
PYTHONPATH=.:backend python -m backend.pipeline.runner sentiment --scope india
PYTHONPATH=.:backend python -m backend.pipeline.runner screen         # Piotroski
PYTHONPATH=.:backend python -m backend.pipeline.runner forecast --scope india --force

# 3. Check data health
# Admin > Maintenance > Data Health > Re-scan
```

---

## Troubleshooting

### All tickers show "stale"

Pipeline likely failed during data_refresh. Check:

1. Run History for the latest pipeline run status
2. Backend logs: `./run.sh logs backend | grep ERROR`
3. yfinance rate limiting (too many concurrent requests)

### NaN rows keep reappearing

Pipeline is scheduled before market settlement. Ensure:

- India pipeline: after 08:00 IST (16h after NSE close)
- US pipeline: after 08:00 IST (1.5h after NYSE close)

### Forecast shows extreme predictions

97 tickers have broken Prophet models (parabolic histories).
Workaround: these are flagged in the Data Health dashboard.
Permanent fix: ASETPLTFRM-302 (model tuning).

### Piotroski "1 missing" ticker

Usually a ticker without quarterly results data in Iceberg
(e.g., newly listed stock). Run `fill-gaps` to attempt fetch.

---

## Iceberg Disk Reclaim — Orphan Sweep

Daily compaction reduces fragmentation but **leaves orphan parquets
on disk**: `tbl.overwrite()` creates a new snapshot referencing one
file per partition, while the prior snapshot still references the
OLD parquets. Across daily runs this grows ~2.5K parquets/day on
`ohlcv` alone. Without periodic physical reclamation, on-disk file
count and disk usage drift unbounded — the 2026-04-25 baseline
showed **96-100 % orphan ratio** across hot tables (warehouse: 16 GB
on disk vs ~4 GB live).

### Fix — `cleanup_orphans_v2()`

Shipped as ASETPLTFRM-338 (2026-04-25). Now runs **daily as part of
`iceberg_maintenance`**, immediately after each `compact_table()`
call. Uses PyIceberg 0.11.1 native `expire_snapshots()` +
`inspect.all_files()` / `inspect.all_manifests()` to compute the
authoritative referenced set, then unlinks anything not in it +
older than a 30-min mtime grace (concurrent-write race protection).

Hard safety guards:

- **Mandatory backup** at the start (fail-closed via `run_backup()`).
- **Catalog-pointer hard-exclusion** + paranoid pre-unlink assertion
  (refuses to delete the file the SQLite catalog references — the
  failure mode that gave us CLAUDE.md Rule 20).
- **Read-verify** after each table sweep — `verified=False` is
  recorded as non-fatal so the operator sees a clear warning on
  the scheduler dashboard.

### First full sweep results (2026-04-25)

| Table | Before | After | Reclaim |
|---|---:|---:|---:|
| `analysis_summary` | 938 MB / 7964 files | 3.5 MB / 25 | −99.6 % |
| `company_info` | 5.6 GB / 18 832 | 8.2 MB / 25 | −99.9 % |
| `sentiment_scores` | 2.0 GB / 30 944 | 12 MB / 1650 | −99.4 % |
| `ohlcv` | 4.0 GB / 34 116 | 97 MB / 1661 | −97.6 % |
| **Total** | **12.5 GB / 91 856** | **120 MB / 3361** | **−12.4 GB** |

Warehouse total: 16 GB → 3.6 GB (**−78 %**). Endpoint p95 sub-5 ms
after each sweep.

### Manual trigger

Pick the **Iceberg Maintenance** tile in the Admin → Scheduler
job-type picker, or run from the backend:

```bash
docker compose exec -T backend python3 -c "
from backend.maintenance.iceberg_maintenance import cleanup_orphans_v2
import json
print(json.dumps(cleanup_orphans_v2('stocks.ohlcv'),
                 indent=2, default=str))
"
```

Full algorithm + recovery procedure: [Iceberg Orphan Sweep](iceberg-orphan-sweep.md).
