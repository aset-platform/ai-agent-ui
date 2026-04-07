# Stock Data Pipeline — Design Spec

**Date:** 2026-04-02
**Status:** Draft (post-review v2)
**Author:** Abhay + Claude
**Reviewed:** 2026-04-02 — spec-panel (Wiegers, Fowler, Nygard, Adzic, Crispin)

---

## 1. Goal

Build a data pipeline module to bulk-ingest and maintain stock data
for Nifty 500 (scaling to 4000+) from dual sources: NSE (jugaad-data)
for OHLCV and yfinance for fundamentals. Paginated ingestion with
cursor tracking, manual trigger control, and scheduled daily updates.

## 2. Data Model (PostgreSQL)

> **Alembic migration required.** All new PG tables below must be
> created via `PYTHONPATH=. alembic revision --autogenerate -m "..."`.
> See `backend/db/migrations/`.

### 2.1 stock_master

Central entity for all stocks. Every other table references this
via `ticker` (Iceberg) or `id` (PG FK).

**Relationship to `stock_registry`:** The existing `stock_registry`
table (`backend/db/models/registry.py`) tracks **fetch metadata**
(last_fetch_date, date_range_start/end, total_rows, market).
`stock_master` tracks **identity + classification** (name, ISIN,
sector, tags). They are complementary, not overlapping:

| Concern | Table | Source of truth |
|---------|-------|-----------------|
| "Is this ticker known?" | `stock_master` | Pipeline seed |
| "What index is it in?" | `stock_tags` | Pipeline seed/rebalance |
| "When was data last fetched?" | `stock_registry` | Fetch jobs |
| "What date range is in Iceberg?" | `stock_registry` | Fetch jobs |

`stock_master.symbol` == `stock_registry.ticker` (both canonical,
no suffix). `stock_registry` is NOT absorbed — it retains its role
as the fetch-metadata tracker. Pipeline jobs update both:
`stock_master` for classification, `stock_registry` for fetch state.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `SERIAL PK` | Internal FK anchor |
| `symbol` | `VARCHAR(30) UNIQUE NOT NULL` | Canonical: `"RELIANCE"` (no suffix) |
| `name` | `VARCHAR(255) NOT NULL` | Full company name |
| `isin` | `VARCHAR(12) UNIQUE` | Immutable identifier, survives renames |
| `exchange` | `VARCHAR(10) NOT NULL` | `"NSE"` or `"BSE"` |
| `yf_ticker` | `VARCHAR(30) NOT NULL` | yfinance format: `"RELIANCE.NS"` |
| `nse_symbol` | `VARCHAR(30)` | jugaad-data format: `"RELIANCE"` |
| `sector` | `VARCHAR(100)` | From yfinance info, refreshed by fundamentals job |
| `industry` | `VARCHAR(100)` | From yfinance info |
| `market_cap` | `BIGINT` | Latest snapshot, updated by fundamentals job |
| `currency` | `VARCHAR(5) DEFAULT 'INR'` | `"INR"` for NSE stocks |
| `is_active` | `BOOLEAN DEFAULT true` | Soft delete for delistings |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | |

**Indexes:** `symbol`, `isin`, `yf_ticker`, `exchange`, `is_active`.

**Symbol resolution:**
- Pipeline bulk/daily jobs read `nse_symbol` for jugaad-data calls.
- Pipeline fundamentals jobs read `yf_ticker` for yfinance calls.
- Chat on-demand flow looks up master by user input, routes to
  appropriate source field.

### 2.2 stock_tags

Temporal many-to-many tagging. Tracks index membership and
classification with history.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `SERIAL PK` | |
| `stock_id` | `INTEGER FK → stock_master.id` | ON DELETE CASCADE |
| `tag` | `VARCHAR(50) NOT NULL` | `"nifty50"`, `"nifty100"`, `"nifty500"`, `"largecap"`, `"midcap"`, `"smallcap"` |
| `added_at` | `TIMESTAMPTZ DEFAULT now()` | When tag became valid |
| `removed_at` | `TIMESTAMPTZ` | NULL = currently active, set on rebalance removal |

**Unique constraint:** `(stock_id, tag, added_at)` — allows re-adding
a tag after removal (new row with new `added_at`).

**Queries enabled:**
- Current Nifty 50: `WHERE tag='nifty50' AND removed_at IS NULL`
- Historical: "Was RELIANCE in Nifty 50 on 2026-01-15?"

### 2.3 ingestion_cursor

Tracks paginated bulk load progress with resume capability.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `SERIAL PK` | |
| `cursor_name` | `VARCHAR(100) UNIQUE NOT NULL` | e.g. `"nifty500_bulk"` |
| `total_tickers` | `INTEGER NOT NULL` | Total stocks in this load |
| `last_processed_id` | `INTEGER DEFAULT 0` | Last `stock_master.id` successfully processed |
| `batch_size` | `INTEGER DEFAULT 50` | Tickers per run |
| `status` | `VARCHAR(20) DEFAULT 'pending'` | `"pending"`, `"in_progress"`, `"completed"`, `"paused"` |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | |

**Resume logic (keyset pagination):** `bulk` command reads
`last_processed_id`, queries
`stock_master WHERE id > :last_processed_id ORDER BY id LIMIT :batch_size`.
After each **individual ticker** succeeds, updates
`last_processed_id = ticker.id`. This is crash-safe: a restart
re-processes only tickers after the last committed ID, with no
skips or duplicates regardless of inserts/deletes between runs.

### 2.4 ingestion_skipped

Failed ticker log with error categorization for retry.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `SERIAL PK` | |
| `cursor_name` | `VARCHAR(100) NOT NULL` | FK → `ingestion_cursor.cursor_name` (ON DELETE CASCADE) |
| `ticker` | `VARCHAR(30) NOT NULL` | Symbol that failed |
| `job_type` | `VARCHAR(20) NOT NULL` | `"ohlcv"` or `"fundamentals"` |
| `error_message` | `VARCHAR(1000)` | Truncated to 1000 chars on insert |
| `error_category` | `VARCHAR(20) NOT NULL` | `"rate_limit"`, `"timeout"`, `"not_found"`, `"parse_error"`, `"unknown"` |
| `attempts` | `INTEGER DEFAULT 1` | Incremented on each retry |
| `resolved` | `BOOLEAN DEFAULT false` | Set true after successful retry |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `last_attempted_at` | `TIMESTAMPTZ DEFAULT now()` | |

**Retry logic:**
- Default retry: `WHERE resolved=false AND error_category IN ('rate_limit', 'timeout')`
- `--all` flag: includes all error categories
- `--ticker RELIANCE`: retry specific ticker
- On success: `resolved=true`
- On failure: `attempts += 1`, `last_attempted_at = now()`

## 3. Pipeline Module Structure

> **Location:** `backend/pipeline/` — inside `backend/` to follow
> existing project layout (all server code under `backend/`).
> `stocks/` stays as the Iceberg data-layer; `backend/pipeline/`
> is the orchestration layer that calls into both `stocks/` and
> `backend/db/`.

```
backend/pipeline/
├── __init__.py
├── config.py              — batch_size, concurrency, retry, source config
├── universe.py            — stock_master + tags CRUD (seed, query, update)
├── cursor.py              — ingestion_cursor CRUD (resume, advance, reset)
├── sources/
│   ├── __init__.py
│   ├── base.py            — abstract Source protocol
│   ├── nse.py             — jugaad-data wrapper (OHLCV only)
│   └── yfinance.py        — yfinance wrapper (OHLCV + fundamentals)
├── jobs/
│   ├── __init__.py
│   ├── ohlcv.py           — bulk + daily OHLCV ingestion
│   ├── fundamentals.py    — company_info + dividends + quarterly
│   └── seed_universe.py   — initial Nifty 500 seeding from CSV
├── router.py              — source selection (bulk→NSE, chat→race, fundamentals→yfinance)
└── runner.py              — CLI entry point + scheduler integration
```

### 3.1 Source Protocol

```python
from typing import Protocol
import pandas as pd
from datetime import date

class OHLCVSource(Protocol):
    """Fetches OHLCV data for a single ticker."""

    async def fetch_ohlcv(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Return DataFrame with columns:
        date, open, high, low, close, adj_close, volume.
        """
        ...
```

**Sync → async bridging:** Both `jugaad_data.nse.stock_df()` and
`yf.Ticker().history()` are synchronous (requests-based). Each
source wraps the sync call in `asyncio.get_running_loop().run_in_executor(None, ...)` using the default ThreadPoolExecutor.
This is the same pattern used elsewhere in the project (see
`shared/debugging/sync-async-migration-patterns`).

Similarly, `StockRepository` (Iceberg/PyIceberg) is synchronous.
All repository writes from async pipeline jobs use
`run_in_executor` — no sync Iceberg calls on the event loop.

- `NseSource` implements this using `jugaad_data.nse.stock_df()`.
  Accepts `nse_symbol` (no `.NS` suffix).
  Internally: `await loop.run_in_executor(None, stock_df, ...)`
- `YfinanceSource` implements this using `yf.Ticker().history()`.
  Accepts `yf_ticker` (with `.NS` suffix).
  Internally: `await loop.run_in_executor(None, ticker.history, ...)`
- `RacingSource` wraps both, runs concurrently via
  `asyncio.wait(tasks, return_when=FIRST_COMPLETED)`.
  On first success: cancels pending tasks and awaits them
  (ensures clean connection teardown). If both fail: raises
  the first exception with both errors in the message.

### 3.2 Source Router

```python
def get_ohlcv_source(context: str) -> OHLCVSource:
    if context in ("bulk", "daily"):
        return NseSource()
    if context == "chat":
        return RacingSource(NseSource(), YfinanceSource())

def get_fundamentals_source() -> YfinanceSource:
    return YfinanceSource()  # always yfinance
```

### 3.3 OHLCV Job

**Bulk mode:**
1. Read cursor → `last_processed_id`
2. Query `stock_master WHERE id > :last_processed_id ORDER BY id LIMIT :batch_size`
3. Semaphore-controlled concurrent fetch (max 10, via ThreadPoolExecutor)
4. Per ticker:
   - Check `stock_registry` for existing date range
   - If fresh (date_range_end >= yesterday): skip
   - If partial: delta fetch from `date_range_end + 1`
   - If new: full fetch (10 years)
5. Write to Iceberg via `repository.insert_ohlcv()` (dedup on `(ticker, date)`)
6. Update `stock_registry` via existing `upsert_registry()`
7. **Advance cursor** `last_processed_id = ticker.id` after each
   successful ticker (not after the whole batch — crash-safe)
8. On failure: log to `ingestion_skipped`, continue batch

**Idempotency contract:** A restart after crash re-processes only
tickers with `id > last_processed_id`. Iceberg `insert_ohlcv()`
deduplicates on `(ticker, date)`, so re-inserting already-written
rows is a no-op. This makes bulk runs safe to retry at any point.

**Daily mode:**
1. Query `stock_master WHERE is_active=true` — but only tickers
   that have been bulk-loaded at least once (i.e., exist in
   `stock_registry` with `date_range_end IS NOT NULL`)
2. Delta fetch today's data via `NseSource`
3. Same Iceberg + registry write path
4. Failed tickers logged to skipped table

### 3.4 Fundamentals Job

1. Query `stock_master WHERE id > :last_processed_id ORDER BY id LIMIT :batch_size`
2. Per ticker (via `yf_ticker`):
   - `yf.Ticker(ticker).info` → Iceberg `company_info`
   - `yf.Ticker(ticker).dividends` → Iceberg `dividends`
   - `yf.Ticker(ticker).quarterly_income_stmt` etc. → Iceberg `quarterly_results`
   - Update `stock_master.sector`, `industry`, `market_cap` from info
3. Uses its own cursor (`nifty500_fundamentals`) — independent of OHLCV cursor
4. Fundamentals refreshed weekly, not daily

### 3.5 Concurrency Model

- **Batch level:** 50 tickers per run (configurable via `--batch-size`)
- **Within batch:** `asyncio.Semaphore(10)` gates concurrent fetches.
  Each fetch runs the sync HTTP call in `run_in_executor(None, ...)`
  (default ThreadPoolExecutor, OS threads — true parallelism for
  I/O-bound sync libraries).
- **Per-request delay:** 0.5s between individual HTTP requests
  (respect NSE/yfinance rate limits)
- **Retry per ticker:** 3 attempts, exponential backoff (1s, 2s, 4s)
- **Failure isolation:** failed tickers logged to `ingestion_skipped`,
  do not block the batch
- **Iceberg writes:** also via `run_in_executor` — PyIceberg is sync.
  Writes are serialized per ticker (no concurrent writes to the same
  Iceberg table from multiple threads).

## 4. CLI Interface

```bash
# --- Universe management ---
# Seed from CSV (first time)
python -m backend.pipeline.runner seed --csv data/universe/nifty500.csv

# Update universe (rebalance, expansion)
python -m backend.pipeline.runner seed --csv data/universe/nse_all_eq.csv --update

# --- Bulk ingestion (manual, paginated) ---
python -m backend.pipeline.runner bulk --batch-size 50
python -m backend.pipeline.runner bulk --batch-size 100  # larger batch

# --- Daily delta (scheduled or manual) ---
python -m backend.pipeline.runner daily

# --- Status & monitoring ---
python -m backend.pipeline.runner status          # cursor position + progress
python -m backend.pipeline.runner skipped         # list failed tickers + reasons

# --- Retry failed tickers ---
python -m backend.pipeline.runner retry           # transient errors only
python -m backend.pipeline.runner retry --all     # all errors
python -m backend.pipeline.runner retry --ticker RELIANCE  # specific ticker

# --- Cursor management ---
python -m backend.pipeline.runner reset           # restart from id 0
```

## 5. Seed Data Format

### 5.1 CSV Structure

File: `data/universe/nifty500.csv`

```csv
symbol,name,isin,exchange,series,sector,industry,tags
RELIANCE,Reliance Industries Limited,INE002A01018,NSE,EQ,Energy,Oil Gas & Consumable Fuels,nifty50|nifty100|nifty500|largecap
TCS,Tata Consultancy Services Limited,INE467B01029,NSE,EQ,Information Technology,IT Services & Consulting,nifty50|nifty100|nifty500|largecap
PERSISTENT,Persistent Systems Limited,INE262H01013,NSE,EQ,Information Technology,IT Services & Consulting,nifty500|midcap
```

- Source: NSE publishes Nifty 500 constituent list as Excel/PDF.
  Manual step: download from
  `https://www.nse1.com/products/content/equities/indices/`
  and convert to CSV. Nifty rebalances quarterly (Mar/Jun/Sep/Dec)
  — re-run `seed --update` after each rebalance.
- Tags are `|` delimited — one stock can have multiple tags.
- `series=EQ` filters equity shares only.
- ISIN is the immutable ID — survives ticker symbol renames.

### 5.2 Seed Job Behavior

**First run (`seed`):**
1. Parse CSV
2. Insert each row into `stock_master` (derive `yf_ticker` = `{symbol}.NS`,
   `nse_symbol` = `symbol`)
3. Insert tags into `stock_tags`
4. Create `ingestion_cursor` at offset 0
5. Print summary

**Update run (`seed --update`):**
- New tickers: full insert into `stock_master` + `stock_tags`
- Existing tickers: update `sector`, `industry` if changed
- Tags: compare new vs existing **only for tickers present in
  the CSV** (avoids false tag-removal when CSV is partial):
  - New tag on existing stock → insert with `added_at=now()`
  - Tag missing from new CSV for a ticker IN the CSV → set
    `removed_at=now()` (soft remove)
  - Tickers NOT in the CSV → tags untouched
- No OHLCV/fundamentals changes
- Creates new cursor for any un-ingested tickers

## 6. Integration with Existing System

### 6.1 Chat Flow (stock_data_tool.py)

Current flow: user asks about a ticker → yfinance fetch → Iceberg.
Entry point: `backend/tools/stock_data_tool.py:fetch_stock_data()`.

Enhanced flow — changes inside `fetch_stock_data()`:
1. User asks about ticker (e.g., "analyse RELIANCE.NS")
2. **Normalize:** strip `.NS`/`.BO` suffix → canonical symbol
3. **Lookup `stock_master`** by `symbol` (or `yf_ticker` if
   user passed suffixed form)
4. If found AND `stock_registry.date_range_end >= yesterday`:
   serve directly from Iceberg (no fetch needed)
5. If found BUT stale: delta fetch via `NseSource` (using
   `stock_master.nse_symbol`)
6. If not found in `stock_master`: fall back to current
   on-demand yfinance flow (existing code path unchanged).
   `RacingSource` is used only when both sources are plausible
   (i.e., ticker looks like an Indian stock with `.NS` mapping).
7. Fundamentals: always `yf_ticker` via yfinance (existing flow)

**`.NS` suffix logic:** `stock_master` stores canonical symbol
(no suffix). `yf_ticker` column stores the suffixed form.
`fetch_stock_data()` already normalizes input via
`validate_ticker()` — the new lookup adds a `stock_master`
check before falling through to the existing yfinance path.

### 6.2 Repository Layer

`stocks/repository.py` is unchanged. Pipeline calls existing methods:
- `insert_ohlcv(ticker, df)` — existing deduplication logic
- `insert_company_info(ticker, info_dict)` — existing
- `insert_dividends(ticker, df)` — existing
- `insert_quarterly_results(ticker, df)` — existing

`stocks.registry` (PG) continues to track per-ticker fetch metadata.

### 6.3 Scheduling

Daily OHLCV job integrates with existing `scheduled_jobs`
infrastructure in PostgreSQL. Runs post-market (~4:30 PM IST for NSE).

### 6.4 Observability

Pipeline jobs emit structured log events compatible with the
existing `ObservabilityCollector` pattern:

| Metric | Where | Granularity |
|--------|-------|-------------|
| `pipeline.batch.started` | Log | Per batch |
| `pipeline.ticker.fetched` | Log | Per ticker (source, duration_ms) |
| `pipeline.ticker.skipped` | Log | Per ticker (reason: fresh/error) |
| `pipeline.ticker.failed` | Log + `ingestion_skipped` | Per ticker (category, error) |
| `pipeline.batch.completed` | Log | Per batch (processed, skipped, failed, duration_s) |
| `pipeline.cursor.progress` | Log | Per batch (cursor_name, last_processed_id, total, pct) |

All logs use `logging.getLogger(__name__)` (no bare `print()`).
The `status` CLI command reads `ingestion_cursor` and
`ingestion_skipped` to show a progress summary.

## 7. Non-Functional Requirements

### 7.1 Throughput Targets

| Scenario | Target | Basis |
|----------|--------|-------|
| Bulk OHLCV (50 tickers × 10yr) | < 10 min per batch | ~12s/ticker with jugaad-data |
| Daily OHLCV (500 tickers × 1 day) | < 15 min total | Delta fetch is fast |
| Fundamentals (50 tickers) | < 20 min per batch | yfinance info is slow (~20s/ticker) |

### 7.2 Rate Limit Budget

| Source | Known limit | Pipeline budget |
|--------|------------|-----------------|
| jugaad-data (NSE scraper) | ~2 req/s sustained | Semaphore(10) + 0.5s delay ≈ 2 req/s |
| yfinance | ~2000 req/hr (undocumented) | 50 tickers/batch × ~3 calls/ticker = 150 req/batch |

On HTTP 429: back off the **entire batch** for 60s, then resume.
Three consecutive 429s → pause cursor, log, exit.

### 7.3 Storage Growth Estimate

| Data | Per ticker | 500 tickers | 4000 tickers |
|------|-----------|-------------|--------------|
| OHLCV (10yr, ~2500 rows) | ~200 KB parquet | ~100 MB | ~800 MB |
| Company info (1 snapshot) | ~2 KB | ~1 MB | ~8 MB |
| Quarterly results (~40 quarters) | ~10 KB | ~5 MB | ~40 MB |
| Dividends (~20 entries) | ~1 KB | ~500 KB | ~4 MB |
| **Total initial load** | | **~107 MB** | **~852 MB** |
| **Daily delta (1 row/ticker)** | ~80 bytes | ~40 KB/day | ~320 KB/day |

Iceberg metadata overhead: ~5% additional. Well within single-disk.

### 7.4 Memory Budget

Peak during bulk: 10 concurrent tickers × ~2500 rows × ~80 bytes
≈ 2 MB DataFrame data. Plus pandas/pyarrow overhead ≈ **< 50 MB
peak**. Not a concern.

## 8. Storage Rules (unchanged from v1)

| Store | What | Pattern |
|-------|------|---------|
| **PostgreSQL** | `stock_master`, `stock_tags`, `ingestion_cursor`, `ingestion_skipped`, `stocks.registry` | CRUD-heavy, relational, mutable |
| **Iceberg** | `ohlcv`, `company_info`, `dividends`, `quarterly_results` + all other existing tables | Append-only with scoped delete |

No changes to existing Iceberg schemas or tables.

## 9. Error Handling

### 9.1 Source-Level Classification

Each source wraps exceptions into categories:

| Category | Examples | Retryable |
|----------|----------|-----------|
| `rate_limit` | HTTP 429, jugaad-data throttle | Yes (auto) |
| `timeout` | Connection timeout, read timeout | Yes (auto) |
| `not_found` | Ticker delisted, invalid symbol | No (manual) |
| `parse_error` | Unexpected response format, NaN data | No (manual) |
| `unknown` | Unhandled exceptions | No (manual) |

### 9.2 Retry Strategy

- **Within a job:** 3 attempts per ticker, exponential backoff (1s, 2s, 4s)
- **Across runs:** `retry` CLI command re-processes `ingestion_skipped`
- **Rate limit handling:** on 429, back off for the entire batch
  (not just the one ticker) to avoid cascading failures

## 10. Future Considerations (Out of Scope)

- **Shareholding patterns** — NSE source, new Iceberg table. Add later.
- **BSE integration** — `exchange="BSE"`, `yf_ticker="{symbol}.BO"`.
  Model supports it, pipeline routing not implemented yet.
- **International stocks** — `stock_master` model accommodates
  non-INR currencies and non-NSE exchanges. No pipeline work now.
- **Real-time intraday** — different architecture (WebSocket/streaming).
  Not part of this pipeline.
