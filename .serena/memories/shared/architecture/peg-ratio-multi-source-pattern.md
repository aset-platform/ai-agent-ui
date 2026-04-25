# Multi-source PEG ratio (three-variant pattern)

Introduced by ASETPLTFRM-332. Pattern generalisable to any metric where multiple derivation paths exist and cross-checking is valuable.

## Three PEG variants

| Column | Formula | Source | Coverage |
|---|---|---|---|
| **PEG (T)** — trailing | `pe_ratio / (earnings_growth × 100)` | yfinance-sourced `company_info.pe_ratio` + `earnings_growth` | 54.4% (440/809) |
| **PEG (YF)** — forward | `info.pegRatio` / `info.trailingPegRatio` | yfinance directly | 1.0% (8/809) — yfinance publishes for US large-caps only |
| **PEG (Q)** — quarterly TTM | `latest_close / TTM_eps_diluted` ÷ Q-vs-year-ago-Q growth | Our own `quarterly_results` filings | 20.5% (166/809) — needs ≥5 quarters |

All three guarded: null for loss-makers (PE ≤ 0), declining earnings (growth ≤ 0), or missing inputs.

## Why three columns, not one

Different sources reveal different truths:

- **US large-caps converge** — MSFT T=0.453 Q=0.453, AAPL T=1.892 Q=1.884, NVDA T=0.432 Q=0.423. When yfinance data is accurate, variants agree.
- **Indian mid-caps diverge** — AHLUCONT.NS T=2.42 Q=0.64, BALUFORGE.NS T=1.03 Q=0.02, AVL.NS T=4.84 Q=0.45. Our own quarterly filings reveal growth yfinance's derived fields miss.
- **PEG (T) blank but PEG (Q) populated** — PFOCUS.NS, TRITURBINE.NS, CAMS.NS have missing `earningsGrowth` in yfinance but computable growth from our quarterlies. The Q variant fills coverage gaps.

## Computation layer (where each variant lives)

- **PEG (T)** — SQL CASE expression in DuckDB `ci` CTE (see `_CTE_TEMPLATES["ci"]` in `backend/insights/screen_parser.py`). Filterable in ScreenQL.
- **PEG (YF)** — captured column on `company_info`. Schema evolution: `evolve_company_info_peg_yf()` in `stocks/create_tables.py`. Write path: `insert_company_info` captures `info.pegRatio` / `info.trailingPegRatio`. Filterable in ScreenQL.
- **PEG (Q)** — Python-side batch computation in screener endpoint (`_compute_peg_ttm_batch` in `backend/insights_routes.py`). Uses pure helper `_peg_ttm_from_quarters` (testable). Not yet filterable in ScreenQL — requires a DuckDB CTE joining `quarterly_results` + `ohlcv` with window-function aggregation.

## Data-history caveat (PEG Q)

Our `quarterly_results` caps at ~5 quarters per ticker today (yfinance publishes 4–5). Proper TTM-vs-prior-TTM growth needs 8+ quarters. Current implementation uses **single-quarter YoY growth** (Q0 vs Q4) as a proxy — noisier but works with available data. When history deepens, swap `_peg_ttm_from_quarters`'s growth computation — callers unchanged.

## Pattern generalisation

When a metric has multiple legitimate derivations, surface them side-by-side rather than picking one:
1. Quick / broad-coverage computed from shallow data
2. Raw captured from primary source if available
3. Ground-truth computed from deep/audit data

Users see divergence → signal about data quality. Convergence → confidence.

Similar applicability: any metric with both "analyst consensus" and "historical measured" variants (price targets, growth rates, risk metrics).

## Tooltip discipline

KPI_TIPS entries explicitly document:
- Formula
- Source
- Null-guard convention (`<1 undervalued / >2 overvalued`)
- Sparsity caveat (PEG (YF) rarely populated for Indian equities)

Users know WHAT each column means and WHEN it's trustworthy.

## Tests

`tests/backend/test_screen_parser_peg.py` — 23 cases across 5 test classes:
- Catalog registration
- CTE compilation (CASE guards present)
- ScreenQL SQL generation
- `_compute_peg` edge cases + parametrised spot-checks
- `_peg_ttm_from_quarters` pure-function tests

All passing as of commit `15dd006`.
