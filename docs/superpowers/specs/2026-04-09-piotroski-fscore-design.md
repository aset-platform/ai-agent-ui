# Piotroski F-Score for Nifty 500 — Design Spec

**Date:** 2026-04-09
**Ticket:** ASETPLTFRM-281 (Phase 1 — Nifty 500 only)
**Estimate:** 13 SP
**Branch:** feature/sprint6

---

## Problem

We have 499 Nifty 500 stocks with quarterly financial data in
Iceberg but no fundamental quality scoring. Investors need a
standardised way to rank stocks by financial health. The
Piotroski F-Score (0-9) is a well-known academic metric that
uses 9 binary criteria from public financial statements.

## Scope (Phase 1)

1. Extend `quarterly_results` Iceberg schema with 3 missing fields
2. Backfill those fields for existing 499 stocks
3. Build pure Piotroski scoring engine + tests
4. New `stocks.piotroski_scores` Iceberg table
5. CLI command: `screen` (compute scores for stock_master stocks)
6. API endpoint: `/insights/piotroski`
7. Frontend: new Insights tab with badge + sortable table

**Out of scope (Phase 2 ticket):** NSE universe expansion
beyond Nifty 500, screening filters (mcap/revenue/volume).

---

## Piotroski F-Score — 9 Criteria

Uses **annual** (not quarterly) YoY comparison. Each True = 1 pt.

### Profitability (4 pts)

| # | Criterion | Formula |
|---|-----------|---------|
| 1 | ROA > 0 | `net_income / total_assets > 0` |
| 2 | Operating CF > 0 | `operating_cashflow > 0` |
| 3 | ROA increasing | `roa_current > roa_prev` |
| 4 | Accrual quality | `operating_cashflow > net_income` |

### Leverage / Liquidity (3 pts)

| # | Criterion | Formula |
|---|-----------|---------|
| 5 | Leverage decreasing | `(total_debt/total_assets)_curr < _prev` |
| 6 | Current ratio increasing | `(current_assets/current_liabilities)_curr > _prev` |
| 7 | No share dilution | `shares_outstanding_curr <= _prev` |

### Operating Efficiency (2 pts)

| # | Criterion | Formula |
|---|-----------|---------|
| 8 | Gross margin increasing | `(gross_profit/revenue)_curr > _prev` |
| 9 | Asset turnover increasing | `(revenue/total_assets)_curr > _prev` |

**Labels:** 8-9 Strong (green) | 5-7 Moderate (amber) | 0-4 Weak (red)

---

## Data Gap — 3 Missing Fields

`quarterly_results` Iceberg table currently has 21 fields
(field_id 1-21). Missing for Piotroski:

| Field | yfinance row label | Type |
|-------|--------------------|------|
| `current_assets` | `Current Assets` | DoubleType |
| `current_liabilities` | `Current Liabilities` | DoubleType |
| `shares_outstanding` | `Ordinary Shares Number` | DoubleType |

### Changes required

1. **`_BALANCE_MAP`** in `backend/tools/stock_data_tool.py:531`:
   Add 3 entries:
   ```python
   "Current Assets": "current_assets",
   "Current Liabilities": "current_liabilities",
   "Ordinary Shares Number": "shares_outstanding",
   ```

2. **Iceberg schema** in `stocks/create_tables.py:783`:
   Add 3 `NestedField` entries (field_id 22-24) to
   `_quarterly_results_schema()`.

3. **Schema evolution**: Use PyIceberg `table.update_schema()`
   to add columns to existing table without data loss. Existing
   rows get `null` for new columns.

4. **Backfill**: Re-run `fundamentals` pipeline command for all
   499 stocks. New fields populate on next fetch; old rows
   retain nulls (acceptable — Piotroski uses latest 2 years).

---

## New Iceberg Table: `stocks.piotroski_scores`

One row per (ticker, score_date). Scoped delete-and-append
per score_date allows clean re-runs.

| field_id | name | type |
|----------|------|------|
| 1 | score_id | StringType (UUID) |
| 2 | ticker | StringType |
| 3 | score_date | DateType |
| 4 | total_score | IntegerType |
| 5 | label | StringType |
| 6 | roa_positive | BooleanType |
| 7 | operating_cf_positive | BooleanType |
| 8 | roa_increasing | BooleanType |
| 9 | cf_gt_net_income | BooleanType |
| 10 | leverage_decreasing | BooleanType |
| 11 | current_ratio_increasing | BooleanType |
| 12 | no_dilution | BooleanType |
| 13 | gross_margin_increasing | BooleanType |
| 14 | asset_turnover_increasing | BooleanType |
| 15 | market_cap | LongType |
| 16 | revenue | DoubleType |
| 17 | avg_volume | LongType |
| 18 | sector | StringType |
| 19 | industry | StringType |
| 20 | company_name | StringType |
| 21 | computed_at | TimestampType |

No partitioning (small table, ~500 rows per run).

---

## Architecture

### New module: `backend/pipeline/screener/`

```
backend/pipeline/screener/
├── __init__.py
├── piotroski.py    — pure F-Score computation (no I/O)
└── screen.py       — orchestrator: read quarterly_results
                      → compute scores → write Iceberg
```

### `piotroski.py` — Pure scoring

```python
@dataclass
class PiotroskiResult:
    total_score: int          # 0-9
    roa_positive: bool
    operating_cf_positive: bool
    roa_increasing: bool
    cf_gt_net_income: bool
    leverage_decreasing: bool
    current_ratio_increasing: bool
    no_dilution: bool
    gross_margin_increasing: bool
    asset_turnover_increasing: bool

    @property
    def label(self) -> str:
        if self.total_score >= 8:
            return "Strong"
        if self.total_score >= 5:
            return "Moderate"
        return "Weak"

def compute_piotroski(
    current: dict, previous: dict,
) -> PiotroskiResult:
    """Compute F-Score from two years of financials.

    Each dict must have: net_income, total_assets,
    operating_cashflow, total_debt, current_assets,
    current_liabilities, shares_outstanding,
    gross_profit, revenue.

    Missing values treated as 0 (criterion fails).
    Division by zero → criterion fails.
    """
```

**Design choice:** Two separate dicts (current, previous)
rather than a single dict with `_prev` suffixes. Cleaner API,
easier to test, maps directly to fiscal year data.

### `screen.py` — Orchestrator

```python
async def run_screen(
    tickers: list[str] | None = None,
) -> dict:
    """Score stocks from quarterly_results data.

    1. Load all stock_master tickers (or subset)
    2. For each ticker, read quarterly_results
       from Iceberg (balance + income + cashflow)
    3. Aggregate to annual, pick latest 2 years
    4. Compute Piotroski score
    5. Enrich with company_info (sector, mcap)
    6. Persist to stocks.piotroski_scores
    7. Return summary dict
    """
```

**Key detail:** Quarterly results are stored per
`statement_type` (income/balance/cashflow). The orchestrator
must merge all 3 types for the same fiscal year before
scoring. Annual aggregation: sum income/cashflow rows by
fiscal_year, take latest balance sheet snapshot per year.

### CLI command

```bash
PYTHONPATH=.:backend python -m backend.pipeline.runner screen
PYTHONPATH=.:backend python -m backend.pipeline.runner screen \
    --tickers RELIANCE.NS,TCS.NS
```

### API endpoint

```python
@router.get("/insights/piotroski")
async def get_piotroski(
    min_score: int = Query(0, ge=0, le=9),
    sector: str = Query("all"),
    user: UserContext = Depends(get_current_user),
) -> PiotroskiResponse:
```

Not scoped to user tickers — this is a universe screener.
All authenticated users see the same results. Cached in
Redis with TTL_STABLE (300s).

### Frontend: Insights tab

Add 8th tab "Piotroski F-Score" to existing Insights page.
Reuses `InsightsTable` + `InsightsFilters` pattern.

**Columns:** Ticker, Company, Score (badge), Label, Sector,
Market Cap (Cr), Revenue (Cr), Avg Volume.

**Filters:** Sector dropdown, min-score dropdown (0-9).

**Badge component:** `PiotroskiBadge.tsx` — small pill with
score number, color-coded green/amber/red.

**Expandable row:** Click to see all 9 criteria as
checkmark/cross icons with labels.

---

## Files Modified / Created

| File | Action |
|------|--------|
| `backend/tools/stock_data_tool.py` | Modify `_BALANCE_MAP` (+3 entries) |
| `stocks/create_tables.py` | Modify schema (+3 fields) + add piotroski_scores table |
| `stocks/repository.py` | Add `insert_piotroski_scores()`, `get_piotroski_scores()` |
| `backend/pipeline/screener/__init__.py` | Create (empty) |
| `backend/pipeline/screener/piotroski.py` | Create (pure scoring) |
| `backend/pipeline/screener/screen.py` | Create (orchestrator) |
| `backend/pipeline/runner.py` | Add `screen` command |
| `backend/insights_models.py` | Add `PiotroskiRow`, `PiotroskiResponse` |
| `backend/insights_routes.py` | Add `/insights/piotroski` endpoint |
| `frontend/lib/types.ts` | Add TS interfaces |
| `frontend/hooks/useInsightsData.ts` | Add `usePiotroski` hook |
| `frontend/components/insights/PiotroskiBadge.tsx` | Create |
| `frontend/app/(authenticated)/analytics/insights/page.tsx` | Add tab |
| `tests/backend/test_piotroski.py` | Create (unit tests) |
| `tests/backend/test_screener.py` | Create (integration tests) |

---

## Testing

### Unit: `test_piotroski.py`
- Happy path: known financials → assert score=7, each criterion
- Edge: zero total_assets (div-by-zero) → score=0
- Edge: missing/None values → defaults to failed criterion
- Edge: equal YoY values → criterion fails (not strictly increasing)

### Integration: `test_screener.py`
- Mock `StockRepository` quarterly_results read
- Assert scores computed and returned correctly
- Assert summary dict structure

### Manual verification
```bash
# After backfill
PYTHONPATH=.:backend python -m backend.pipeline.runner screen
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8181/v1/insights/piotroski
# Frontend: Insights → Piotroski F-Score tab
```
