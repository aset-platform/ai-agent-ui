# Piotroski F-Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute and display Piotroski F-Score (0-9) for all Nifty 500 stocks using existing quarterly financial data.

**Architecture:** Extend the Iceberg `quarterly_results` schema with 3 missing balance-sheet fields, build a pure scoring engine in `backend/pipeline/screener/piotroski.py`, orchestrate scoring via a new `screen` CLI command that reads quarterly data and writes to a new `stocks.piotroski_scores` Iceberg table, expose results via `/insights/piotroski` API, and add a new tab to the Insights page.

**Tech Stack:** Python 3.12, PyIceberg, yfinance, FastAPI, React 19, Next.js 16, SWR, Tailwind CSS

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/tools/stock_data_tool.py` | Modify (line 531) | Add 3 entries to `_BALANCE_MAP` |
| `stocks/create_tables.py` | Modify (line 910, line 88, line 1545) | Add 3 fields to quarterly schema + new piotroski_scores table |
| `stocks/repository.py` | Modify (line 2164, line 2327) | Add 3 columns to Arrow builder + new insert/get methods |
| `backend/pipeline/screener/__init__.py` | Create | Empty package init |
| `backend/pipeline/screener/piotroski.py` | Create | Pure F-Score computation (no I/O) |
| `backend/pipeline/screener/screen.py` | Create | Orchestrator: read quarterly → score → write Iceberg |
| `backend/pipeline/runner.py` | Modify (line 158, line 168) | Add `screen` CLI command |
| `backend/insights_models.py` | Modify (EOF) | Add `PiotroskiRow`, `PiotroskiResponse` |
| `backend/insights_routes.py` | Modify (EOF) | Add `/insights/piotroski` endpoint |
| `frontend/lib/types.ts` | Modify (EOF) | Add TS interfaces |
| `frontend/hooks/useInsightsData.ts` | Modify (EOF) | Add `usePiotroski` hook |
| `frontend/components/insights/PiotroskiBadge.tsx` | Create | Score badge component |
| `frontend/app/(authenticated)/analytics/insights/page.tsx` | Modify (lines 46-63, 1207-1260) | Add Piotroski tab |
| `tests/backend/test_piotroski.py` | Create | Unit tests for scoring |
| `tests/backend/test_screener.py` | Create | Integration tests for orchestrator |

---

### Task 1: Extend `_BALANCE_MAP` with 3 missing fields

**Files:**
- Modify: `backend/tools/stock_data_tool.py:531-537`

- [ ] **Step 1: Add 3 entries to `_BALANCE_MAP`**

In `backend/tools/stock_data_tool.py`, replace the `_BALANCE_MAP` dict (lines 531-537) with:

```python
_BALANCE_MAP = {
    "Total Assets": "total_assets",
    "Total Liabilities Net Minority Interest": (
        "total_liabilities"
    ),
    "Stockholders Equity": "total_equity",
    "Total Debt": "total_debt",
    "Cash And Cash Equivalents": "cash_and_equivalents",
    "Current Assets": "current_assets",
    "Current Liabilities": "current_liabilities",
    "Ordinary Shares Number": "shares_outstanding",
}
```

- [ ] **Step 2: Verify no code changes needed in `_extract_statement`**

The `_extract_statement` function (line 546) already iterates `metric_map.items()` and initialises all columns from all maps via `all_cols` (line 573-577). The 3 new columns will be auto-populated. No changes needed.

- [ ] **Step 3: Commit**

```bash
git add backend/tools/stock_data_tool.py
git commit -m "feat(pipeline): add current_assets, current_liabilities, shares_outstanding to _BALANCE_MAP

Extends balance sheet extraction for Piotroski F-Score.
yfinance labels: Current Assets, Current Liabilities,
Ordinary Shares Number.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 2: Extend Iceberg `quarterly_results` schema + add `piotroski_scores` table

**Files:**
- Modify: `stocks/create_tables.py:88` (table identifiers)
- Modify: `stocks/create_tables.py:904-916` (quarterly schema)
- Modify: `stocks/create_tables.py:1543-1548` (create_tables function)

- [ ] **Step 1: Add table identifier constant**

After line 93 (`_DATA_GAPS_TABLE`), add:

```python
_PIOTROSKI_SCORES_TABLE = f"{_NAMESPACE}.piotroski_scores"
```

- [ ] **Step 2: Add 3 fields to `_quarterly_results_schema`**

Insert before the `updated_at` field (before line 910), add 3 new `NestedField` entries with field_id 22-24:

```python
        NestedField(
            field_id=22,
            name="current_assets",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=23,
            name="current_liabilities",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=24,
            name="shares_outstanding",
            field_type=DoubleType(),
            required=False,
        ),
```

- [ ] **Step 3: Add `_piotroski_scores_schema` function**

Add after `_quarterly_results_schema` (after line 916):

```python
def _piotroski_scores_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.piotroski_scores``.

    Returns:
        Schema: One row per (ticker, score_date).
    """
    return Schema(
        NestedField(
            field_id=1,
            name="score_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="ticker",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="score_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="total_score",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="label",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="roa_positive",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="operating_cf_positive",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="roa_increasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="cf_gt_net_income",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="leverage_decreasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="current_ratio_increasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="no_dilution",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=13,
            name="gross_margin_increasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=14,
            name="asset_turnover_increasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="market_cap",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=16,
            name="revenue",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=17,
            name="avg_volume",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=18,
            name="sector",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=19,
            name="industry",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=20,
            name="company_name",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=21,
            name="computed_at",
            field_type=TimestampType(),
            required=False,
        ),
    )
```

- [ ] **Step 4: Register `piotroski_scores` in `create_tables()`**

In the `create_tables()` function, after the `_QUARTERLY_RESULTS_TABLE` creation block (after line 1548), add:

```python
    _create_table(
        catalog,
        _PIOTROSKI_SCORES_TABLE,
        _piotroski_scores_schema(),
        empty_spec,
    )
```

- [ ] **Step 5: Add schema evolution script for existing table**

Since `quarterly_results` already exists with 21 fields, we need schema evolution. Add a standalone function at the bottom of `stocks/create_tables.py`:

```python
def evolve_quarterly_results_schema() -> None:
    """Add Piotroski fields to existing quarterly_results.

    Adds current_assets, current_liabilities,
    shares_outstanding (field_id 22-24). Idempotent —
    skips if columns already exist.
    """
    catalog = _get_catalog()
    tbl = catalog.load_table(_QUARTERLY_RESULTS_TABLE)
    existing = {f.name for f in tbl.schema().fields}
    new_fields = [
        NestedField(
            field_id=22,
            name="current_assets",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=23,
            name="current_liabilities",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=24,
            name="shares_outstanding",
            field_type=DoubleType(),
            required=False,
        ),
    ]
    to_add = [f for f in new_fields if f.name not in existing]
    if not to_add:
        _logger.info(
            "quarterly_results already has Piotroski "
            "columns — skipping evolution."
        )
        return
    with tbl.update_schema() as update:
        for field in to_add:
            update.add_column(
                path=field.name,
                field_type=field.field_type,
            )
    _logger.info(
        "Evolved quarterly_results schema: added %s",
        [f.name for f in to_add],
    )
```

- [ ] **Step 6: Update docstring table count**

Update the docstring at top of file (line 3): change "11 Iceberg tables" to "15 Iceberg tables" and add `stocks.piotroski_scores` to the table list (after line 32).

- [ ] **Step 7: Commit**

```bash
git add stocks/create_tables.py
git commit -m "feat(iceberg): add piotroski_scores table + evolve quarterly_results schema

3 new fields in quarterly_results: current_assets,
current_liabilities, shares_outstanding (field_id 22-24).
New piotroski_scores table with 21 fields for F-Score storage.
evolve_quarterly_results_schema() for live schema migration.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 3: Extend repository Arrow builder for 3 new columns

**Files:**
- Modify: `stocks/repository.py:2164-2250` (insert_quarterly_results Arrow builder)
- Modify: `stocks/repository.py:2327` (_ALL_TICKER_TABLES)

- [ ] **Step 1: Add 3 columns to Arrow table builder**

In `insert_quarterly_results` method, after the `"free_cashflow"` array (line 2244) and before the `"updated_at"` array (line 2246), add:

```python
                "current_assets": pa.array(
                    [
                        _safe_float(v)
                        for v in combined.get(
                            "current_assets",
                            [None] * len(combined),
                        )
                    ]
                    if "current_assets" in combined.columns
                    else [None] * len(combined),
                    pa.float64(),
                ),
                "current_liabilities": pa.array(
                    [
                        _safe_float(v)
                        for v in combined.get(
                            "current_liabilities",
                            [None] * len(combined),
                        )
                    ]
                    if "current_liabilities"
                    in combined.columns
                    else [None] * len(combined),
                    pa.float64(),
                ),
                "shares_outstanding": pa.array(
                    [
                        _safe_float(v)
                        for v in combined.get(
                            "shares_outstanding",
                            [None] * len(combined),
                        )
                    ]
                    if "shares_outstanding"
                    in combined.columns
                    else [None] * len(combined),
                    pa.float64(),
                ),
```

The `if col in combined.columns else [None]` guards handle backward compatibility — existing rows being merged won't have these columns.

- [ ] **Step 2: Add `stocks.piotroski_scores` to `_ALL_TICKER_TABLES`**

At line 2327, add to the tuple:

```python
    _ALL_TICKER_TABLES = (
        "stocks.registry",
        "stocks.company_info",
        "stocks.ohlcv",
        "stocks.dividends",
        "stocks.technical_indicators",
        "stocks.analysis_summary",
        "stocks.forecast_runs",
        "stocks.forecasts",
        "stocks.quarterly_results",
        "stocks.piotroski_scores",
    )
```

- [ ] **Step 3: Add `_PIOTROSKI_SCORES` constant and repository methods**

Add near the top of the file where other table constants are defined:

```python
_PIOTROSKI_SCORES = "stocks.piotroski_scores"
```

Then add methods after the `get_quarterly_results_if_fresh` method (after line 2323):

```python
    # ------------------------------------------------------------------
    # Piotroski Scores
    # ------------------------------------------------------------------

    def insert_piotroski_scores(
        self, scores: list[dict],
    ) -> int:
        """Write Piotroski scores to Iceberg.

        Uses scoped delete-and-append per score_date
        so re-runs on the same day overwrite cleanly.

        Args:
            scores: List of score dicts matching the
                ``stocks.piotroski_scores`` schema.

        Returns:
            Number of rows written.
        """
        if not scores:
            return 0
        now = _now_utc()
        score_date = scores[0].get("score_date")
        arrow = pa.table(
            {
                "score_id": pa.array(
                    [s["score_id"] for s in scores],
                    pa.string(),
                ),
                "ticker": pa.array(
                    [s["ticker"] for s in scores],
                    pa.string(),
                ),
                "score_date": pa.array(
                    [
                        _to_date(s["score_date"])
                        for s in scores
                    ],
                    pa.date32(),
                ),
                "total_score": pa.array(
                    [s["total_score"] for s in scores],
                    pa.int32(),
                ),
                "label": pa.array(
                    [s["label"] for s in scores],
                    pa.string(),
                ),
                "roa_positive": pa.array(
                    [s["roa_positive"] for s in scores],
                    pa.bool_(),
                ),
                "operating_cf_positive": pa.array(
                    [
                        s["operating_cf_positive"]
                        for s in scores
                    ],
                    pa.bool_(),
                ),
                "roa_increasing": pa.array(
                    [
                        s["roa_increasing"]
                        for s in scores
                    ],
                    pa.bool_(),
                ),
                "cf_gt_net_income": pa.array(
                    [
                        s["cf_gt_net_income"]
                        for s in scores
                    ],
                    pa.bool_(),
                ),
                "leverage_decreasing": pa.array(
                    [
                        s["leverage_decreasing"]
                        for s in scores
                    ],
                    pa.bool_(),
                ),
                "current_ratio_increasing": pa.array(
                    [
                        s["current_ratio_increasing"]
                        for s in scores
                    ],
                    pa.bool_(),
                ),
                "no_dilution": pa.array(
                    [s["no_dilution"] for s in scores],
                    pa.bool_(),
                ),
                "gross_margin_increasing": pa.array(
                    [
                        s["gross_margin_increasing"]
                        for s in scores
                    ],
                    pa.bool_(),
                ),
                "asset_turnover_increasing": pa.array(
                    [
                        s["asset_turnover_increasing"]
                        for s in scores
                    ],
                    pa.bool_(),
                ),
                "market_cap": pa.array(
                    [
                        _safe_int(s.get("market_cap"))
                        for s in scores
                    ],
                    pa.int64(),
                ),
                "revenue": pa.array(
                    [
                        _safe_float(s.get("revenue"))
                        for s in scores
                    ],
                    pa.float64(),
                ),
                "avg_volume": pa.array(
                    [
                        _safe_int(s.get("avg_volume"))
                        for s in scores
                    ],
                    pa.int64(),
                ),
                "sector": pa.array(
                    [
                        s.get("sector") for s in scores
                    ],
                    pa.string(),
                ),
                "industry": pa.array(
                    [
                        s.get("industry")
                        for s in scores
                    ],
                    pa.string(),
                ),
                "company_name": pa.array(
                    [
                        s.get("company_name")
                        for s in scores
                    ],
                    pa.string(),
                ),
                "computed_at": pa.array(
                    [now] * len(scores),
                    pa.timestamp("us"),
                ),
            }
        )
        # Delete previous run for same date
        if score_date:
            from pyiceberg.expressions import EqualTo

            try:
                self._delete_rows(
                    _PIOTROSKI_SCORES,
                    EqualTo(
                        "score_date",
                        _to_date(score_date),
                    ),
                )
            except Exception:
                _logger.debug(
                    "Delete before insert failed for "
                    "piotroski_scores/%s",
                    score_date,
                    exc_info=True,
                )
        self._append_rows(_PIOTROSKI_SCORES, arrow)
        _logger.info(
            "piotroski_scores inserted %d rows for %s",
            len(scores),
            score_date,
        )
        return len(scores)

    def get_piotroski_scores(
        self,
    ) -> pd.DataFrame:
        """Read all Piotroski scores from Iceberg.

        Returns:
            DataFrame sorted by total_score descending.
            Caller filters by score_date if needed.
        """
        df = self._table_to_df(_PIOTROSKI_SCORES)
        if df.empty:
            return df
        return (
            df.sort_values(
                "total_score", ascending=False,
            )
            .reset_index(drop=True)
        )
```

- [ ] **Step 4: Commit**

```bash
git add stocks/repository.py
git commit -m "feat(repo): extend quarterly_results Arrow builder + add piotroski_scores methods

3 new columns in Arrow builder with backward-compat guards.
insert_piotroski_scores: scoped delete-and-append per date.
get_piotroski_scores: full scan sorted by score desc.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 4: Run schema evolution on live Iceberg table

**Files:**
- None (CLI command only)

- [ ] **Step 1: Run schema evolution**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
PYTHONPATH=.:backend python -c "
from stocks.create_tables import evolve_quarterly_results_schema
evolve_quarterly_results_schema()
"
```

Expected: `Evolved quarterly_results schema: added ['current_assets', 'current_liabilities', 'shares_outstanding']`

- [ ] **Step 2: Create piotroski_scores table**

```bash
PYTHONPATH=.:backend python -c "
from stocks.create_tables import create_tables
create_tables()
"
```

Expected: `Created Iceberg table 'stocks.piotroski_scores'.` (other tables log "already exists — skipping").

---

### Task 5: Build `piotroski.py` — pure scoring engine

**Files:**
- Create: `backend/pipeline/screener/__init__.py`
- Create: `backend/pipeline/screener/piotroski.py`
- Create: `tests/backend/test_piotroski.py`

- [ ] **Step 1: Create package init**

Create empty `backend/pipeline/screener/__init__.py`.

- [ ] **Step 2: Write failing tests**

Create `tests/backend/test_piotroski.py`:

```python
"""Unit tests for Piotroski F-Score computation."""
import pytest

from backend.pipeline.screener.piotroski import (
    PiotroskiResult,
    compute_piotroski,
)


def _make_financials(
    net_income=100,
    total_assets=1000,
    operating_cashflow=150,
    total_debt=200,
    current_assets=500,
    current_liabilities=300,
    shares_outstanding=1_000_000,
    gross_profit=400,
    revenue=800,
):
    """Build a financials dict with defaults."""
    return {
        "net_income": net_income,
        "total_assets": total_assets,
        "operating_cashflow": operating_cashflow,
        "total_debt": total_debt,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "shares_outstanding": shares_outstanding,
        "gross_profit": gross_profit,
        "revenue": revenue,
    }


class TestComputePiotroski:
    """Tests for compute_piotroski()."""

    def test_perfect_score(self):
        """All 9 criteria pass → score 9."""
        current = _make_financials(
            net_income=120,
            total_assets=1000,
            operating_cashflow=150,
            total_debt=180,
            current_assets=550,
            current_liabilities=300,
            shares_outstanding=900_000,
            gross_profit=450,
            revenue=900,
        )
        previous = _make_financials(
            net_income=100,
            total_assets=1000,
            operating_cashflow=130,
            total_debt=200,
            current_assets=500,
            current_liabilities=300,
            shares_outstanding=1_000_000,
            gross_profit=400,
            revenue=800,
        )
        result = compute_piotroski(current, previous)
        assert result.total_score == 9
        assert result.label == "Strong"
        assert result.roa_positive is True
        assert result.operating_cf_positive is True
        assert result.roa_increasing is True
        assert result.cf_gt_net_income is True
        assert result.leverage_decreasing is True
        assert result.current_ratio_increasing is True
        assert result.no_dilution is True
        assert result.gross_margin_increasing is True
        assert result.asset_turnover_increasing is True

    def test_zero_score(self):
        """All criteria fail → score 0."""
        current = _make_financials(
            net_income=-50,
            total_assets=1000,
            operating_cashflow=-10,
            total_debt=300,
            current_assets=400,
            current_liabilities=500,
            shares_outstanding=1_200_000,
            gross_profit=300,
            revenue=700,
        )
        previous = _make_financials(
            net_income=100,
            total_assets=1000,
            operating_cashflow=150,
            total_debt=200,
            current_assets=500,
            current_liabilities=300,
            shares_outstanding=1_000_000,
            gross_profit=400,
            revenue=800,
        )
        result = compute_piotroski(current, previous)
        assert result.total_score == 0
        assert result.label == "Weak"

    def test_moderate_score(self):
        """Mixed criteria → moderate score."""
        current = _make_financials(
            net_income=120,
            total_assets=1000,
            operating_cashflow=150,
            total_debt=250,
            current_assets=400,
            current_liabilities=500,
            shares_outstanding=1_000_000,
            gross_profit=400,
            revenue=800,
        )
        previous = _make_financials()
        result = compute_piotroski(current, previous)
        assert 1 <= result.total_score <= 8
        assert result.roa_positive is True
        assert result.operating_cf_positive is True
        assert result.cf_gt_net_income is True

    def test_zero_total_assets(self):
        """Division by zero guarded → criterion fails."""
        current = _make_financials(total_assets=0)
        previous = _make_financials(total_assets=0)
        result = compute_piotroski(current, previous)
        assert result.roa_positive is False
        assert result.roa_increasing is False
        assert result.leverage_decreasing is False
        assert result.asset_turnover_increasing is False

    def test_none_values(self):
        """Missing data → defaults to criterion fail."""
        current = {
            "net_income": None,
            "total_assets": None,
            "operating_cashflow": None,
            "total_debt": None,
            "current_assets": None,
            "current_liabilities": None,
            "shares_outstanding": None,
            "gross_profit": None,
            "revenue": None,
        }
        previous = dict(current)
        result = compute_piotroski(current, previous)
        assert result.total_score == 0

    def test_equal_yoy_fails(self):
        """Equal YoY values → 'increasing' criteria fail."""
        current = _make_financials()
        previous = _make_financials()
        result = compute_piotroski(current, previous)
        assert result.roa_increasing is False
        assert result.current_ratio_increasing is False
        assert result.gross_margin_increasing is False
        assert result.asset_turnover_increasing is False
        assert result.no_dilution is True

    def test_label_boundaries(self):
        """Label boundaries: 8=Strong, 5=Moderate, 4=Weak."""
        r8 = PiotroskiResult(
            total_score=8,
            roa_positive=True,
            operating_cf_positive=True,
            roa_increasing=True,
            cf_gt_net_income=True,
            leverage_decreasing=True,
            current_ratio_increasing=True,
            no_dilution=True,
            gross_margin_increasing=True,
            asset_turnover_increasing=False,
        )
        assert r8.label == "Strong"

        r5 = PiotroskiResult(
            total_score=5,
            roa_positive=True,
            operating_cf_positive=True,
            roa_increasing=True,
            cf_gt_net_income=True,
            leverage_decreasing=True,
            current_ratio_increasing=False,
            no_dilution=False,
            gross_margin_increasing=False,
            asset_turnover_increasing=False,
        )
        assert r5.label == "Moderate"

        r4 = PiotroskiResult(
            total_score=4,
            roa_positive=True,
            operating_cf_positive=True,
            roa_increasing=True,
            cf_gt_net_income=True,
            leverage_decreasing=False,
            current_ratio_increasing=False,
            no_dilution=False,
            gross_margin_increasing=False,
            asset_turnover_increasing=False,
        )
        assert r4.label == "Weak"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
python -m pytest tests/backend/test_piotroski.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'backend.pipeline.screener'`

- [ ] **Step 4: Implement `piotroski.py`**

Create `backend/pipeline/screener/piotroski.py`:

```python
"""Piotroski F-Score computation engine.

Pure functions — no I/O, no database access. Takes two
dicts of annual financial data (current year, previous
year) and returns a scored result.

Reference: Piotroski, J.D. (2000). "Value Investing:
The Use of Historical Financial Statement Information
to Separate Winners from Losers."
"""
from __future__ import annotations

from dataclasses import dataclass


def _safe(val) -> float:
    """Coerce to float; None/NaN → 0.0."""
    if val is None:
        return 0.0
    try:
        f = float(val)
        return 0.0 if f != f else f  # NaN check
    except (ValueError, TypeError):
        return 0.0


def _ratio(num: float, den: float) -> float | None:
    """Safe division; returns None if denominator is 0."""
    if den == 0.0:
        return None
    return num / den


@dataclass
class PiotroskiResult:
    """Result of Piotroski F-Score computation."""

    total_score: int
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
        """Human-readable quality label."""
        if self.total_score >= 8:
            return "Strong"
        if self.total_score >= 5:
            return "Moderate"
        return "Weak"


def compute_piotroski(
    current: dict,
    previous: dict,
) -> PiotroskiResult:
    """Compute Piotroski F-Score from two years of data.

    Args:
        current: Current fiscal year financials with keys:
            net_income, total_assets, operating_cashflow,
            total_debt, current_assets, current_liabilities,
            shares_outstanding, gross_profit, revenue.
        previous: Prior fiscal year (same keys).

    Returns:
        PiotroskiResult with all 9 criteria and total.
    """
    # Current year values
    ni = _safe(current.get("net_income"))
    ta = _safe(current.get("total_assets"))
    ocf = _safe(current.get("operating_cashflow"))
    td = _safe(current.get("total_debt"))
    ca = _safe(current.get("current_assets"))
    cl = _safe(current.get("current_liabilities"))
    so = _safe(current.get("shares_outstanding"))
    gp = _safe(current.get("gross_profit"))
    rev = _safe(current.get("revenue"))

    # Previous year values
    ni_p = _safe(previous.get("net_income"))
    ta_p = _safe(previous.get("total_assets"))
    td_p = _safe(previous.get("total_debt"))
    ca_p = _safe(previous.get("current_assets"))
    cl_p = _safe(previous.get("current_liabilities"))
    so_p = _safe(previous.get("shares_outstanding"))
    gp_p = _safe(previous.get("gross_profit"))
    rev_p = _safe(previous.get("revenue"))

    # Profitability (4)
    roa_curr = _ratio(ni, ta)
    roa_prev = _ratio(ni_p, ta_p)
    roa_positive = (roa_curr or 0) > 0
    operating_cf_positive = ocf > 0
    roa_increasing = (
        roa_curr is not None
        and roa_prev is not None
        and roa_curr > roa_prev
    )
    cf_gt_net_income = ocf > ni

    # Leverage / Liquidity (3)
    lev_curr = _ratio(td, ta)
    lev_prev = _ratio(td_p, ta_p)
    leverage_decreasing = (
        lev_curr is not None
        and lev_prev is not None
        and lev_curr < lev_prev
    )

    cr_curr = _ratio(ca, cl)
    cr_prev = _ratio(ca_p, cl_p)
    current_ratio_increasing = (
        cr_curr is not None
        and cr_prev is not None
        and cr_curr > cr_prev
    )

    no_dilution = so <= so_p

    # Operating Efficiency (2)
    gm_curr = _ratio(gp, rev)
    gm_prev = _ratio(gp_p, rev_p)
    gross_margin_increasing = (
        gm_curr is not None
        and gm_prev is not None
        and gm_curr > gm_prev
    )

    at_curr = _ratio(rev, ta)
    at_prev = _ratio(rev_p, ta_p)
    asset_turnover_increasing = (
        at_curr is not None
        and at_prev is not None
        and at_curr > at_prev
    )

    criteria = [
        roa_positive,
        operating_cf_positive,
        roa_increasing,
        cf_gt_net_income,
        leverage_decreasing,
        current_ratio_increasing,
        no_dilution,
        gross_margin_increasing,
        asset_turnover_increasing,
    ]

    return PiotroskiResult(
        total_score=sum(criteria),
        roa_positive=roa_positive,
        operating_cf_positive=operating_cf_positive,
        roa_increasing=roa_increasing,
        cf_gt_net_income=cf_gt_net_income,
        leverage_decreasing=leverage_decreasing,
        current_ratio_increasing=current_ratio_increasing,
        no_dilution=no_dilution,
        gross_margin_increasing=gross_margin_increasing,
        asset_turnover_increasing=asset_turnover_increasing,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/backend/test_piotroski.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/screener/__init__.py \
        backend/pipeline/screener/piotroski.py \
        tests/backend/test_piotroski.py
git commit -m "feat(piotroski): pure F-Score computation engine + 7 unit tests

PiotroskiResult dataclass with 9 boolean criteria + label.
compute_piotroski(current, previous) → PiotroskiResult.
Safe division, None/NaN handling, zero-total-assets guard.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 6: Build `screen.py` orchestrator

**Files:**
- Create: `backend/pipeline/screener/screen.py`

- [ ] **Step 1: Create orchestrator**

Create `backend/pipeline/screener/screen.py`:

```python
"""Piotroski F-Score screening orchestrator.

Reads quarterly_results from Iceberg for stock_master
tickers, aggregates to annual, computes F-Score, enriches
with company_info metadata, and persists to
stocks.piotroski_scores.

Usage::

    from backend.pipeline.screener.screen import run_screen
    result = await run_screen()
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import date, timezone

import pandas as pd

from backend.pipeline.screener.piotroski import (
    compute_piotroski,
)

_logger = logging.getLogger(__name__)


def _aggregate_annual(
    qr_df: pd.DataFrame,
) -> dict[int, dict]:
    """Aggregate quarterly results to annual dicts.

    Income/cashflow rows are summed per fiscal_year.
    Balance rows use latest quarter_end per year.

    Args:
        qr_df: Quarterly results for one ticker.

    Returns:
        Dict keyed by fiscal_year with merged fields.
    """
    if qr_df.empty:
        return {}

    years: dict[int, dict] = {}

    # Income: sum per year
    inc = qr_df[qr_df["statement_type"] == "income"]
    for fy, grp in inc.groupby("fiscal_year"):
        years.setdefault(int(fy), {})
        for col in [
            "revenue",
            "net_income",
            "gross_profit",
        ]:
            vals = grp[col].dropna()
            if not vals.empty:
                years[int(fy)][col] = vals.sum()

    # Cashflow: sum per year
    cf = qr_df[qr_df["statement_type"] == "cashflow"]
    for fy, grp in cf.groupby("fiscal_year"):
        years.setdefault(int(fy), {})
        for col in ["operating_cashflow"]:
            vals = grp[col].dropna()
            if not vals.empty:
                years[int(fy)][col] = vals.sum()

    # Balance: latest quarter per year
    bal = qr_df[
        qr_df["statement_type"] == "balance"
    ].copy()
    if not bal.empty:
        bal = bal.sort_values("quarter_end")
        for fy, grp in bal.groupby("fiscal_year"):
            years.setdefault(int(fy), {})
            latest = grp.iloc[-1]
            for col in [
                "total_assets",
                "total_debt",
                "current_assets",
                "current_liabilities",
                "shares_outstanding",
            ]:
                val = latest.get(col)
                if pd.notna(val):
                    years[int(fy)][col] = float(val)

    return years


async def run_screen(
    tickers: list[str] | None = None,
) -> dict:
    """Score stocks and persist to Iceberg.

    Args:
        tickers: Optional list of tickers to score.
            If None, scores all active stock_master
            tickers.

    Returns:
        Summary dict with counts and elapsed time.
    """
    t0 = time.monotonic()
    from tools._stock_shared import _require_repo

    repo = _require_repo()

    # Get tickers from stock_master if not provided
    if tickers is None:
        from backend.db.engine import session_factory
        from backend.pipeline.universe import (
            get_all_stocks,
        )

        async with session_factory() as session:
            stocks = await get_all_stocks(
                session, active_only=True,
            )
        tickers = [s.yf_ticker for s in stocks]

    _logger.info(
        "Scoring %d tickers for Piotroski F-Score",
        len(tickers),
    )

    # Load company_info for enrichment
    try:
        ci_df = repo._table_to_df("stocks.company_info")
        if not ci_df.empty:
            ci_df = (
                ci_df.sort_values(
                    "fetched_at", ascending=False,
                )
                .groupby("ticker", as_index=False)
                .first()
            )
    except Exception:
        ci_df = pd.DataFrame()

    today = date.today()
    scores: list[dict] = []
    skipped = 0
    failed = 0

    for ticker in tickers:
        try:
            qr_df = repo.get_quarterly_results(ticker)
            if qr_df.empty:
                skipped += 1
                continue

            annual = _aggregate_annual(qr_df)
            if len(annual) < 2:
                skipped += 1
                continue

            # Pick latest 2 fiscal years
            sorted_years = sorted(
                annual.keys(), reverse=True,
            )
            curr_year = annual[sorted_years[0]]
            prev_year = annual[sorted_years[1]]

            result = compute_piotroski(
                curr_year, prev_year,
            )

            # Enrich with company_info
            ci_row = {}
            if not ci_df.empty:
                match = ci_df[
                    ci_df["ticker"] == ticker
                ]
                if not match.empty:
                    ci_row = match.iloc[0].to_dict()

            scores.append(
                {
                    "score_id": str(uuid.uuid4()),
                    "ticker": ticker,
                    "score_date": today,
                    "total_score": result.total_score,
                    "label": result.label,
                    "roa_positive": (
                        result.roa_positive
                    ),
                    "operating_cf_positive": (
                        result.operating_cf_positive
                    ),
                    "roa_increasing": (
                        result.roa_increasing
                    ),
                    "cf_gt_net_income": (
                        result.cf_gt_net_income
                    ),
                    "leverage_decreasing": (
                        result.leverage_decreasing
                    ),
                    "current_ratio_increasing": (
                        result.current_ratio_increasing
                    ),
                    "no_dilution": result.no_dilution,
                    "gross_margin_increasing": (
                        result.gross_margin_increasing
                    ),
                    "asset_turnover_increasing": (
                        result.asset_turnover_increasing
                    ),
                    "market_cap": ci_row.get(
                        "market_cap",
                    ),
                    "revenue": curr_year.get("revenue"),
                    "avg_volume": ci_row.get(
                        "avg_volume",
                    ),
                    "sector": ci_row.get("sector"),
                    "industry": ci_row.get("industry"),
                    "company_name": ci_row.get(
                        "company_name",
                    ),
                }
            )
        except Exception:
            _logger.warning(
                "Failed to score %s",
                ticker,
                exc_info=True,
            )
            failed += 1

    # Persist
    written = 0
    if scores:
        written = repo.insert_piotroski_scores(scores)

    strong = sum(
        1 for s in scores if s["total_score"] >= 8
    )
    moderate = sum(
        1
        for s in scores
        if 5 <= s["total_score"] < 8
    )
    weak = sum(
        1 for s in scores if s["total_score"] < 5
    )
    elapsed = time.monotonic() - t0

    summary = {
        "tickers": len(tickers),
        "scored": written,
        "skipped": skipped,
        "failed": failed,
        "strong": strong,
        "moderate": moderate,
        "weak": weak,
        "elapsed_s": round(elapsed, 1),
    }
    _logger.info("Screen complete: %s", summary)
    return summary
```

- [ ] **Step 2: Commit**

```bash
git add backend/pipeline/screener/screen.py
git commit -m "feat(screener): orchestrator reads quarterly_results, scores, writes Iceberg

_aggregate_annual: merges income/balance/cashflow by fiscal year.
run_screen: iterates stock_master, computes Piotroski, enriches
with company_info, persists to stocks.piotroski_scores.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 7: Add `screen` CLI command to runner

**Files:**
- Modify: `backend/pipeline/runner.py:147-158` (parser)
- Modify: `backend/pipeline/runner.py:168-181` (dispatch)

- [ ] **Step 1: Add subparser**

In `_build_parser()`, before `return parser` (line 158), add:

```python
    # screen -------------------------------------------------------
    p_screen = sub.add_parser(
        "screen",
        help="Compute Piotroski F-Score for stock_master",
    )
    p_screen.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated tickers (default: all)",
    )
```

- [ ] **Step 2: Add handler and dispatch entry**

Add `"screen": _cmd_screen` to the `handlers` dict (after line 180).

Add the handler function after the other `_cmd_*` functions:

```python
async def _cmd_screen(
    args: argparse.Namespace,
) -> None:
    from backend.pipeline.screener.screen import (
        run_screen,
    )

    tickers = None
    if args.tickers:
        tickers = [
            t.strip() for t in args.tickers.split(",")
        ]
    result = await run_screen(tickers=tickers)
    _logger.info(
        "Screen: scored=%d skipped=%d failed=%d "
        "strong=%d moderate=%d weak=%d (%.1fs)",
        result["scored"],
        result["skipped"],
        result["failed"],
        result["strong"],
        result["moderate"],
        result["weak"],
        result["elapsed_s"],
    )
```

- [ ] **Step 3: Commit**

```bash
git add backend/pipeline/runner.py
git commit -m "feat(cli): add 'screen' command for Piotroski F-Score

PYTHONPATH=.:backend python -m backend.pipeline.runner screen
Optional: --tickers RELIANCE.NS,TCS.NS

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 8: Add API endpoint

**Files:**
- Modify: `backend/insights_models.py` (EOF)
- Modify: `backend/insights_routes.py` (EOF)

- [ ] **Step 1: Add Pydantic models**

Append to `backend/insights_models.py`:

```python


# ---------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------

class PiotroskiRow(BaseModel):
    """Single row in the Piotroski F-Score table."""

    ticker: str
    company_name: str | None = None
    total_score: int = 0
    label: str = "Weak"
    roa_positive: bool = False
    operating_cf_positive: bool = False
    roa_increasing: bool = False
    cf_gt_net_income: bool = False
    leverage_decreasing: bool = False
    current_ratio_increasing: bool = False
    no_dilution: bool = False
    gross_margin_increasing: bool = False
    asset_turnover_increasing: bool = False
    market_cap: int | None = None
    revenue: float | None = None
    avg_volume: int | None = None
    sector: str | None = None
    industry: str | None = None
    score_date: str | None = None


class PiotroskiResponse(BaseModel):
    """Piotroski F-Score tab response."""

    rows: list[PiotroskiRow] = Field(
        default_factory=list,
    )
    sectors: list[str] = Field(
        default_factory=list,
    )
    score_date: str | None = None
```

- [ ] **Step 2: Add endpoint to `insights_routes.py`**

Add imports at the top (after existing imports from `insights_models`):

```python
from insights_models import (
    # ... existing imports ...
    PiotroskiResponse,
    PiotroskiRow,
)
```

Then append the endpoint at the bottom of the file:

```python


# ---------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------


@router.get(
    "/insights/piotroski",
    response_model=PiotroskiResponse,
)
async def get_piotroski(
    min_score: int = Query(0, ge=0, le=9),
    sector: str = Query("all"),
    user: UserContext = Depends(get_current_user),
):
    """Return latest Piotroski F-Score results."""
    cache = get_cache()
    cache_key = (
        f"cache:insights:piotroski:"
        f"{min_score}:{sector}"
    )
    cached = await cache.get_json(cache_key)
    if cached:
        return PiotroskiResponse(**cached)

    repo = _get_stock_repo()
    df = repo.get_piotroski_scores()

    if df.empty:
        return PiotroskiResponse()

    # Filter to latest score_date
    latest_date = df["score_date"].max()
    df = df[df["score_date"] == latest_date]

    # Apply filters
    if min_score > 0:
        df = df[df["total_score"] >= min_score]
    if sector != "all":
        df = df[df["sector"] == sector]

    rows = []
    for _, r in df.iterrows():
        rows.append(
            PiotroskiRow(
                ticker=r["ticker"],
                company_name=r.get("company_name"),
                total_score=int(
                    r.get("total_score", 0)
                ),
                label=r.get("label", "Weak"),
                roa_positive=bool(
                    r.get("roa_positive", False)
                ),
                operating_cf_positive=bool(
                    r.get(
                        "operating_cf_positive",
                        False,
                    )
                ),
                roa_increasing=bool(
                    r.get("roa_increasing", False)
                ),
                cf_gt_net_income=bool(
                    r.get("cf_gt_net_income", False)
                ),
                leverage_decreasing=bool(
                    r.get(
                        "leverage_decreasing", False
                    )
                ),
                current_ratio_increasing=bool(
                    r.get(
                        "current_ratio_increasing",
                        False,
                    )
                ),
                no_dilution=bool(
                    r.get("no_dilution", False)
                ),
                gross_margin_increasing=bool(
                    r.get(
                        "gross_margin_increasing",
                        False,
                    )
                ),
                asset_turnover_increasing=bool(
                    r.get(
                        "asset_turnover_increasing",
                        False,
                    )
                ),
                market_cap=_safe_int(
                    r.get("market_cap")
                ),
                revenue=_safe(r.get("revenue")),
                avg_volume=_safe_int(
                    r.get("avg_volume")
                ),
                sector=r.get("sector"),
                industry=r.get("industry"),
                score_date=str(latest_date),
            )
        )

    # Unique sectors for filter dropdown
    all_sectors = sorted(
        {
            r.sector
            for r in rows
            if r.sector
        }
    )

    resp = PiotroskiResponse(
        rows=rows,
        sectors=all_sectors,
        score_date=str(latest_date),
    )
    await cache.set_json(
        cache_key, resp.model_dump(), ttl=TTL_STABLE,
    )
    return resp
```

- [ ] **Step 3: Commit**

```bash
git add backend/insights_models.py backend/insights_routes.py
git commit -m "feat(api): add /insights/piotroski endpoint

PiotroskiRow + PiotroskiResponse Pydantic models.
GET /insights/piotroski?min_score=0&sector=all
Redis cached (300s). Not user-scoped — universe screener.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 9: Frontend — types, hook, badge, tab

**Files:**
- Modify: `frontend/lib/types.ts` (EOF)
- Modify: `frontend/hooks/useInsightsData.ts` (EOF)
- Create: `frontend/components/insights/PiotroskiBadge.tsx`
- Modify: `frontend/app/(authenticated)/analytics/insights/page.tsx`

- [ ] **Step 1: Add TypeScript types**

Append to `frontend/lib/types.ts`:

```typescript

// ---------------------------------------------------------------
// Piotroski F-Score
// ---------------------------------------------------------------

export interface PiotroskiRow {
  ticker: string;
  company_name: string | null;
  total_score: number;
  label: string;
  roa_positive: boolean;
  operating_cf_positive: boolean;
  roa_increasing: boolean;
  cf_gt_net_income: boolean;
  leverage_decreasing: boolean;
  current_ratio_increasing: boolean;
  no_dilution: boolean;
  gross_margin_increasing: boolean;
  asset_turnover_increasing: boolean;
  market_cap: number | null;
  revenue: number | null;
  avg_volume: number | null;
  sector: string | null;
  industry: string | null;
  score_date: string | null;
}

export interface PiotroskiResponse {
  rows: PiotroskiRow[];
  sectors: string[];
  score_date: string | null;
}
```

- [ ] **Step 2: Add `usePiotroski` hook**

Append to `frontend/hooks/useInsightsData.ts`:

Add the import at the top:

```typescript
import type {
  // ... existing imports ...
  PiotroskiResponse,
} from "@/lib/types";
```

Then append the hook:

```typescript

export function usePiotroski(
  minScore: number = 0,
  sector: string = "all",
): InsightsData<PiotroskiResponse> {
  const params = new URLSearchParams();
  if (minScore > 0)
    params.set("min_score", String(minScore));
  if (sector !== "all")
    params.set("sector", sector);
  const qs = params.toString();
  return useInsightsFetch<PiotroskiResponse>(
    `/insights/piotroski${qs ? `?${qs}` : ""}`,
  );
}
```

- [ ] **Step 3: Create `PiotroskiBadge.tsx`**

Create `frontend/components/insights/PiotroskiBadge.tsx`:

```tsx
"use client";
/**
 * Piotroski F-Score badge — color-coded pill.
 *
 * 8-9: green (Strong)
 * 5-7: amber (Moderate)
 * 0-4: red (Weak)
 */

interface PiotroskiBadgeProps {
  score: number;
  label: string;
}

export function PiotroskiBadge({
  score,
  label,
}: PiotroskiBadgeProps) {
  let cls =
    "inline-flex items-center gap-1 " +
    "px-2 py-0.5 rounded-full text-xs " +
    "font-semibold ";
  if (score >= 8) {
    cls +=
      "bg-emerald-100 text-emerald-700 " +
      "dark:bg-emerald-900/30 " +
      "dark:text-emerald-400";
  } else if (score >= 5) {
    cls +=
      "bg-amber-100 text-amber-700 " +
      "dark:bg-amber-900/30 " +
      "dark:text-amber-400";
  } else {
    cls +=
      "bg-red-100 text-red-700 " +
      "dark:bg-red-900/30 dark:text-red-400";
  }
  return (
    <span className={cls} title={label}>
      {score}
    </span>
  );
}
```

- [ ] **Step 4: Add Piotroski tab to Insights page**

In `frontend/app/(authenticated)/analytics/insights/page.tsx`:

**4a. Add imports** (after existing imports, around line 22):

```typescript
import { usePiotroski } from "@/hooks/useInsightsData";
import { PiotroskiBadge } from "@/components/insights/PiotroskiBadge";
import type { PiotroskiRow } from "@/lib/types";
```

**4b. Extend TabId** (line 46-53) — add `"piotroski"`:

```typescript
type TabId =
  | "screener"
  | "targets"
  | "dividends"
  | "risk"
  | "sectors"
  | "correlation"
  | "quarterly"
  | "piotroski";
```

**4c. Add to TABS array** (line 55-63) — add after quarterly:

```typescript
  { id: "piotroski", label: "Piotroski F-Score" },
```

**4d. Add column definitions** (after `screenerCols` around line 326):

```typescript
const piotroskiCols: Column<PiotroskiRow>[] = [
  { key: "ticker", label: "Ticker" },
  {
    key: "company_name",
    label: "Company",
    render: (r) => r.company_name ?? "\u2014",
  },
  {
    key: "total_score",
    label: "Score",
    numeric: true,
    render: (r) => (
      <PiotroskiBadge
        score={r.total_score}
        label={r.label}
      />
    ),
  },
  {
    key: "label",
    label: "Rating",
    render: (r) => r.label,
  },
  {
    key: "sector",
    label: "Sector",
    render: (r) => r.sector ?? "\u2014",
  },
  {
    key: "market_cap",
    label: "MCap (Cr)",
    numeric: true,
    render: (r) =>
      r.market_cap != null
        ? (r.market_cap / 1e7).toFixed(0)
        : "\u2014",
  },
  {
    key: "revenue",
    label: "Rev (Cr)",
    numeric: true,
    render: (r) =>
      r.revenue != null
        ? (r.revenue / 1e7).toFixed(0)
        : "\u2014",
  },
  {
    key: "avg_volume",
    label: "Avg Vol",
    numeric: true,
    render: (r) =>
      r.avg_volume != null
        ? r.avg_volume.toLocaleString()
        : "\u2014",
  },
  {
    key: "action",
    label: "Action",
    sortable: false,
    render: (r) => (
      <button
        title="Stock Analysis"
        onClick={() =>
          window.open(
            `/analytics/analysis?ticker=${encodeURIComponent(r.ticker)}&tab=analysis`,
            "_blank",
          )
        }
        className="flex h-7 w-7 items-center
          justify-center rounded-md border
          border-gray-200 text-gray-400
          transition-all hover:border-indigo-400
          hover:bg-indigo-50 hover:text-indigo-600
          dark:border-gray-700 dark:text-gray-500
          dark:hover:border-indigo-500
          dark:hover:bg-indigo-500/10
          dark:hover:text-indigo-400"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 20 20"
          fill="currentColor"
          className="h-3.5 w-3.5"
        >
          <path d="M15.5 2A1.5 1.5 0 0014 3.5v13a1.5 1.5 0 001.5 1.5h1a1.5 1.5 0 001.5-1.5v-13A1.5 1.5 0 0016.5 2h-1zM9.5 6A1.5 1.5 0 008 7.5v9A1.5 1.5 0 009.5 18h1a1.5 1.5 0 001.5-1.5v-9A1.5 1.5 0 0010.5 6h-1zM3.5 10A1.5 1.5 0 002 11.5v5A1.5 1.5 0 003.5 18h1A1.5 1.5 0 006 16.5v-5A1.5 1.5 0 004.5 10h-1z" />
        </svg>
      </button>
    ),
  },
];
```

**4e. Add `PiotroskiTab` component** (before `InsightsPage`, around line 1200):

```typescript
function PiotroskiTab() {
  const [sector, setSector] = useState("all");
  const [minScore, setMinScore] = useState(0);
  const data = usePiotroski(minScore, sector);

  const filtered = useMemo(() => {
    if (!data.value?.rows) return [];
    return data.value.rows;
  }, [data.value]);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return (
      <WidgetError
        message={data.error}
        data-testid="insights-error"
      />
    );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {/* Sector filter */}
        {(data.value?.sectors ?? []).length > 0 && (
          <select
            data-testid="piotroski-sector-filter"
            value={sector}
            onChange={(e) =>
              setSector(e.target.value)
            }
            className="rounded-lg border border-gray-300
              dark:border-gray-600 bg-white dark:bg-gray-800
              px-2.5 py-1.5 text-sm
              text-gray-700 dark:text-gray-200
              focus:outline-none focus:ring-2
              focus:ring-indigo-500/40"
          >
            <option value="all">All Sectors</option>
            {(data.value?.sectors ?? []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        )}
        {/* Min score filter */}
        <select
          data-testid="piotroski-score-filter"
          value={minScore}
          onChange={(e) =>
            setMinScore(Number(e.target.value))
          }
          className="rounded-lg border border-gray-300
            dark:border-gray-600 bg-white dark:bg-gray-800
            px-2.5 py-1.5 text-sm
            text-gray-700 dark:text-gray-200
            focus:outline-none focus:ring-2
            focus:ring-indigo-500/40"
        >
          <option value={0}>All Scores</option>
          <option value={8}>Strong (8-9)</option>
          <option value={5}>Moderate+ (5-9)</option>
        </select>
        {data.value?.score_date && (
          <span className="text-xs text-gray-400 dark:text-gray-500 ml-auto">
            Scored: {data.value.score_date}
          </span>
        )}
      </div>
      <InsightsTable<PiotroskiRow>
        columns={piotroskiCols}
        rows={filtered}
        defaultSort={{
          col: "total_score",
          dir: "desc",
        }}
      />
    </div>
  );
}
```

**4f. Add case to `renderTab` switch** (line 1225, after quarterly case):

```typescript
      case "piotroski":
        return <PiotroskiTab />;
```

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/types.ts \
        frontend/hooks/useInsightsData.ts \
        frontend/components/insights/PiotroskiBadge.tsx \
        frontend/app/\(authenticated\)/analytics/insights/page.tsx
git commit -m "feat(ui): Piotroski F-Score tab on Insights page

PiotroskiBadge: green/amber/red pill for score 8-9/5-7/0-4.
PiotroskiTab: sector + min-score filters, sortable table.
usePiotroski hook with SWR caching.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 10: Integration test for screener

**Files:**
- Create: `tests/backend/test_screener.py`

- [ ] **Step 1: Write integration test**

Create `tests/backend/test_screener.py`:

```python
"""Integration tests for Piotroski screen orchestrator."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.pipeline.screener.screen import (
    _aggregate_annual,
)


class TestAggregateAnnual:
    """Tests for _aggregate_annual()."""

    def test_empty_df(self):
        """Empty DataFrame → empty dict."""
        df = pd.DataFrame()
        assert _aggregate_annual(df) == {}

    def test_merges_statements(self):
        """Income + balance + cashflow merge by year."""
        rows = [
            {
                "statement_type": "income",
                "fiscal_year": 2025,
                "quarter_end": "2025-03-31",
                "revenue": 100,
                "net_income": 20,
                "gross_profit": 50,
                "operating_cashflow": None,
                "total_assets": None,
                "total_debt": None,
                "current_assets": None,
                "current_liabilities": None,
                "shares_outstanding": None,
            },
            {
                "statement_type": "balance",
                "fiscal_year": 2025,
                "quarter_end": "2025-03-31",
                "revenue": None,
                "net_income": None,
                "gross_profit": None,
                "operating_cashflow": None,
                "total_assets": 1000,
                "total_debt": 200,
                "current_assets": 500,
                "current_liabilities": 300,
                "shares_outstanding": 1_000_000,
            },
            {
                "statement_type": "cashflow",
                "fiscal_year": 2025,
                "quarter_end": "2025-03-31",
                "revenue": None,
                "net_income": None,
                "gross_profit": None,
                "operating_cashflow": 150,
                "total_assets": None,
                "total_debt": None,
                "current_assets": None,
                "current_liabilities": None,
                "shares_outstanding": None,
            },
        ]
        df = pd.DataFrame(rows)
        result = _aggregate_annual(df)
        assert 2025 in result
        y = result[2025]
        assert y["revenue"] == 100
        assert y["net_income"] == 20
        assert y["total_assets"] == 1000
        assert y["operating_cashflow"] == 150
        assert y["shares_outstanding"] == 1_000_000

    def test_sums_quarterly_income(self):
        """4 quarters of income are summed."""
        rows = []
        for q in range(1, 5):
            rows.append(
                {
                    "statement_type": "income",
                    "fiscal_year": 2025,
                    "quarter_end": f"2025-{q * 3:02d}-30",
                    "revenue": 100,
                    "net_income": 25,
                    "gross_profit": 50,
                    "operating_cashflow": None,
                    "total_assets": None,
                    "total_debt": None,
                    "current_assets": None,
                    "current_liabilities": None,
                    "shares_outstanding": None,
                }
            )
        df = pd.DataFrame(rows)
        result = _aggregate_annual(df)
        assert result[2025]["revenue"] == 400
        assert result[2025]["net_income"] == 100
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/backend/test_screener.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/backend/test_screener.py
git commit -m "test(screener): integration tests for _aggregate_annual

Tests empty df, statement merging, quarterly income summation.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 11: Run lint and full test suite

- [ ] **Step 1: Lint backend**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
black backend/pipeline/screener/ backend/insights_models.py backend/insights_routes.py backend/tools/stock_data_tool.py stocks/create_tables.py stocks/repository.py
isort backend/pipeline/screener/ backend/insights_models.py backend/insights_routes.py backend/tools/stock_data_tool.py stocks/create_tables.py stocks/repository.py --profile black
flake8 backend/pipeline/screener/ backend/insights_models.py backend/insights_routes.py backend/tools/stock_data_tool.py stocks/create_tables.py stocks/repository.py
```

- [ ] **Step 2: Run Piotroski + screener tests**

```bash
python -m pytest tests/backend/test_piotroski.py tests/backend/test_screener.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Lint frontend**

```bash
cd frontend && npx eslint app/\(authenticated\)/analytics/insights/page.tsx components/insights/PiotroskiBadge.tsx hooks/useInsightsData.ts lib/types.ts --fix
```

- [ ] **Step 4: Fix any issues, commit**

```bash
git add -A
git commit -m "chore: lint fixes for Piotroski feature

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```
