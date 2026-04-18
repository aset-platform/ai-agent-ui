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
