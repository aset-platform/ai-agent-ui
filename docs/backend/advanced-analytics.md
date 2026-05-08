# Advanced Analytics — `/v1/advanced-analytics/`

Sprint 9 (Apr-29 → May-02 2026, ASETPLTFRM-340 epic) shipped a
new top-level page **`/advanced-analytics`** for pro and
superuser accounts. Seven NSE-bhavcopy-driven scan reports
sit behind the same shared table component, mirroring the
§5.4 tabular-page-pattern hardened in Sprints 7-8 (Screener,
ScreenQL, RecommendationHistory, Admin Users).

## Route

`/v1/advanced-analytics/<report>` — 7 endpoints.

| Path | Filter (server-side) | Default sort | Cap |
|---|---|---|---|
| `/current-day-upmove` | `today_x_vol > 1 AND current_dpc > avg_20d_dpc` | `today_x_vol DESC` | — |
| `/previous-day-breakout` | `today_x_vol > 1` | `today_x_vol DESC` | — |
| `/mom-volume-delivery` | `x_vol_20d > 1 OR x_dv_20d > 1` | `x_dv_20d DESC` | — |
| `/wow-volume-delivery` | `x_vol_10d > 1 OR x_dv_10d > 1` | `x_dv_10d DESC` | — |
| `/two-day-scan` | `today_x_vol > 1 AND prev_day_x_vol > 1` | `today_x_vol DESC` | — |
| `/three-day-scan` | `today_x_vol > 1 AND prev_day_x_vol > 1` | `today_x_vol DESC` | — |
| `/top-50-delivery-by-qty` | `today_dv > 0` | `today_dv DESC` | top 50 |

### Query params

| Param | Type | Default | Notes |
|---|---|---|---|
| `page` | `int ≥ 1` | `1` | Server-side pagination |
| `page_size` | `1 ≤ int ≤ 200` | `25` | Hard cap 200 |
| `sort_key` | `str \| null` | Default sort per report | Any field on `AdvancedRow` |
| `sort_dir` | `"asc" \| "desc"` | `"desc"` | Pattern-validated |
| `market` | `"all" \| "india" \| "us"` | `"all"` (UI default `india`) | Pre-filter via `detect_market()` |
| `ticker_type` | `"all" \| "stock" \| "etf"` | `"all"` (UI default `stock`) | Pre-filter via `stock_registry.ticker_type` |
| `search` | `str` (≤ 20 chars) | `""` | Case-insensitive substring match on ticker. Debounced 300 ms client-side |
| `tech` | `str` (≤ 200, regex `^[a-z0-9_,]*$`) | `""` | Comma-joined sorted CSV of **technical** filter keys (e.g. `golden_recent,price_gt_sma50`). Allowlist enforced server-side; unknown key → 400. AND within bundle (Sprint 9 follow-on) |
| `fund` | `str` (≤ 200, regex `^[a-z0-9_,]*$`) | `""` | Comma-joined sorted CSV of **fundamentals** filter keys (e.g. `fscore_ge_7,debt_lt_0_5`). Same shape + validation as `tech`. AND across bundles |

**Filter bundles** — the canonical allowlist + predicates live in
`backend/advanced_analytics_filters.py` (9 technical + 8 fundamentals).
Frontend mirror: `frontend/components/advanced-analytics/filterCatalogs.ts`.
Drift between the two is caught by `tests/backend/test_filter_catalog_sync.py`
(CI gate). NaN/None on a row's source field excludes that row from the result —
the existing stale-chip surface explains *why* upstream data is missing.

**`as_of` anchor** (commit `8e16144`): every report
caps both OHLCV + delivery loads to `MAX(date) FROM
nse_delivery` so volume + delivery describe the same
trading session. Cached 60 s in Redis. Naturally
handles weekends, public holidays, long weekends, and
OHLCV-vs-delivery skew.

### Response shape

```jsonc
{
  "rows": [/* AdvancedRow superset, ~52 nullable fields */],
  "total": 50,
  "page": 1,
  "page_size": 25,
  "stale_tickers": [
    {"ticker": "AAPL", "reason": "missing_delivery"}
  ]
}
```

## Auth + scope

- **`pro_or_superuser` guard** (existing dependency in
  `auth/dependencies.py`, also used by §5.7 scoped admin
  endpoints) — general role gets 403.
- **`_scoped_tickers(user, "discovery")`** (from
  `backend/insights_routes.py`):
    - **Pro / superuser** → full universe
      (`ticker_type IN ('stock', 'etf')`) ∪ watchlist ∪ holdings.
    - **General** never reaches here (403 above), but if it
      did the helper falls back to watchlist ∪ holdings.

## Architecture

**Single-batched DuckDB read per Iceberg table** — no
per-ticker loops (CLAUDE.md §4.1 #1). Per request the
endpoint fans out 8 reads:

```
ohlcv               last 25 trading days per ticker
nse_delivery        last 25 trading days per ticker
technical_indicators latest row per ticker (rsi_14, sma_50, sma_200)
fundamentals_snapshot latest row per ticker (3y/5y CAGR, ROCE, YoY)
promoter_holdings   latest quarter per ticker
corporate_events    latest event per ticker
piotroski_scores    latest score per ticker
company_info        latest snapshot per ticker
```

**EMV-14** is computed inline via
`backend.tools._analysis_indicators.compute_emv_14()` — no
Iceberg column (Sprint 9 AA-1 deviation; the
`technical_indicators` table is the dead persistence path
per system-overview).

## Caching

- Key (paginated): `cache:advanced_analytics:<report>:{user_id}:m{market}:t{ticker_type}:q{search}:ftech{tech_csv}:ffund{fund_csv}:dt{as_of}:p{page}:s{sort_key|default}:{sort_dir}:ps{page_size}` (per-user — Sprint 7 cross-user leak fix, §5.9).
- Key (export): same prefix, no `:p`/`:ps`, plus `:export:{cols_csv}` suffix.
- TTL: `TTL_STABLE` (300 s).
- Invalidation: `_CACHE_INVALIDATION_MAP` glob `cache:advanced_analytics:*` is fired by every Iceberg write to `nse_delivery`, `promoter_holdings`, `corporate_events`, `fundamentals_snapshot`, `ohlcv`, `technical_indicators` (CLAUDE.md §5.13). Single glob covers both paginated and export cache slots.

## Export endpoint

`GET /v1/advanced-analytics/<report>/export` — streams the **full
filtered set** as CSV. Reuses the same compute pipeline as the
paginated endpoint minus pagination.

| Param | Type | Notes |
|---|---|---|
| `sort_key`, `sort_dir`, `market`, `ticker_type`, `search`, `tech`, `fund` | — | Same shape as the paginated endpoint |
| `columns` | `str` (≤ 2000, regex `^[a-z0-9_,]*$`) | Sorted CSV of column keys to project. Validated against `_CSV_COLUMN_LABELS`. Empty → safe defaults (`ticker, today_ltp, sma_50, sma_200, rsi`). `ticker` is always prepended if missing. |
| `fmt` | `"csv"` | Reserved for future `json` / `xlsx` |

- **Hard cap**: `_MAX_EXPORT_ROWS = 10_000`. Over-cap → `413` with
  `detail` containing "tighten filters". Frontend mirrors the cap
  via `FILTER_EXPORT_ROW_CAP` and disables the CSV button when
  `total > cap`, surfacing the same hint as a tooltip.
- **Sort**: defaults to `_DEFAULT_SORT[report]` when `sort_key`
  is empty; mirrors the paginated path so the export and view
  agree on row order.
- **Streaming**: `text/csv; charset=utf-8` in 64 KB chunks via
  `StreamingResponse`. Header row uses `_CSV_COLUMN_LABELS`.
  `Content-Disposition: attachment; filename="advanced-analytics-{report}-{YYYYMMDD}.csv"`.
- **`top-50-delivery-by-qty` cap** is honoured in the export
  (matches the paginated semantic contract) — first 50 rows after
  the default sort, not the first 50 rows in cache order.

## Stale-ticker transparency chip

Per CLAUDE.md §5.5 each response includes
`stale_tickers: list[StaleTicker]` for any ticker with
missing/NaN required input. Reasons:

| Reason | Trigger |
|---|---|
| `nan_close` | No close price for the latest 25 trading days |
| `missing_delivery` | No row in `nse_delivery` (US stocks, weekends, holidays) |
| `missing_quarterly` | No `fundamentals_snapshot` row |
| `missing_promoter` | No `promoter_holdings` row |

Frontend renders the count as an amber chip in the panel-
title row via `frontend/components/common/StaleTickerChip.tsx`
(extracted from `PLTrendWidget` in Sprint 9 AA-11).

## Frontend

- `/advanced-analytics` route — RSC + `<Suspense fallback={<h1>Advanced Analytics</h1>}>` + `loading.tsx` (text-bearing for FCP heuristic).
- Tab strip + URL sync (`?tab=<id>`).
- Shared `<AdvancedAnalyticsTable>` parameterised by report
  name + column catalog (`columnCatalogs.ts`). Reuses
  `useColumnSelection`, `<ColumnSelector />`,
  `<DownloadCsvButton />`. Locked column: `ticker`.
- Lighthouse on the focused single-route audit:
  **Score 100, LCP 0 ms, FCP 136 ms, TBT 0 ms, CLS 0.000**
  (well under the §5.15 `/analytics/*` budget).

## Data layer

| Iceberg table | Cadence | Source |
|---|---|---|
| `stocks.nse_delivery` | Step 6 of India Daily Pipeline (07:00 IST tue-sat); executor walks back T-0..T-7 to find first non-empty day | NSE bhavcopy via `jugaad_data` |
| `stocks.fundamentals_snapshot` | Step 8 of India Daily Pipeline | Aggregated from `quarterly_results` |
| `stocks.promoter_holdings` | Standalone `scheduled_jobs` row — monthly on the 25th @ 04:00 IST (idempotent on quarter_end). Currently Cloudflare-blocked from dev IP; production needs allowlisted egress (ASETPLTFRM-358) | BSE shareholding scrape |
| `stocks.corporate_events` | Step 7 of India Daily Pipeline | NSE corporate-actions feed (rolling 7-day window) |

Step 9 of the pipeline is `iceberg_maintenance` — runs
last so it backs up + compacts the AA tables ingested in
steps 6-8. `ALL_TABLES` + `DATE_COLUMNS` in
`backend/maintenance/iceberg_maintenance.py` extended for
the new tables.

See `stocks/create_tables.py:1296+` for the schemas (`_nse_delivery_schema`, `_promoter_holdings_schema`, `_corporate_events_schema`, `_fundamentals_snapshot_schema`).

## Testing

- **Backend pytest** (27 cases, ~0.6 s):
    - `tests/backend/test_advanced_analytics_routes.py` — 7 happy-path × 7 reports, 7 × 403-for-general, cache short-circuit, pagination, stale_tickers, sort validation, top-50 cap (19 cases).
    - `tests/backend/test_emv_14.py` — 5 EMV-14 reference cases.
    - `tests/backend/pipeline/test_bhavcopy.py` — 3 ingestion cases (one commit per day, skipped on empty, surface `SourceError`).
- **E2E Playwright** (5 cases, ~12 s @ 1 worker) —
  `e2e/tests/frontend/aa-page.spec.ts`, project
  `frontend-chromium` (superuser fixture). Covers default
  load, tab switch + URL sync, CSV button enabled,
  stale chip, pagination round-trip.
- **Frontend** — TypeScript clean, ESLint clean (no new
  errors on AA files; pre-existing test-file drift is
  unrelated).

See `advanced-analytics-rollout.md` (this directory) for
the production rollout SOP.
