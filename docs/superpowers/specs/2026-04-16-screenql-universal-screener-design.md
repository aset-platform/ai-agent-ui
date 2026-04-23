# ScreenQL — Universal Stock Screener (v1)

> Design spec for a text-based stock screener tab on the Insights page.
> Users write conditions in a simple query language across 35 fields
> from 6 Iceberg tables. Backend parses, validates, assembles DuckDB
> SQL, and returns paginated results.

**Date:** 2026-04-16
**Status:** Approved
**Epic:** ASETPLTFRM-5 (Frontend SPA)
**Sprint:** 7

---

## 1. Overview

New "ScreenQL" tab on the Insights page. A multi-line textarea
accepts conditions in a human-readable query language with
autocomplete on field names. 6 preset templates help users get
started. Backend translates the query into parameterized DuckDB SQL
across Iceberg tables, JOINing only the tables needed. Results
render in `InsightsTable` with dynamic columns (base 5 + fields
used in query), sorting, pagination, and CSV download.

**Architecture:**

```
[Textarea + Autocomplete + Preset Chips]
        | POST /v1/insights/screen
        v
[Backend: Parse -> Validate -> DuckDB SQL]
        | JOIN only referenced tables
        v
[Paginated JSON response]
        | InsightsTable + dynamic columns
        v
[Sort / Paginate / CSV Download]
```

---

## 2. Query Language Syntax

### 2.1 Conditions

Each condition: `<field> <operator> <value>`

**Operators (numeric):** `>`, `<`, `>=`, `<=`, `=`, `!=`
**Operators (text):** `=`, `!=`
**Special:** `CONTAINS` (for array fields like `tags`)

### 2.2 Connectors

- **AND** — combine conditions on one line or across lines
- **OR** — explicit connector on one line
- **Parentheses** — grouping: `(A OR B) AND C`
- **Multi-line** — each line is implicitly AND-ed

### 2.3 Values

- Numbers: `15`, `0.5`, `50000`
- Strings: `"Technology"`, `"nifty50"` (double-quoted)
- Booleans: not needed for v1 (Piotroski booleans exposed
  via `piotroski_score` integer instead)

### 2.4 Examples

```
pe_ratio < 15 AND price_to_book < 3 AND dividend_yield > 2
```

```
market_cap > 50000
earnings_growth > 20
sharpe_ratio > 0.5
```
(multi-line = implicit AND)

```
(pe_ratio < 15 OR price_to_book < 2) AND piotroski_score >= 7
```

```
sector = "Technology" AND sentiment_score > 0.3
tags CONTAINS "nifty50" AND annualized_return_pct > 15
```

---

## 3. Field Catalog (35 fields)

### 3.1 Identity (7 fields)

| Field | Type | Source Table | Column |
|-------|------|-------------|--------|
| `ticker` | text | company_info | ticker |
| `company_name` | text | company_info | company_name |
| `sector` | text | company_info | sector |
| `industry` | text | company_info | industry |
| `market` | text | stock_registry (PG) | market |
| `ticker_type` | text | stock_registry (PG) | ticker_type |
| `tags` | array | stock_tags (PG) | tag |

### 3.2 Valuation (7 fields)

| Field | Type | Source Table | Column |
|-------|------|-------------|--------|
| `market_cap` | number | company_info | market_cap |
| `pe_ratio` | number | company_info | pe_ratio |
| `price_to_book` | number | company_info | price_to_book |
| `dividend_yield` | number | company_info | dividend_yield |
| `current_price` | number | company_info | current_price |
| `week_52_high` | number | company_info | week_52_high |
| `week_52_low` | number | company_info | week_52_low |

### 3.3 Profitability (6 fields)

| Field | Type | Source Table | Column |
|-------|------|-------------|--------|
| `profit_margins` | number | company_info | profit_margins |
| `earnings_growth` | number | company_info | earnings_growth |
| `revenue_growth` | number | company_info | revenue_growth |
| `revenue` | number | quarterly_results | revenue |
| `net_income` | number | quarterly_results | net_income |
| `eps` | number | quarterly_results | eps_diluted |

### 3.4 Risk (5 fields)

| Field | Type | Source Table | Column |
|-------|------|-------------|--------|
| `sharpe_ratio` | number | analysis_summary | sharpe_ratio |
| `annualized_return_pct` | number | analysis_summary | annualized_return_pct |
| `annualized_volatility_pct` | number | analysis_summary | annualized_volatility_pct |
| `max_drawdown_pct` | number | analysis_summary | max_drawdown_pct |
| `beta` | number | company_info | beta |

### 3.5 Technical (5 fields)

| Field | Type | Source Table | Column |
|-------|------|-------------|--------|
| `rsi_14` | number | analysis_summary | rsi_14 (parsed from rsi_signal) |
| `rsi_signal` | text | analysis_summary | rsi_signal |
| `macd_signal` | text | analysis_summary | macd_signal_text |
| `sma_200_signal` | text | analysis_summary | sma_200_signal |
| `sentiment_score` | number | sentiment_scores | avg_score |

### 3.6 Quality (3 fields)

| Field | Type | Source Table | Column |
|-------|------|-------------|--------|
| `piotroski_score` | number | piotroski_scores | total_score |
| `piotroski_label` | text | piotroski_scores | label |
| `forecast_confidence` | number | forecast_runs | confidence_score |

### 3.7 Forecast (3 fields) — latest run only

| Field | Type | Source Table | Column |
|-------|------|-------------|--------|
| `target_3m_pct` | number | forecast_runs | target_3m_pct_change |
| `target_6m_pct` | number | forecast_runs | target_6m_pct_change |
| `target_9m_pct` | number | forecast_runs | target_9m_pct_change |

**Extensibility:** Adding fields = one line in `FIELD_CATALOG` dict.
Tier 2 fields (quarterly financials, Piotroski booleans, forecast
accuracy) can be added without code changes beyond the catalog.

---

## 4. Backend

### 4.1 New Endpoint

**`POST /v1/insights/screen`** in `insights_routes.py`

Request body:
```json
{
  "query": "pe_ratio < 15 AND market_cap > 50000",
  "page": 1,
  "page_size": 25,
  "sort_by": "market_cap",
  "sort_dir": "desc"
}
```

Response:
```json
{
  "rows": [
    {
      "ticker": "KRBL.NS",
      "company_name": "KRBL Limited",
      "sector": "Consumer Defensive",
      "market_cap": 7583000000,
      "current_price": 345.2,
      "pe_ratio": 12.5,
      "market_cap_display": 7583
    }
  ],
  "total": 142,
  "page": 1,
  "page_size": 25,
  "columns_used": ["pe_ratio", "market_cap"],
  "excluded_null_count": 12
}
```

- Auth: JWT required (same as other insights endpoints)
- Superuser: sees all registry tickers
- General user: sees watchlist tickers only
- Redis cached: 300s TTL, keyed on query hash + page + sort

### 4.2 Query Parser Module

**New file:** `backend/insights/screen_parser.py`

Components:
1. **Tokenizer** — splits query into tokens (field, operator,
   value, AND, OR, parens)
2. **Parser** — builds AST from tokens, validates field names
   against `FIELD_CATALOG`, validates operator/type compatibility
3. **SQL Generator** — converts AST to parameterized DuckDB SQL
   WHERE clause, determines which tables to JOIN

**FIELD_CATALOG** dict:
```python
FIELD_CATALOG: dict[str, FieldDef] = {
    "pe_ratio": FieldDef(
        table="ci", column="pe_ratio",
        type="number", label="P/E Ratio",
    ),
    "sector": FieldDef(
        table="ci", column="sector",
        type="text", label="Sector",
    ),
    # ... 35 entries
}
```

**Security:** All values parameterized (`$1`, `$2`, ...).
Field names validated against catalog whitelist. No string
interpolation of user input into SQL.

### 4.3 DuckDB Query Assembly

CTE-based query joining only referenced tables:

```sql
WITH ci AS (
  SELECT ticker, company_name, sector, market_cap,
         current_price, pe_ratio,
         ROW_NUMBER() OVER (
           PARTITION BY ticker
           ORDER BY fetched_at DESC
         ) AS rn
  FROM company_info
),
ci_latest AS (
  SELECT * FROM ci WHERE rn = 1
),
as_latest AS (
  SELECT ticker, sharpe_ratio,
         ROW_NUMBER() OVER (
           PARTITION BY ticker
           ORDER BY computed_at DESC
         ) AS rn
  FROM analysis_summary
)
SELECT
  ci_latest.ticker,
  ci_latest.company_name,
  ci_latest.sector,
  ci_latest.market_cap,
  ci_latest.current_price,
  ci_latest.pe_ratio
FROM ci_latest
LEFT JOIN as_latest ON as_latest.ticker = ci_latest.ticker
  AND as_latest.rn = 1
WHERE ci_latest.pe_ratio < $1
  AND as_latest.sharpe_ratio > $2
ORDER BY ci_latest.market_cap DESC
LIMIT $3 OFFSET $4
```

- `company_info` always included (provides base columns)
- Other tables joined only when their fields are referenced
- Deduplication via `ROW_NUMBER() OVER (PARTITION BY ticker)`
  for tables with multiple rows per ticker
- `quarterly_results` uses latest quarter per ticker
- `sentiment_scores` uses latest score_date per ticker
- `forecast_runs` uses latest run_date per ticker
- `piotroski_scores` uses latest score_date per ticker

### 4.4 NULL Handling

- NULL values **fail** all comparison conditions (conservative)
- SQL uses `AND field IS NOT NULL AND field < $1` pattern
- Response includes `excluded_null_count`: count of tickers
  that had NULL in any referenced field
- Base columns use `COALESCE(ci.company_name, sm.name)` with
  stock_master PG fallback

### 4.5 Error Handling

| Error | HTTP | Response |
|-------|------|----------|
| Unknown field | 422 | `"Unknown field: pe_ration. Did you mean: pe_ratio?"` |
| Type mismatch | 422 | `"Cannot use > with text field 'sector'. Use = or !="` |
| Parse error | 422 | `"Parse error at position 23: expected operator"` |
| Empty query | 422 | `"Query cannot be empty"` |
| Too many conditions | 422 | `"Maximum 20 conditions per query"` |
| Empty results | 200 | `rows: [], total: 0` |

Fuzzy field suggestions use `difflib.get_close_matches()`.

### 4.6 Field Metadata Endpoint

**`GET /v1/insights/screen/fields`** — returns the field catalog
for frontend autocomplete:

```json
{
  "fields": [
    {
      "name": "pe_ratio",
      "label": "P/E Ratio",
      "type": "number",
      "category": "Valuation"
    },
    ...
  ]
}
```

Cached indefinitely (catalog changes only on deployment).

---

## 5. Frontend

### 5.1 ScreenQL Tab Component

New `ScreenQLTab()` function in `insights/page.tsx`.

**Layout (top to bottom):**
1. **Preset chips** — 6 horizontally-scrollable buttons
2. **Query textarea** — multi-line, monospace, 4-row default,
   auto-expand. Placeholder: `Type conditions... e.g. pe_ratio < 15`
3. **Run button + result count** — "Run Screen" primary button,
   "142 results" badge updates after response
4. **Error banner** — red dismissible banner for parse errors
5. **Results table** — `InsightsTable` with dynamic columns

### 5.2 Autocomplete

- Triggered when user types a word that partially matches a field
- Shows a dropdown of matching fields with type + category hint
- Implemented as a positioned `<div>` below cursor (not a
  separate library)
- Field list fetched once from `/screen/fields` endpoint
- Tab/Enter to accept suggestion

### 5.3 Preset Templates

```typescript
const PRESETS = [
  {
    label: "Value Picks",
    query: 'pe_ratio < 15 AND price_to_book < 3\nAND dividend_yield > 2',
  },
  {
    label: "Growth Stars",
    query: 'earnings_growth > 20 AND revenue_growth > 20\nAND sharpe_ratio > 0.5',
  },
  {
    label: "Quality + Momentum",
    query: 'piotroski_score >= 7\nAND annualized_return_pct > 15\nAND rsi_14 < 70',
  },
  {
    label: "Undervalued Large Caps",
    query: 'market_cap > 50000\nAND pe_ratio < 20\nAND sentiment_score > 0.2',
  },
  {
    label: "High Conviction Forecasts",
    query: 'forecast_confidence > 0.6\nAND target_6m_pct > 10',
  },
  {
    label: "Dividend Champions",
    query: 'dividend_yield > 3\nAND piotroski_score >= 5\nAND profit_margins > 10',
  },
];
```

### 5.4 Dynamic Columns

Base columns always shown:
- `ticker`, `company_name`, `sector`, `market_cap`, `current_price`

Additional columns added from `columns_used` in the response:
- Deduplicated (don't add `market_cap` twice if it's in the query)
- Ordered: base columns first, then query columns in order

### 5.5 URL State

Query persisted in URL: `?tab=screenql&q=pe_ratio%20%3C%2015`
- On mount, read `q` param and auto-run if present
- On run, update URL with encoded query (no page reload)
- Enables bookmarking and sharing screen URLs

### 5.6 CSV Download

Reuses existing `downloadCsv()` utility. CSV columns match
the visible table columns. Filename: `screenql-results.csv`.

---

## 6. Presets

| Name | Query |
|------|-------|
| Value Picks | `pe_ratio < 15 AND price_to_book < 3 AND dividend_yield > 2` |
| Growth Stars | `earnings_growth > 20 AND revenue_growth > 20 AND sharpe_ratio > 0.5` |
| Quality + Momentum | `piotroski_score >= 7 AND annualized_return_pct > 15 AND rsi_14 < 70` |
| Undervalued Large Caps | `market_cap > 50000 AND pe_ratio < 20 AND sentiment_score > 0.2` |
| High Conviction Forecasts | `forecast_confidence > 0.6 AND target_6m_pct > 10` |
| Dividend Champions | `dividend_yield > 3 AND piotroski_score >= 5 AND profit_margins > 10` |

---

## 7. Testing

### 7.1 Unit Tests (Backend — pytest)

**Parser tests (`tests/test_screen_parser.py`):**
- Single condition: `pe_ratio < 15` → correct AST
- Multiple AND: `pe_ratio < 15 AND market_cap > 50000`
- OR connector: `pe_ratio < 15 OR price_to_book < 2`
- Parentheses: `(A OR B) AND C` → correct precedence
- Multi-line implicit AND
- String equality: `sector = "Technology"`
- CONTAINS operator: `tags CONTAINS "nifty50"`
- All 6 operators: `>`, `<`, `>=`, `<=`, `=`, `!=`
- Error: unknown field → fuzzy suggestion
- Error: type mismatch (`sector > 15`)
- Error: malformed syntax (missing value, missing operator)
- Error: SQL injection attempts (`; DROP TABLE`)
- Error: empty query
- Error: exceeds 20 conditions

**SQL generator tests:**
- Single table query → no unnecessary JOINs
- Multi-table query → correct JOINs added
- Parameterized values (no string interpolation)
- LIMIT/OFFSET from page/page_size
- ORDER BY from sort_by/sort_dir
- NULL exclusion in WHERE clause

**Endpoint tests (`tests/test_screen_endpoint.py`):**
- Valid query → 200 with rows
- Invalid query → 422 with error detail
- Pagination: page 2 returns different rows
- Empty results → 200 with total: 0
- Auth required → 401 without token
- Redis caching: same query returns cached result

### 7.2 Frontend Tests (Vitest)

- Preset click populates textarea
- Run button triggers API call with query
- Dynamic columns rendered from response
- Error banner shown on 422
- URL param read on mount, auto-run
- CSV download callback wired

### 7.3 E2E Tests (Playwright)

- Navigate to ScreenQL tab
- Type `pe_ratio < 20` → click Run → results table visible
- Click "Value Picks" preset → textarea populated → run → results
- Empty results: type impossible query → "No data available"
- Invalid query: type `xyz > 5` → error banner with suggestion
- Pagination: run broad query → change page size → navigate pages
- CSV download: click CSV button → file downloads
- URL persistence: run query → reload page → same query + results
- Autocomplete: type "pe_" → dropdown shows `pe_ratio`
- Sort: click column header → rows reorder

---

## 8. Files to Create/Modify

### New files:
- `backend/insights/screen_parser.py` — tokenizer, parser,
  SQL generator, field catalog
- `backend/insights/__init__.py` — empty init
- `tests/test_screen_parser.py` — parser unit tests
- `tests/test_screen_endpoint.py` — API integration tests

### Modified files:
- `backend/insights_routes.py` — add `/screen` and
  `/screen/fields` endpoints
- `frontend/app/(authenticated)/analytics/insights/page.tsx` —
  add ScreenQLTab, PRESETS, tab registration
- `frontend/hooks/useInsightsData.ts` — add
  `useScreenFields()` hook for field catalog

### No new dependencies:
- Parser built from scratch (simple recursive descent)
- Autocomplete built with native HTML/React (no library)
- Uses existing: DuckDB engine, InsightsTable, downloadCsv,
  Redis caching, JWT auth

---

## 9. Scope Boundaries (v1)

**In scope:**
- Text query input with autocomplete
- 35 fields across 6 tables
- AND/OR/parentheses
- 6 presets
- Server-side pagination + sorting
- CSV download
- URL-encoded query for bookmarking
- Error messages with fuzzy suggestions

**Out of scope (v2+):**
- Visual query builder
- Saved screens (PG table + CRUD)
- Tier 2 fields (quarterly, Piotroski booleans, forecast
  accuracy)
- Cross-field computed expressions (`revenue / market_cap`)
- Date range conditions (e.g. `ex_date > "2024-01-01"`)
- Result charts / visualizations
- Query history / recent queries
