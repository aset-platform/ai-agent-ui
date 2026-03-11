# Iceberg Data Layer: Column Projection & Caching Analysis

## Executive Summary
The Iceberg repository has **significant optimization opportunities** through column projection (selected_columns) and intelligent caching. Current implementation:
- Reads full tables where only subset of columns needed
- Has file-based caching for analysis/forecast results but not repository-level caching
- Dashboard has TTL caches but backend tools do not
- No column pruning in scans

## 9 Iceberg Tables & Current Access Patterns

### Table 1: registry
- **Partitioning**: None (small table)
- **Write**: upsert_registry() — copy-on-write
- **Reads**: 
  - `get_registry(ticker?)` — uses _scan_ticker or _table_to_df
  - `get_all_registry()` — full scan, reshapes to dict
  - `check_existing_data(ticker)` — uses _scan_ticker
- **Callers**: fetch_stock_data (delta check), dashboard (multi-ticker view)
- **Columns actually used**: ticker, last_fetch_date, total_rows, date_range_{start,end}, market
- **Unused columns**: created_at, updated_at
- **Projection opportunity**: HIGH — always reads all 8 cols, uses 6

### Table 2: company_info
- **Partitioning**: None (append-only snapshots)
- **Write**: insert_company_info() — append only
- **Reads**:
  - `get_latest_company_info_if_fresh(ticker, date)` — _scan_ticker
  - `get_latest_company_info(ticker)` — _scan_ticker
  - `get_all_latest_company_info()` — _table_to_df, groups by ticker
- **Callers**: 
  - get_stock_info tool — reads, checks freshness
  - dashboard analysis/screener — reads all latest
  - helpers._load_currency(ticker) — called from tools, caches in _CURRENCY_CACHE (TTL 300s)
- **Columns actually used**: 
  - get_latest_company_info_if_fresh: all (returns dict)
  - get_latest_company_info: all (returns dict)
  - get_currency(ticker): only "currency" column
- **Projection opportunity**: MEDIUM
  - get_currency could use selected_columns=["ticker", "currency"]
  - Full result readers cannot be optimized (return dict)

### Table 3: ohlcv
- **Partitioning**: ticker
- **Write**: 
  - insert_ohlcv() — append with dedup on (ticker, date)
  - update_ohlcv_adj_close() — copy-on-write partition
- **Reads**:
  - `get_ohlcv(ticker, start?, end?)` — _scan_ticker (all cols)
  - `get_latest_ohlcv_date(ticker)` — _scan_ticker, only needs date col
- **Callers**:
  - fetch_stock_data — reads full table for backup rebuild
  - price_analysis_tool via _analysis_shared._load_parquet() — reads full (Open, High, Low, Close, Adj Close, Volume)
  - forecasting_tool via _forecast_shared._load_parquet() — same as above
  - dashboard via _get_ohlcv_cached() — returns full DataFrame, cached 5 min TTL
- **Schema**: ticker, date, open, high, low, close, adj_close, volume, fetched_at (9 cols)
- **Columns actually used per caller**:
  - Analysis: open, high, low, close, adj_close, volume, date (7/9)
  - Forecasting: close, volume, date (3/9) — rest computed
  - Dashboard: same as analysis (7/9)
- **Projection opportunity**: MEDIUM
  - get_latest_ohlcv_date could project to ["ticker", "date"] only
  - Full reads (analysis/forecast) need 7/9 cols — skip ticker, fetched_at

### Table 4: dividends
- **Partitioning**: ticker
- **Write**: insert_dividends() — append with dedup on (ticker, ex_date)
- **Reads**:
  - `get_dividends(ticker)` — _scan_ticker (all cols)
  - No specific freshness check
- **Callers**:
  - get_dividend_history tool — writes only
  - dashboard via _get_dividends_cached() — reads, cached 5 min TTL
- **Schema**: ticker, ex_date, dividend, currency, inserted_at (5 cols)
- **Columns actually used**: all 5 (dashboard/UI needs all fields)
- **Projection opportunity**: LOW — already minimal schema

### Table 5: technical_indicators
- **Partitioning**: ticker, date
- **Write**: upsert_technical_indicators() — copy-on-write partition
- **Reads**:
  - `get_technical_indicators(ticker, start?, end?)` — _scan_ticker (all cols)
- **Callers**:
  - price_analysis_tool via repo.upsert_technical_indicators() — writes only
  - dashboard analysis charts — reads via indicators cache (TTL 5 min)
- **Schema**: ticker, date, sma_50, sma_200, ema_20, rsi_14, macd, macd_signal, macd_hist, bb_upper, bb_middle, bb_lower, atr_14, computed_at (14 cols)
- **Columns actually used**: all (chart overlays)
- **Projection opportunity**: LOW — full rows needed for visualization

### Table 6: analysis_summary
- **Partitioning**: ticker
- **Write**: insert_analysis_summary() — append only
- **Reads**:
  - `get_latest_analysis_summary(ticker)` — _scan_ticker, sorts by analysis_date
  - `get_all_latest_analysis_summary(limit?, offset?)` — _table_to_df, groups/sorts
  - `get_analysis_history(ticker)` — _scan_ticker, sorts by date
- **Callers**:
  - price_analysis_tool — freshness gate, reads one field (analysis_date)
  - dashboard via _get_analysis_summary_cached() — reads all latest, cached 5 min TTL
  - dashboard gap-fill logic — reads one, computes on-the-fly for missing
- **Schema**: summary_id, ticker, analysis_date, bull_phase_pct, bear_phase_pct, max_drawdown_pct, max_drawdown_duration_days, annualized_volatility_pct, annualized_return_pct, sharpe_ratio, all_time_high, all_time_high_date, all_time_low, all_time_low_date, support_levels, resistance_levels, sma_50_signal, sma_200_signal, rsi_signal, macd_signal_text, best_month, worst_month, best_year, worst_year, computed_at (25 cols)
- **Columns actually used**:
  - Freshness gate: analysis_date only
  - Dashboard: all (result is formatted as report)
- **Projection opportunity**: HIGH
  - get_latest_analysis_summary freshness check: project to ["ticker", "analysis_date"]
  - Full reads cannot be optimized

### Table 7: forecast_runs
- **Partitioning**: ticker, horizon_months, run_date
- **Write**: insert_forecast_run() — append only
- **Reads**:
  - `get_latest_forecast_run(ticker, horizon_months)` — _scan_two_filters, sorts by run_date
  - `get_all_latest_forecast_runs(horizon_months)` — _table_to_df, groups by ticker
- **Callers**:
  - forecasting_tool — freshness gate, reads run_date only
  - dashboard via _get_forecast_runs_cached() — reads metadata (accuracy, targets), cached 5 min TTL
- **Schema**: run_id, ticker, horizon_months, run_date, sentiment, current_price_at_run, target_3m_{date,price,pct_change,lower,upper}, target_6m_{...}, target_9m_{...}, mae, rmse, mape, computed_at (30 cols)
- **Columns actually used**:
  - Freshness gate: run_date only
  - Dashboard: all (targets, accuracy, sentiment)
- **Projection opportunity**: HIGH
  - get_latest_forecast_run freshness check: project to ["ticker", "horizon_months", "run_date"]

### Table 8: forecasts
- **Partitioning**: ticker, horizon_months, run_date
- **Write**: insert_forecast_series() — append (existing series dropped before re-insert)
- **Reads**:
  - `get_latest_forecast_series(ticker, horizon_months)` — _scan_two_filters
- **Callers**:
  - dashboard via _get_forecast_cached() — reads all 4 cols, cached 5 min TTL (per ticker, horizon)
- **Schema**: ticker, horizon_months, run_date, forecast_date, predicted_price, lower_bound, upper_bound (7 cols)
- **Columns actually used**: all 7 (chart needs bounds)
- **Projection opportunity**: LOW — already minimal schema

### Table 9: quarterly_results
- **Partitioning**: ticker, quarter_end, statement_type
- **Write**: insert_quarterly_results() — copy-on-write partition
- **Reads**:
  - `get_quarterly_results(ticker)` — _scan_ticker
  - `get_all_quarterly_results()` — _table_to_df
  - `get_quarterly_results_if_fresh(ticker, days)` — _scan_ticker, filters by date
- **Callers**:
  - fetch_quarterly_results tool — writes only
  - dashboard via _get_quarterly_cached() — reads all, cached 5 min TTL
- **Schema**: ticker, quarter_end, fiscal_year, fiscal_quarter, statement_type, revenue, net_income, gross_profit, operating_income, ebitda, eps_basic, eps_diluted, total_assets, total_liabilities, total_equity, total_debt, cash_and_equivalents, operating_cashflow, capex, free_cashflow, inserted_at (20 cols)
- **Columns actually used**:
  - Freshness check: quarter_end date only
  - Dashboard: all (table display)
- **Projection opportunity**: MEDIUM
  - get_quarterly_results_if_fresh: project to ["ticker", "quarter_end"]

## Column Projection Recommendations by Priority

### CRITICAL (High frequency reads, high column reduction)

**1. get_latest_forecast_run(ticker, horizon_months) — Freshness gate in forecasting_tool**
- Current: reads 30 columns, returns dict
- Optimization: Project to ["ticker", "horizon_months", "run_date"]
- Use case: forecasting_tool line 103 — only checks `rd` field
- Impact: 30 → 3 cols, called once per forecast request
- Recommendation: Add selected_columns parameter to _scan_two_filters
```python
# Before (forecasting_tool, line 103):
latest_run = repo_check.get_latest_forecast_run(ticker, months)
if latest_run is not None:
    rd = latest_run.get("run_date")

# After: modify method signature
def get_latest_forecast_run(
    self,
    ticker: str,
    horizon_months: int,
    projection: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the most recent forecast run."""
    df = self._scan_two_filters(
        _FORECAST_RUNS,
        "ticker", ticker.upper(),
        "horizon_months", horizon_months,
        selected_fields=projection or None,  # Add this
    ).to_pandas()
```

**2. get_latest_analysis_summary(ticker) — Freshness gate in price_analysis_tool**
- Current: reads 25 columns, returns dict
- Optimization: Project to ["ticker", "analysis_date"] for freshness checks
- Use case: price_analysis_tool line 100 — only checks analysis_date
- Impact: 25 → 2 cols, called once per analysis request
- Recommendation: Add optional projection parameter

**3. get_latest_ohlcv_date(ticker) — Used by delta fetch logic**
- Current: reads all 9 columns, only extracts date
- Optimization: Project to ["ticker", "date"]
- Use case: stock_data_tool, called on every delta fetch
- Impact: 9 → 2 cols per ticker per session
- Recommendation: Simple one-liner change to _scan_ticker call

### HIGH (Moderate frequency, moderate reduction)

**4. get_currency(ticker) from company_info**
- Current: full scan + extract "currency"
- Optimization: Project to ["ticker", "currency"]
- Use case: _helpers._load_currency (called from analysis, forecast, UI)
- Cache: Already has TTL cache in _CURRENCY_CACHE (300s), so projection helps cache hit cost
- Impact: All company_info cols → 2 cols
- Recommendation: Modify get_currency to use projection

**5. get_quarterly_results_if_fresh(ticker, days)**
- Current: reads 20 columns, filters by date
- Optimization: For freshness checks only, project to ["ticker", "quarter_end"]
- Use case: fetch_quarterly_results tool (not called frequently)
- Impact: 20 → 2 cols when checking freshness
- Recommendation: Add optional projection parameter

### MEDIUM (Lower frequency, some reduction)

**6. get_all_latest_analysis_summary() — Dashboard gap-fill**
- Current: _table_to_df reads all 25 columns
- Optimization: Dashboard only displays ~8 key fields (rsi_14, macd, sharpe, volatility, etc.)
- Use case: dashboard home & screener (cached 5 min)
- Impact: Not huge (cached), but could reduce initial load time
- Recommendation: Only needed if full-table scans become bottleneck

**7. get_ohlcv(ticker) — Tools + Dashboard**
- Current: reads all 9 columns
- Optimization: Skip ticker (redundant) and fetched_at (metadata only)
- Use case: analysis, forecasting, dashboard
- Impact: 9 → 7 cols — minor savings
- Recommendation: Projection is not worth complexity (full rows needed)

## Caching Opportunities

### Current Caching Status

**Backend tools**: File-based only
- price_analysis_tool: _save_cache / _load_cache (analysis result, today only)
- forecasting_tool: _save_cache / _load_cache (forecast report, today only)
- No Iceberg-level caching in StockRepository

**Dashboard**: Multi-level
- TTL caches (5 min) in iceberg.py:
  - _OHLCV_CACHE per ticker
  - _FORECAST_CACHE per (ticker, horizon)
  - _DIVIDENDS_CACHE per ticker
  - _SUMMARY_CACHE, _COMPANY_CACHE, _FILLED_SUMMARY_CACHE (global, 5 min)
  - _QUARTERLY_CACHE (global, 5 min)
- Indicator cache in data_loaders.py: 5 min TTL per ticker
- Registry cache: 5 min TTL (global)

### Caching Gaps & Recommendations

**1. CRITICAL: Freshness gates re-read full tables**
- Problem: Calls like `get_latest_analysis_summary()` read 25 columns just to check analysis_date
- Solution: Use column projection when only metadata needed
- Expected benefit: 10-20x reduction in bytes read for freshness checks
- Implementation: Low effort (add selected_fields parameter)

**2. Backend tool result caching is shallow**
- Problem: File cache only works within same day for same user
- Current: analyse_stock_price checks Iceberg freshness gate, then file cache
- Limitation: Multi-user agent runs in same process don't share cache
- Recommendation: Not critical (file cache is effective) but consider in-memory cache with user_id key

**3. Repository methods not thread-aware**
- Current: All caching in dashboard/callbacks, none in repository
- If backend runs concurrent agent sessions: no cache sharing between sessions
- Recommendation: Add optional in-memory cache dict to StockRepository for high-frequency reads

**4. Forecast/analysis summary grouped reads inefficient**
- Problem: get_all_latest_analysis_summary groups by ticker after full scan
- Improvement: Add partition-aware scan for multi-ticker reads
- Current: Already cached in dashboard, so not urgent

### Recommended Caching Implementation

**Add to StockRepository for backend tools:**
```python
class StockRepository:
    def __init__(self, enable_cache: bool = False):
        """Cache high-frequency reads if enable_cache=True."""
        self._cache = {} if enable_cache else None
        self._cache_ttl = 300  # 5 minutes
        self._cache_time = {}
    
    def get_currency(self, ticker: str) -> str:
        """Read currency with optional caching."""
        if self._cache is not None:
            cached = self._cache.get(('currency', ticker))
            if cached and time.time() - self._cache_time[('currency', ticker)] < self._cache_ttl:
                return cached
        
        result = ...
        if self._cache is not None:
            self._cache[('currency', ticker)] = result
            self._cache_time[('currency', ticker)] = time.time()
        return result
```

## Read-Heavy vs Write-Heavy Analysis

### Read-Heavy Tables (Candidates for aggressive caching)
1. **ohlcv** — Read 100s of times per session (analysis, forecast, dashboard, export)
   - Already cached in dashboard (5 min)
   - Backend: no caching
   - Recommendation: Enable repo-level cache in agent

2. **technical_indicators** — Read for every chart overlay
   - Cached in dashboard indicator cache (5 min)
   - Recommendation: Sufficient

3. **company_info** — Read for metadata, currency lookups
   - get_currency cached in _CURRENCY_CACHE (300s)
   - get_latest_company_info not cached
   - Recommendation: Add cache for full reads if screener queries improve

4. **analysis_summary** — Read for dashboard & gap-fill
   - Already cached (5 min)
   - Recommendation: Sufficient

5. **forecast_runs** — Read for metadata every forecast request
   - Not cached
   - Recommendation: Add cache (or use projection)

### Write-Heavy Tables (Not candidates for aggressive caching)
1. **registry** — Upsert on every data fetch (copy-on-write, low volume)
2. **technical_indicators** — Upsert on every analysis (per-partition, acceptable)
3. **quarterly_results** — Upsert on quarterly data fetch (rare)

## Single-Run Query Patterns (Repeated within agent execution)

Observed in tools flow:

1. **fetch_stock_data → check_existing_data → get_all_registry**
   - Calls get_all_registry (full scan)
   - Then check_existing_data (ticker scan)
   - Opportunity: Cache full registry, use for both

2. **price_analysis_tool & forecasting_tool → both call _load_parquet**
   - Sequential calls to get_ohlcv(ticker)
   - Should reuse same result within session
   - Current: No sharing between tools
   - Recommendation: Cache at tool invocation level

3. **Dashboard multi-ticker operations**
   - Already handled by 5-min TTL caches
   - Sufficient

## Summary Table: Recommended Actions

| Table | Method | Projection Cols | Priority | Effort | Impact |
|-------|--------|-----------------|----------|--------|--------|
| forecast_runs | get_latest_forecast_run | ["ticker", "horizon_months", "run_date"] | CRITICAL | Low | 30→3 cols (90% reduction) |
| analysis_summary | get_latest_analysis_summary (freshness) | ["ticker", "analysis_date"] | CRITICAL | Low | 25→2 cols (92% reduction) |
| ohlcv | get_latest_ohlcv_date | ["ticker", "date"] | CRITICAL | Low | 9→2 cols (78% reduction) |
| company_info | get_currency | ["ticker", "currency"] | HIGH | Low | All→2 cols |
| quarterly_results | get_quarterly_results_if_fresh | ["ticker", "quarter_end"] | HIGH | Low | 20→2 cols |
| registry | get_all_registry | - | MEDIUM | - | Already small; N/A |
| ohlcv | get_ohlcv (analysis/forecast) | Skip ticker, fetched_at | MEDIUM | Medium | 9→7 cols; complex |
| - | Add repo-level cache | - | MEDIUM | Medium | 5-min TTL for backend tools |

## Implementation Checklist

- [ ] Modify _scan_ticker to accept optional selected_fields parameter
- [ ] Modify _scan_two_filters to accept optional selected_fields parameter
- [ ] Add column projection to get_latest_forecast_run (freshness gate)
- [ ] Add column projection to get_latest_analysis_summary (freshness gate)
- [ ] Optimize get_latest_ohlcv_date to project to date column only
- [ ] Optimize get_currency to project to currency column only
- [ ] Add optional repo-level caching to StockRepository.__init__
- [ ] Benchmark before/after with typical agent runs
- [ ] Update docstrings with column projection examples

## Expected Performance Improvements

- Freshness gates: 10-20x faster bytes read
- Backend tool caching: 2-5x reduction in Iceberg scans per session
- Dashboard: Minimal change (already well-cached)
- Overall agent latency: 5-10% improvement if I/O bound

## Risks & Mitigations

1. **Risk**: Column projection breaks if schema changes
   - Mitigation: Use selected_fields=None as default; only optimize known patterns

2. **Risk**: Caching staleness in multi-process agent
   - Mitigation: Short TTL (5 min); document cache invalidation in agent flow

3. **Risk**: Complexity of optional parameters
   - Mitigation: Hide in internal methods (_scan_ticker); public API unchanged

