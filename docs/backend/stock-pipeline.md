# Stock Data Pipeline — Usage Guide

End-to-end guide to seeding, ingesting, and maintaining stock data
using the `backend/pipeline` module.

---

## Prerequisites

### 1. PostgreSQL running with migrations applied

```bash
# Start services (if not already)
docker compose up -d

# Apply Alembic migrations (creates stock_master, stock_tags,
# ingestion_cursor, ingestion_skipped tables)
PYTHONPATH=. alembic revision --autogenerate -m "add pipeline tables"
PYTHONPATH=. alembic upgrade head
```

Verify the tables exist:

```bash
docker compose exec postgres psql -U asetuser -d asetdb -c "\dt"
```

You should see `stock_master`, `stock_tags`, `ingestion_cursor`,
and `ingestion_skipped` in the list alongside existing tables.

### 2. Python virtualenv activated

```bash
source ~/.ai-agent-ui/venv/bin/activate
```

### 3. Required Python packages

```bash
pip install jugaad-data yfinance
```

Both should already be in `requirements.txt`. If not, add them.

### 4. Iceberg catalog initialised

```bash
python stocks/create_tables.py
```

Only needed once. Ensures `stocks.ohlcv`, `stocks.company_info`,
etc. exist.

---

## Step 0 — Download Nifty 500 Constituents (optional)

Download the full Nifty 500 list from NSE with auto-tagging:

```bash
python scripts/download_nifty500.py
```

This fetches live index data for Nifty 50, 100, and 500, merges
them into `data/universe/nifty500.csv` with pipe-delimited tags
(nifty50, nifty100, nifty500, largecap, midcap).

Output: 499 stocks with ISIN, industry, and index tags.

Skip this step if you want to start with the 10-stock sample.

---

## Step 1 — Seed the Stock Universe

Populate `stock_master` and `stock_tags` from a CSV file.

### Using the sample CSV (10 Nifty 50 stocks)

```bash
PYTHONPATH=. python -m backend.pipeline.runner seed \
    --csv data/universe/nifty500_sample.csv
```

Expected output:

```
Seed complete: inserted=10 updated=0 skipped=0
    tags_added=40 tags_removed=0 errors=0
```

### Verify in PostgreSQL

```bash
docker compose exec postgres psql -U asetuser -d asetdb \
    -c "SELECT id, symbol, yf_ticker, nse_symbol, exchange
        FROM stock_master ORDER BY id;"
```

Expected: 10 rows (RELIANCE, TCS, HDFCBANK, etc.)

```bash
docker compose exec postgres psql -U asetuser -d asetdb \
    -c "SELECT sm.symbol, st.tag
        FROM stock_tags st
        JOIN stock_master sm ON sm.id = st.stock_id
        WHERE st.removed_at IS NULL
        ORDER BY sm.symbol, st.tag
        LIMIT 20;"
```

Expected: tags like `largecap`, `nifty50`, `nifty100`, `nifty500`
per stock.

### Check the cursor was created

```bash
PYTHONPATH=. python -m backend.pipeline.runner status \
    --cursor nifty500_sample_bulk
```

Expected:

```
Cursor: nifty500_sample_bulk
Status: pending
Progress: 0/10 (0.0%)
Batch size: 50
Skipped (unresolved): 0
```

---

## Step 2 — Bulk OHLCV Ingestion

### Recommended: yfinance batch download (fast)

Downloads all tickers concurrently via `yf.download()` — 499
stocks in ~2 minutes:

```bash
PYTHONPATH=.:backend python scripts/bulk_download_ohlcv.py
```

Options:

```bash
# Limit to first N tickers (for testing)
PYTHONPATH=.:backend python scripts/bulk_download_ohlcv.py --batch 50

# Specific tickers only
PYTHONPATH=.:backend python scripts/bulk_download_ohlcv.py --tickers RELIANCE,TCS,INFY

# Different history period
PYTHONPATH=.:backend python scripts/bulk_download_ohlcv.py --period 5y
```

Downloads in chunks of 100, writes each to Iceberg, updates
`stock_registry`, and marks the cursor as completed.

### Alternative: CLI runner (slower, crash-safe cursor)

Uses jugaad-data (NSE scraper), one ticker at a time with
crash-safe cursor. Better for daily deltas, but slow for bulk
(~12s/ticker, throttled by NSE).

```bash
PYTHONPATH=.:backend python -m backend.pipeline.runner bulk \
    --cursor nifty500_bulk
```

This processes up to 50 tickers per batch. Run multiple times
to complete all 499. Crash-safe: restarts from last successful
ticker.

Expected log lines:

```
pipeline.batch.started cursor=nifty500_bulk batch_size=50 last_processed_id=0
pipeline.ticker.fetched ticker=RELIANCE source=nse duration_ms=12000
pipeline.ticker.fetched ticker=TCS source=nse duration_ms=11500
...
pipeline.batch.completed cursor=nifty500_sample_bulk processed=10 skipped=0 failed=0 duration_s=45.20
```

### Check progress

```bash
PYTHONPATH=. python -m backend.pipeline.runner status \
    --cursor nifty500_sample_bulk
```

Expected:

```
Cursor: nifty500_sample_bulk
Status: completed
Progress: 10/10 (100.0%)
Batch size: 50
Skipped (unresolved): 0
```

### Verify data in Iceberg

Open a Python shell:

```python
from stocks.repository import StockRepository
repo = StockRepository()
df = repo.get_ohlcv("RELIANCE")
print(f"Rows: {len(df)}, Range: {df['date'].min()} to {df['date'].max()}")
```

Expected: ~2500 rows spanning ~10 years.

### Verify stock_registry updated

```bash
docker compose exec postgres psql -U asetuser -d asetdb \
    -c "SELECT ticker, total_rows, date_range_start, date_range_end
        FROM stock_registry ORDER BY ticker;"
```

---

## Step 3 — Bulk Fundamentals Ingestion

Fetch company info and dividends from yfinance. Uses a separate
cursor (`nifty500_fundamentals`).

```bash
PYTHONPATH=. python -m backend.pipeline.runner fundamentals
```

First run auto-creates the `nifty500_fundamentals` cursor. Each
ticker fetches:
- `yf.Ticker().info` → Iceberg `company_info` + updates
  `stock_master.sector`, `industry`, `market_cap`
- `yf.Ticker().dividends` → Iceberg `dividends`

Expected log lines:

```
pipeline.ticker.fetched ticker=RELIANCE source=yfinance duration_ms=5200
...
Fundamentals complete: cursor=nifty500_fundamentals status=completed processed=10 failed=0
```

### Verify stock_master updated

```bash
docker compose exec postgres psql -U asetuser -d asetdb \
    -c "SELECT symbol, sector, industry, market_cap
        FROM stock_master ORDER BY symbol;"
```

Sector/industry/market_cap should now be populated from yfinance.

---

## Step 4 — Daily Delta (after initial bulk)

Run this daily post-market (~4:30 PM IST) to fetch today's data
for all bulk-loaded stocks.

> **In production this runs automatically as the first step of
> the scheduled `India Daily Pipeline` / `USA Daily Pipeline`
> (cron 08:00 / 08:15 IST, Tue–Sat).** See "Scheduled daily
> chain" below.

```bash
PYTHONPATH=. python -m backend.pipeline.runner daily
```

Only processes stocks that:
1. Are active in `stock_master`
2. Have `date_range_end` in `stock_registry` (bulk-loaded)

Expected: delta fetch of 1 row per ticker (today's candle).

```
Daily complete: processed=10 skipped=0 failed=0
```

If run on a weekend/holiday, most tickers will be `skipped`
(date_range_end already >= yesterday).

### Scheduled daily chain (6 steps, ~12 min total)

In production both `India Daily Pipeline` (cron `08:00 IST`) and
`USA Daily Pipeline` (cron `08:15 IST`) run Tue–Sat. Each is a
6-step chain:

| # | Job type | Duration | What it does |
|---|---|---|---|
| 1 | `data_refresh` | ~5 min | Bulk yfinance OHLCV + company_info + dividends + quarterly |
| 2 | `compute_analytics` | ~45 s | Technical indicators + analysis_summary |
| 3 | `run_sentiment` | ~3.5 min | FinBERT scoring with dormancy-aware classification (top-50 by `market_cap`) |
| 4 | `run_piotroski` | ~2 s | F-Score from quarterly_results |
| 5 | `recommendation_outcomes` | ~15 s | 30/60/90d outcome checkpoints |
| 6 | `iceberg_maintenance` | ~2 min | Backup-then-compact for hot tables (fail-closed) |

**Container TZ matters**: backend runs `TZ=Asia/Kolkata` so cron
strings match wall-clock IST. The `schedule` library uses local
time — pre-Apr-23 the container was UTC and jobs fired 5.5h late.

**Catchup defaults to OFF**: `scheduler_catchup_enabled=False` —
startup catchup of "missed" jobs was silently pulling mid-day
partial data. Opt-in via `SCHEDULER_CATCHUP_ENABLED=true`.

**OHLCV upsert is NaN-replaceable** (Apr 23+): the dedup query
filters non-NaN closes AND scoped pre-deletes NaN rows for the
to-be-inserted `(ticker, date)` set before append. So a stuck
NaN-close row from a Yahoo upstream gap doesn't block future
re-fetches as "duplicate." Pattern in both `insert_ohlcv` (this
module) and `batch_data_refresh`.

---

## Step 5 — Handling Failures

### View failed tickers

```bash
PYTHONPATH=. python -m backend.pipeline.runner skipped \
    --cursor nifty500_sample_bulk
```

Output (if any):

```
Ticker       Job            Category         Attempts  Last Attempt
SYMBOL1      ohlcv          rate_limit              2  2026-04-02 15:30
SYMBOL2      ohlcv          timeout                 1  2026-04-02 15:31
```

### Retry transient errors (rate_limit + timeout)

```bash
PYTHONPATH=. python -m backend.pipeline.runner retry \
    --cursor nifty500_sample_bulk
```

### Retry ALL errors (including not_found, parse_error)

```bash
PYTHONPATH=. python -m backend.pipeline.runner retry \
    --cursor nifty500_sample_bulk --all
```

### Retry a specific ticker

```bash
PYTHONPATH=. python -m backend.pipeline.runner retry \
    --cursor nifty500_sample_bulk --ticker RELIANCE
```

---

## Step 6 — Re-seeding / Universe Update

When Nifty rebalances (quarterly: Mar/Jun/Sep/Dec), download the
updated constituent list and re-seed:

```bash
PYTHONPATH=. python -m backend.pipeline.runner seed \
    --csv data/universe/nifty500_updated.csv --update
```

The `--update` flag:
- New tickers → full insert into `stock_master` + `stock_tags`
- Existing tickers → updates `sector`, `industry` if changed
- Tags reconciled **only for tickers in the CSV** (no false
  removals for tickers not in the file)
- Missing tags → soft-removed (`removed_at` set, not deleted)

After re-seeding, run `bulk` again to ingest OHLCV for any new
tickers.

---

## Step 7 — Reset Cursor (if needed)

To re-process all tickers from scratch:

```bash
# With confirmation prompt
PYTHONPATH=. python -m backend.pipeline.runner reset \
    --cursor nifty500_sample_bulk

# Skip prompt
PYTHONPATH=. python -m backend.pipeline.runner reset \
    --cursor nifty500_sample_bulk --yes
```

Then run `bulk` again.

---

## CLI Quick Reference

All commands: `PYTHONPATH=. python -m backend.pipeline.runner <cmd>`

| Command | Args | Description |
|---------|------|-------------|
| `seed` | `--csv PATH` `[--update]` | Seed/update stock universe from CSV |
| `download` | (none) | Download Nifty 500 constituents from NSE (saves to `data/universe/nifty500.csv`) |
| `bulk` | `[--cursor NAME]` `[--batch-size N]` | Run one OHLCV bulk batch via pipeline cursor (crash-safe) |
| `bulk-download` | `[--batch N]` `[--tickers SYM,...]` `[--period 10y]` | Batch yfinance OHLCV download (fast, recommended for initial load) |
| `fundamentals` | `[--cursor NAME]` `[--batch-size N]` | Run one fundamentals batch |
| `daily` | (none) | Daily OHLCV delta (yfinance) |
| `fill-gaps` | `[--cursor NAME]` | Fill OHLCV gaps for tickers missing recent data |
| `status` | `[--cursor NAME]` | Show cursor progress + skipped count |
| `skipped` | `[--cursor NAME]` | List failed tickers with details |
| `retry` | `[--cursor NAME]` `[--all]` `[--ticker SYM]` | Retry via NSE (jugaad-data fallback) |
| `correct` | `--ticker SYM` | Re-fetch from NSE for a specific ticker |
| `reset` | `[--cursor NAME]` `[--yes]` | Reset cursor to position 0 |

Default cursor: `nifty500_sample_bulk` (OHLCV) /
`nifty500_fundamentals` (fundamentals).

### Source Strategy

The pipeline uses **yfinance as the primary source** for bulk and daily
operations (fast, reliable, batch-capable). NSE via jugaad-data serves
as a **fallback for retries and corrections** (different source avoids
repeat failures). Chat uses a **racing strategy** where both sources
compete and the fastest wins.

| Context | Source | Rationale |
|---------|--------|-----------|
| `bulk` / `bulk-download` / `daily` | yfinance | Fast, reliable, batch-capable |
| `retry` | jugaad-data (NSE) | Different source for failed tickers |
| `correct` | jugaad-data (NSE) | Authoritative NSE data for corrections |
| `fill-gaps` | yfinance | Delta fetch for missing date ranges |
| `chat` | Racing (NSE vs yfinance) | Fastest wins |
| `fundamentals` | yfinance | Only source for .info/.dividends |

---

## Chat Integration

After bulk ingestion, the chat agent (`fetch_stock_data` tool)
automatically benefits:

1. User asks "analyse RELIANCE"
2. Tool looks up `stock_master` → finds canonical symbol
3. Checks `stock_registry` → data is fresh (date_range_end >=
   yesterday)
4. Serves directly from Iceberg → **zero network calls**
5. If data is stale → delta fetch via jugaad-data
6. If ticker not in `stock_master` → falls back to existing
   yfinance flow (no regression)

No manual steps needed. Pipeline data is used transparently.

---

## Error Categories

| Category | Examples | Auto-retryable |
|----------|----------|----------------|
| `rate_limit` | HTTP 429, NSE throttle | Yes |
| `timeout` | Connection/read timeout | Yes |
| `not_found` | Delisted ticker, invalid symbol | No (manual) |
| `parse_error` | Bad response format, NaN data | No (manual) |
| `unknown` | Unhandled exceptions | No (manual) |

The `retry` command retries `rate_limit` + `timeout` by default.
Use `--all` to include all categories.

---

## Configuration

Constants in `backend/pipeline/config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `DEFAULT_BATCH_SIZE` | 50 | Tickers per bulk batch |
| `MAX_CONCURRENCY` | 10 | Concurrent fetches (semaphore) |
| `REQUEST_DELAY_S` | 0.5 | Delay between HTTP requests |
| `MAX_RETRIES` | 3 | Retry attempts per ticker |
| `RETRY_BACKOFF_BASE_S` | 1.0 | Base backoff (1s, 2s, 4s) |
| `RATE_LIMIT_BACKOFF_S` | 60.0 | Batch-level 429 backoff |
| `MAX_CONSECUTIVE_429` | 3 | 429s before pausing cursor |
| `DEFAULT_HISTORY_YEARS` | 10 | Full fetch history depth |

---

## Troubleshooting

### "Cursor not found"

Run `seed` first — it creates the cursor automatically.

### "No module named jugaad_data"

```bash
pip install jugaad-data
```

### Bulk job processes 0 tickers

Cursor is already `completed`. Check with `status`. Use `reset`
to start over, or `seed --update` with a new CSV to add tickers.

### All tickers showing as "skipped" in bulk

Data is already fresh (date_range_end >= yesterday). This is
normal if you ran bulk recently.

### Fundamentals cursor not found (first run)

This is fine — `run_fundamentals` auto-creates the cursor on first
run by counting active stocks in `stock_master`.

### "Iceberg write failed"

Check that `stocks/create_tables.py` has been run and the Iceberg
catalog is accessible. Verify `~/.ai-agent-ui/data/iceberg/` exists.

### Rate limit errors from NSE

jugaad-data scrapes NSE, which throttles at ~2 req/s. The pipeline
respects this with `REQUEST_DELAY_S=0.5` and `Semaphore(10)`.
If you still hit 429s, the `RateLimitTracker` backs off 60s per
hit and pauses after 3 consecutive. Failed tickers are logged for
later `retry`.
