# Swing Setups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new "Swing Setups" tab in Advanced Analytics that emits three ranked, user-scoped watchlists per trading day (Bull / Sideways / Bearish), with the active filter rules surfaced as a backend-sourced methodology panel on the page.

**Architecture:** New router endpoints `/v1/advanced-analytics/swing-setups[/methodology]` mounted alongside the seven existing AA reports. A focused sub-module (`advanced_analytics_swing.py`) owns the regime threshold constants, methodology block, regime filters, and rank functions — making it the single source of truth (route consumes it, frontend renders it). Reuses the existing `AdvancedRow` pipeline (no new Iceberg tables); extends with 5 computed columns + 3 rec-engine join fields. Frontend tab composes regime-pill control + collapsible methodology strip + the shared `<AdvancedAnalyticsTable />` with per-regime column selection.

**Tech Stack:** FastAPI · SQLAlchemy 2.0 async · PyIceberg · DuckDB · pydantic v2 · Next.js 16 (RSC + Client) · SWR · TypeScript · vitest · Playwright

**Spec:** `docs/superpowers/specs/2026-05-12-swing-setups-design.md`

**Branch:** `feature/aa-swing-setups` (already created, holds the spec commits)

---

## File Structure

### Created

| Path | Responsibility |
|---|---|
| `backend/advanced_analytics_swing.py` | Methodology constants per regime · `build_methodology(regime)` · regime filter predicates · rank functions · bullish category set |
| `backend/tests/test_advanced_analytics_swing.py` | Unit tests for new computed cols, regime filters, rank functions, methodology block |
| `frontend/components/advanced-analytics/SwingSetupsTab.tsx` | Tab composition: pills + methodology panel + table |
| `frontend/components/advanced-analytics/SwingRegimePills.tsx` | Bull / Sideways / Bearish segmented control |
| `frontend/components/advanced-analytics/SwingMethodologyPanel.tsx` | Collapsible "How this list is built" strip rendering backend methodology block |
| `frontend/components/advanced-analytics/__tests__/SwingSetupsTab.test.tsx` | vitest — tab interactions, pill switch, methodology panel |
| `frontend/components/advanced-analytics/__tests__/SwingMethodologyPanel.test.tsx` | vitest — panel render, collapse persistence, degraded rec-gate strike-through |
| `frontend/hooks/useSwingSetups.ts` | SWR hook for swing-setups endpoint + methodology endpoint |
| `frontend/types/swingSetups.ts` | TypeScript shapes for response + methodology block |
| `e2e/pages/frontend/AdvancedAnalyticsSwingPage.ts` | Page object |
| `e2e/tests/frontend/aa-swing-setups.spec.ts` | E2E spec |
| `.serena/memories/shared/architecture/swing-setups-design.md` | Architecture memory |

### Modified

| Path | Change |
|---|---|
| `backend/advanced_analytics_models.py` | Add 8 optional fields to `AdvancedRow` (5 computed + 3 rec-join) + new `SwingMethodology` + `SwingSetupsResponse` models |
| `backend/advanced_analytics_routes.py` | Add `_death_cross_days_ago`, `_rolling_band_20d_prev`, `_rsi_lookback`, `_load_latest_recommendations`, wire into `_build_row`, add 2 new endpoints, extend `_CACHE_INVALIDATION_MAP` |
| `frontend/components/advanced-analytics/AdvancedAnalyticsTabs.tsx` | Register `swing-setups` tab in the strip |
| `frontend/lib/aaColumnCatalogs.ts` (or wherever the AA catalog lives — verify in T22) | Add default visible cols per regime + lock `ticker` |
| `e2e/utils/selectors.ts` | Add testids for tab, pills, methodology panel, empty state |
| `CLAUDE.md` | Add §9 pattern-index row "Add a swing-setup regime → swing-setups-design memory" |
| `PROGRESS.md` | Dated entry |

---

## Conventions used throughout

- **Black/isort line-length 79** (CLAUDE.md §4.2 #9). All Python blocks below respect this.
- **`X | None` not `Optional[X]`** (§4.2 #11).
- **No bare `print()` — use `_logger`** (§4.2 #10).
- **`safe_float` for any NaN-prone numeric gate** (§6.1).
- **Test naming**: `test_<unit>_<scenario>` for unit; `test_<route>_<case>` for integration.
- **Commits**: small, focused, message follows existing style (`feat:`, `test:`, `docs:`). Co-Authored-By line is added by the existing commit hook setup.
- **Backend restart after new route / new field on `response_model` / new `app.include_router`** — restart container, not just uvicorn reload (§6.2). Reminded in route tasks.

---

## Task 0: Pin the bullish category set (DB query)

**Files:** none (data verification step)

- [ ] **Step 1: Query distinct categories in `stocks.recommendations`**

Run from project root:

```bash
docker compose exec postgres psql -U postgres -d aiagent \
  -c "SELECT category, COUNT(*) FROM stocks.recommendations \
      WHERE status = 'active' \
      GROUP BY category ORDER BY 2 DESC;"
```

Expected: a list of category strings with counts. Record them.

- [x] **Step 2: Decide the bullish set** — **RESOLVED 2026-05-12**

Bullish set pinned: `{"offensive", "value", "growth", "hold_accumulate"}`.

Rationale: rec engine uses a portfolio-action vocabulary, not stock-rating; these four are the categories whose semantics map to "go long this name." Other categories surfaced in the DB (`defensive`, `rebalance`, `risk_alert`, `gap_fill`, `diversification`) are either direction-agnostic or bearish. Severity field is surfaced on the row for analysis but is NOT used as a hard gate in Phase A (Phase A.5 may revisit if hit-rate data suggests tightening).

- [x] **Step 3: No commit yet** — done as part of plan-edit; carry the set forward into Task 6 constants.

---

## Task 1: Extend `AdvancedRow` model with 8 new fields

**Files:**
- Modify: `backend/advanced_analytics_models.py`
- Test: `backend/tests/test_advanced_analytics_swing.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_advanced_analytics_swing.py`:

```python
"""Tests for swing-setups feature."""

from __future__ import annotations

from advanced_analytics_models import AdvancedRow


def test_advanced_row_swing_fields_default_none() -> None:
    """New swing fields default to None for back-compat with the
    seven existing AA reports that don't populate them.
    """
    row = AdvancedRow(ticker="TCS.NS")
    assert row.death_cross_days_ago is None
    assert row.rolling_low_20d_prev is None
    assert row.rolling_high_20d_prev is None
    assert row.rsi_3d_ago is None
    assert row.rsi_max_10d is None
    assert row.rec_category is None
    assert row.rec_severity is None
    assert row.rec_expected_return_pct is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py::test_advanced_row_swing_fields_default_none -v
```

Expected: FAIL with `AttributeError` or pydantic ValidationError on the unknown field name.

- [ ] **Step 3: Add the 8 fields to `AdvancedRow`**

In `backend/advanced_analytics_models.py`, find the `AdvancedRow` model and add the following fields (preserve existing ordering convention — group computed swing cols together, rec-join cols together):

```python
    # Swing-setup computed columns (Task 2-4).
    death_cross_days_ago: int | None = None
    rolling_low_20d_prev: float | None = None
    rolling_high_20d_prev: float | None = None
    rsi_3d_ago: float | None = None
    rsi_max_10d: float | None = None

    # Recommendation-engine join (Task 10).
    rec_category: str | None = None
    rec_severity: str | None = None
    rec_expected_return_pct: float | None = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py::test_advanced_row_swing_fields_default_none -v
```

Expected: PASS.

- [ ] **Step 5: Restart backend** (response_model field added — §6.2)

```bash
docker compose restart backend && sleep 5
```

- [ ] **Step 6: Commit**

```bash
git add backend/advanced_analytics_models.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): extend AdvancedRow with swing computed + rec-join fields"
```

---

## Task 2: Compute `death_cross_days_ago`

**Files:**
- Modify: `backend/advanced_analytics_routes.py` (add helper next to `_golden_cross_days_ago` at ~line 332)
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_advanced_analytics_swing.py`:

```python
import numpy as np
import pandas as pd

from advanced_analytics_routes import _death_cross_days_ago


def _make_sma_df(s50: list[float], s200: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"SMA_50": s50, "SMA_200": s200})


def test_death_cross_none_when_50_above_200() -> None:
    """No death cross active when SMA-50 is above SMA-200 today."""
    df = _make_sma_df([100, 102, 104], [99, 100, 101])
    assert _death_cross_days_ago(df) is None


def test_death_cross_zero_when_today_is_cross() -> None:
    """Cross today → 0."""
    df = _make_sma_df([100, 101, 99], [98, 100, 100])
    # Yesterday: 50=101 > 200=100. Today: 50=99 < 200=100 → cross today.
    assert _death_cross_days_ago(df) == 0


def test_death_cross_n_days_back() -> None:
    """Cross 2 trading days back → 2."""
    df = _make_sma_df([101, 99, 98, 97], [100, 100, 99, 96])
    # Index 0→1 is cross (101>100 then 99<100). n=4, i=1 → (4-1)-1 = 2.
    assert _death_cross_days_ago(df) == 2


def test_death_cross_sentinel_when_below_entire_window() -> None:
    """SMA-50 below SMA-200 entire window with no cross → 999."""
    df = _make_sma_df([90, 91, 92], [100, 101, 102])
    assert _death_cross_days_ago(df) == 999


def test_death_cross_handles_nan_prefix() -> None:
    """NaN prefix (insufficient warmup) returns 999 sentinel."""
    df = _make_sma_df([np.nan, np.nan, 95], [np.nan, np.nan, 100])
    assert _death_cross_days_ago(df) == 999


def test_death_cross_missing_columns_returns_none() -> None:
    """Missing SMA columns return None safely."""
    df = pd.DataFrame({"close": [100, 101]})
    assert _death_cross_days_ago(df) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k death_cross
```

Expected: FAIL with `ImportError` for `_death_cross_days_ago`.

- [ ] **Step 3: Add the helper to `backend/advanced_analytics_routes.py`**

Insert immediately after `_golden_cross_days_ago` (around line 365):

```python
def _death_cross_days_ago(ind: pd.DataFrame) -> int | None:
    """Trading days since SMA 50 last crossed BELOW SMA 200.

    Mirror of :func:`_golden_cross_days_ago` with inverted
    comparators.

    Returns:
        None — SMA 50 ≥ SMA 200 today (no death cross active).
        0–N  — cross happened N trading rows back; 0 = today.
        999  — SMA 50 has been below SMA 200 for the entire
               window (established bearish, no cross visible).
    """
    s50 = ind["SMA_50"] if "SMA_50" in ind.columns else None
    s200 = ind["SMA_200"] if "SMA_200" in ind.columns else None
    if s50 is None or s200 is None:
        return None

    last50 = s50.iloc[-1]
    last200 = s200.iloc[-1]
    if pd.isna(last50) or pd.isna(last200) or last50 >= last200:
        return None

    n = len(ind)
    for i in range(n - 1, 0, -1):
        v50, v200 = s50.iloc[i], s200.iloc[i]
        p50, p200 = s50.iloc[i - 1], s200.iloc[i - 1]
        if pd.isna(v50) or pd.isna(v200) or pd.isna(p50) or pd.isna(p200):
            return 999
        if v50 < v200 and p50 >= p200:
            return (n - 1) - i

    return 999
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k death_cross
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): _death_cross_days_ago mirror of golden-cross helper"
```

---

## Task 3: Compute `rolling_low_20d_prev` + `rolling_high_20d_prev`

**Files:**
- Modify: `backend/advanced_analytics_routes.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from advanced_analytics_routes import _rolling_band_20d_prev


def test_rolling_band_20d_prev_basic() -> None:
    """20-day rolling band excludes today."""
    # 21 rows: index 0..19 used for the band, index 20 is "today".
    lows = list(range(10, 30)) + [5]  # today_low = 5 (below band)
    highs = list(range(20, 40)) + [50]  # today_high = 50 (above)
    df = pd.DataFrame({"low": lows, "high": highs})
    low, high = _rolling_band_20d_prev(df)
    assert low == 10  # min of 10..29 (indices 0..19)
    assert high == 39  # max of 20..39 (indices 0..19)


def test_rolling_band_short_history_returns_none() -> None:
    """Fewer than 21 rows → cannot exclude today, returns (None, None)."""
    df = pd.DataFrame({
        "low": [10, 11, 12],
        "high": [15, 16, 17],
    })
    assert _rolling_band_20d_prev(df) == (None, None)


def test_rolling_band_handles_nan() -> None:
    """NaN low/high values are ignored in min/max."""
    lows = [float("nan")] * 5 + list(range(10, 25)) + [5]
    highs = [float("nan")] * 5 + list(range(20, 35)) + [50]
    df = pd.DataFrame({"low": lows, "high": highs})
    low, high = _rolling_band_20d_prev(df)
    assert low == 10
    assert high == 34
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k rolling_band
```

Expected: FAIL with ImportError.

- [ ] **Step 3: Implement helper**

Insert after `_death_cross_days_ago`:

```python
def _rolling_band_20d_prev(
    ohlcv: pd.DataFrame,
) -> tuple[float | None, float | None]:
    """20-day rolling (low, high) EXCLUDING the last row (today).

    Returns (None, None) when fewer than 21 rows of history are
    available — caller cannot use the band for breakout detection
    without a clean prior window.
    """
    if "low" not in ohlcv.columns or "high" not in ohlcv.columns:
        return (None, None)
    if len(ohlcv) < 21:
        return (None, None)
    prev_window = ohlcv.iloc[-21:-1]
    low = prev_window["low"].min(skipna=True)
    high = prev_window["high"].max(skipna=True)
    return (
        None if pd.isna(low) else float(low),
        None if pd.isna(high) else float(high),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k rolling_band
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): _rolling_band_20d_prev for sideways + bearish gates"
```

---

## Task 4: Compute `rsi_3d_ago` + `rsi_max_10d`

**Files:**
- Modify: `backend/advanced_analytics_routes.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from advanced_analytics_routes import _rsi_lookback


def test_rsi_lookback_basic() -> None:
    """RSI lookback: today, 3-days-ago, max over last 10."""
    rsi_series = pd.Series(
        [40, 45, 50, 55, 60, 65, 70, 68, 60, 50, 45]
    )
    df = pd.DataFrame({"RSI_14": rsi_series})
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today == 45
    assert three_ago == 68  # index -4 (3 trading days before today)
    assert max_10 == 70  # max over last 10 rows


def test_rsi_lookback_short_series_returns_partial_nones() -> None:
    """<4 rows → three_ago None; <10 rows → max_10 still computes
    over available rows."""
    df = pd.DataFrame({"RSI_14": [40, 50, 60]})
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today == 60
    assert three_ago is None
    assert max_10 == 60  # max of the 3 available


def test_rsi_lookback_missing_column_returns_all_none() -> None:
    df = pd.DataFrame({"close": [100, 101]})
    assert _rsi_lookback(df) == (None, None, None)


def test_rsi_lookback_handles_nan() -> None:
    """NaN today returns None for today; lookback unaffected."""
    df = pd.DataFrame({
        "RSI_14": [40, 50, 60, 65, 55, float("nan")],
    })
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today is None
    assert three_ago == 60
    assert max_10 == 65
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k rsi_lookback
```

Expected: FAIL with ImportError.

- [ ] **Step 3: Implement helper**

Insert after `_rolling_band_20d_prev`:

```python
def _rsi_lookback(
    ind: pd.DataFrame,
) -> tuple[float | None, float | None, float | None]:
    """Return (today_rsi, rsi_3d_ago, rsi_max_10d) for the bearish
    rollover detector. Any value not computable safely is None.
    """
    if "RSI_14" not in ind.columns or len(ind) == 0:
        return (None, None, None)
    s = ind["RSI_14"]
    today = s.iloc[-1]
    today_val = None if pd.isna(today) else float(today)
    three_ago_val: float | None
    if len(s) < 4:
        three_ago_val = None
    else:
        v = s.iloc[-4]
        three_ago_val = None if pd.isna(v) else float(v)
    window = s.iloc[-min(10, len(s)):]
    max_10 = window.max(skipna=True)
    max_10_val = None if pd.isna(max_10) else float(max_10)
    return (today_val, three_ago_val, max_10_val)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k rsi_lookback
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): _rsi_lookback for bearish RSI rollover detection"
```

---

## Task 5: Wire new computed columns into `_build_row`

**Files:**
- Modify: `backend/advanced_analytics_routes.py` (find `_build_row` function — look around line 600-720 based on existing AA route shape)
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Read `_build_row` to locate insertion points**

```bash
grep -n "def _build_row\|death_cross\|golden_cross" backend/advanced_analytics_routes.py
```

Find where `golden_cross_days_ago` is assigned onto the `AdvancedRow` constructor. The 5 new computed cols (`death_cross_days_ago`, `rolling_low_20d_prev`, `rolling_high_20d_prev`, `rsi_3d_ago`, `rsi_max_10d`) need to be passed alongside it.

- [ ] **Step 2: Write the failing integration test**

Append:

```python
from unittest.mock import patch


def test_build_row_includes_swing_computed_cols(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """Smoke: _build_row populates the 5 swing computed cols when
    OHLCV + indicators are present."""
    import advanced_analytics_routes as aar

    # Construct a 30-row OHLCV + 30-row indicators frame.
    n = 30
    ohlcv = pd.DataFrame({
        "date": pd.date_range("2026-04-01", periods=n).date,
        "open": [100.0] * n,
        "high": [105.0 + i for i in range(n)],
        "low": [95.0 - i * 0.1 for i in range(n)],
        "close": [100.0 + i * 0.5 for i in range(n)],
        "volume": [1_000_000] * n,
    })
    ind = pd.DataFrame({
        "SMA_50": [110.0] * n,
        "SMA_200": [105.0] * n,
        "RSI_14": [60.0 + i for i in range(n)],
    })

    # Minimal stub delivery / fundamentals.
    row = aar._build_row(  # type: ignore[attr-defined]
        ticker="TCS.NS",
        ohlcv=ohlcv,
        indicators=ind,
        delivery=pd.DataFrame(),
        fundamentals={},
        company_info={},
        promoter={},
        piotroski={},
        events={},
        as_of=ohlcv["date"].iloc[-1],
    )
    assert row.rolling_low_20d_prev is not None
    assert row.rolling_high_20d_prev is not None
    assert row.rsi_3d_ago is not None
    assert row.rsi_max_10d is not None
    # SMA_50 > SMA_200 → death_cross None.
    assert row.death_cross_days_ago is None
```

*Note: the exact signature of `_build_row` must be cross-checked at this task's start — replace the kwargs above with whatever the real signature uses. If `_build_row` doesn't accept these as kwargs but reads from a context dict, adapt accordingly.*

- [ ] **Step 3: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py::test_build_row_includes_swing_computed_cols -v
```

Expected: FAIL — fields are None because not yet populated.

- [ ] **Step 4: Modify `_build_row` to populate the 5 cols**

In `backend/advanced_analytics_routes.py`, inside `_build_row` where `golden_cross_days_ago` is computed, add immediately after:

```python
    death_cross = _death_cross_days_ago(indicators)
    rb_low, rb_high = _rolling_band_20d_prev(ohlcv)
    rsi_today, rsi_3d, rsi_max10 = _rsi_lookback(indicators)
```

Then in the `AdvancedRow(...)` constructor call at the bottom of `_build_row`, add:

```python
        death_cross_days_ago=death_cross,
        rolling_low_20d_prev=rb_low,
        rolling_high_20d_prev=rb_high,
        rsi_3d_ago=rsi_3d,
        rsi_max_10d=rsi_max10,
```

Note: the existing `rsi` field is computed elsewhere as `rsi_today` equivalent — don't double-assign.

- [ ] **Step 5: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py::test_build_row_includes_swing_computed_cols -v
```

Expected: PASS.

- [ ] **Step 6: Re-run the full existing AA test suite to ensure no regression**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics*.py -v
```

Expected: all green. If failures appear, they are likely tests asserting the pre-Task-1 shape of `AdvancedRow` — fix by passing the new fields explicitly as `None` where needed.

- [ ] **Step 7: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): wire 5 swing-setup computed cols into _build_row"
```

---

## Task 6: Create `advanced_analytics_swing.py` — methodology module

**Files:**
- Create: `backend/advanced_analytics_swing.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from advanced_analytics_swing import (
    BULLISH_CATEGORIES,
    REGIMES,
    build_methodology,
)


def test_regimes_constant() -> None:
    """Regime literal set is exactly the three published values."""
    assert REGIMES == ("bull", "sideways", "bearish")


def test_bullish_categories_non_empty() -> None:
    """The bullish set is pinned at module level (Task 0)."""
    assert len(BULLISH_CATEGORIES) >= 2
    assert all(isinstance(c, str) for c in BULLISH_CATEGORIES)


def test_build_methodology_bull_shape() -> None:
    """Bull methodology has all required sub-fields and ≥ 8 gates."""
    m = build_methodology("bull")
    assert m["regime"] == "bull"
    assert isinstance(m["summary"], str) and len(m["summary"]) > 20
    assert isinstance(m["gates"], list)
    assert len(m["gates"]) >= 8
    for g in m["gates"]:
        assert "label" in g and "rule" in g and "why" in g
        assert g["rule"]  # non-empty
    assert "formula" in m["rank"]
    assert m["rank"]["direction"] == "DESC"
    assert m["rank"]["cap"] == 25


def test_build_methodology_sideways_shape() -> None:
    m = build_methodology("sideways")
    assert m["regime"] == "sideways"
    assert len(m["gates"]) >= 6
    assert m["rank"]["direction"] == "ASC"


def test_build_methodology_bearish_shape() -> None:
    m = build_methodology("bearish")
    assert m["regime"] == "bearish"
    assert len(m["gates"]) >= 5
    assert m["rank"]["direction"] == "DESC"


def test_build_methodology_unknown_regime_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        build_methodology("noisy")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k methodology
```

Expected: FAIL with ImportError on `advanced_analytics_swing`.

- [ ] **Step 3: Create the module**

`backend/advanced_analytics_swing.py`:

```python
"""Swing Setups — regime definitions, thresholds, methodology.

Single source of truth for the bull/sideways/bearish regime filters
and the human-readable methodology block surfaced on the page.
Anyone tuning thresholds edits ONE place; the on-page explanation
and the filter behaviour move in lockstep.

Bullish category set was pinned in plan Task 0 via:
    SELECT category, COUNT(*) FROM stocks.recommendations
    WHERE status = 'active' GROUP BY category;

If categories shift over time, update :data:`BULLISH_CATEGORIES`
and the snapshot test in test_advanced_analytics_swing.py.
"""

from __future__ import annotations

from typing import Any, Literal

Regime = Literal["bull", "sideways", "bearish"]
REGIMES: tuple[Regime, ...] = ("bull", "sideways", "bearish")

# Pinned 2026-05-12 from DB inspection (Task 0). Rec engine uses a
# portfolio-action vocabulary (not stock-rating); these four
# categories map semantically to "go long this name". Other live
# categories (defensive, rebalance, risk_alert, gap_fill,
# diversification) are direction-agnostic or bearish. Severity is
# NOT used as a hard gate in Phase A.
BULLISH_CATEGORIES: frozenset[str] = frozenset({
    "offensive",
    "value",
    "growth",
    "hold_accumulate",
})

# ----- Bull regime thresholds -----
BULL_VOL_MIN = 2.0
BULL_VOL_MAX = 5.0
BULL_RSI_MAX = 70.0
BULL_PSCORE_MIN = 5
BULL_PLEDGED_MAX = 10.0
BULL_RANGE_MAX = 0.95  # today_ltp / week52_high
BULL_GOLDEN_CROSS_FRESH_DAYS = 30

# ----- Sideways regime thresholds -----
SIDEWAYS_MA_CONV_MAX = 0.05  # |sma_50 - sma_200| / sma_200
SIDEWAYS_PRICE_NEAR_SMA50 = 0.03
SIDEWAYS_RSI_MIN = 40.0
SIDEWAYS_RSI_MAX = 60.0
SIDEWAYS_VOL_MIN = 0.7
SIDEWAYS_VOL_MAX = 1.3
SIDEWAYS_NOT_FLOOR_INR = 50_000_000.0  # ₹5 crore
SIDEWAYS_NOT_FLOOR_USD = 600_000.0
SIDEWAYS_PSCORE_MIN = 4

# ----- Bearish regime thresholds -----
BEARISH_DEATH_CROSS_FRESH_DAYS = 60
BEARISH_RSI_MAX_RECENT = 60.0
BEARISH_RSI_TODAY_MAX = 50.0
BEARISH_FLOOR_RATIO = 1.05  # today_ltp / week52_low
BEARISH_NOT_FLOOR_INR = 50_000_000.0
BEARISH_NOT_FLOOR_USD = 600_000.0

# ----- Cap -----
SWING_CAP = 25


def _bull_gates() -> list[dict[str, str]]:
    return [
        {
            "label": "Trend stack",
            "rule": (
                "today_ltp > sma_50 > sma_200 OR "
                f"golden_cross_days_ago ≤ {BULL_GOLDEN_CROSS_FRESH_DAYS}"
            ),
            "why": "Establishes an uptrend or a fresh reversal.",
        },
        {
            "label": "Volume sweet spot",
            "rule": (
                f"{BULL_VOL_MIN} ≤ today_x_vol ≤ {BULL_VOL_MAX}"
            ),
            "why": (
                "Below 2× lacks conviction; above 5× is usually "
                "news-spike / exhaustion."
            ),
        },
        {
            "label": "Delivery confirmation",
            "rule": "current_dpc > avg_20d_dpc",
            "why": (
                "Today's delivery % above 20-day average — real "
                "buying, not just churn."
            ),
        },
        {
            "label": "Accumulation trend",
            "rule": "x_dv_20d > 1",
            "why": "20-day delivery quantity trending up.",
        },
        {
            "label": "Not exhausted",
            "rule": f"rsi < {BULL_RSI_MAX}",
            "why": "Leaves room before momentum reverses.",
        },
        {
            "label": "Quality floor",
            "rule": (
                f"pscore ≥ {BULL_PSCORE_MIN} AND "
                f"pledged_pct < {BULL_PLEDGED_MAX}"
            ),
            "why": "Filters out distressed names.",
        },
        {
            "label": "Room to run",
            "rule": (
                f"today_ltp / week52_high < {BULL_RANGE_MAX}"
            ),
            "why": "Not already at the top of the 52-week range.",
        },
        {
            "label": "Rec-engine bullish",
            "rule": (
                "rec_category ∈ "
                f"{sorted(BULLISH_CATEGORIES)}"
            ),
            "why": (
                "Rec engine independently confirms the long "
                "thesis (offensive / value / growth / "
                "hold_accumulate). Skipped if user has no rec run "
                "this month — chip surfaced."
            ),
        },
    ]


def _sideways_gates() -> list[dict[str, str]]:
    return [
        {
            "label": "MA convergence",
            "rule": (
                "|sma_50 - sma_200| / sma_200 < "
                f"{SIDEWAYS_MA_CONV_MAX}"
            ),
            "why": "MAs converged — no directional trend.",
        },
        {
            "label": "Price near SMA-50",
            "rule": (
                "|today_ltp - sma_50| / sma_50 < "
                f"{SIDEWAYS_PRICE_NEAR_SMA50}"
            ),
            "why": "Anchored to the mean, not on an edge.",
        },
        {
            "label": "RSI band",
            "rule": (
                f"{SIDEWAYS_RSI_MIN} ≤ rsi ≤ {SIDEWAYS_RSI_MAX}"
            ),
            "why": "Mid-band RSI — no momentum either way.",
        },
        {
            "label": "Neutral volume",
            "rule": (
                f"{SIDEWAYS_VOL_MIN} ≤ today_x_vol ≤ "
                f"{SIDEWAYS_VOL_MAX}"
            ),
            "why": "No surge, no drought — true consolidation.",
        },
        {
            "label": "Liquidity floor",
            "rule": (
                f"today_not > ₹{SIDEWAYS_NOT_FLOOR_INR:,.0f} "
                f"(IN) / ${SIDEWAYS_NOT_FLOOR_USD:,.0f} (US)"
            ),
            "why": (
                "Avoid illiquid names; native-currency notional."
            ),
        },
        {
            "label": "Basic quality",
            "rule": f"pscore ≥ {SIDEWAYS_PSCORE_MIN}",
            "why": "Skips junk-tier consolidators.",
        },
    ]


def _bearish_gates() -> list[dict[str, str]]:
    return [
        {
            "label": "Death-cross active",
            "rule": (
                "sma_50 < sma_200 AND death_cross_days_ago ≤ "
                f"{BEARISH_DEATH_CROSS_FRESH_DAYS}"
            ),
            "why": "Fresh structural downtrend, not stale weakness.",
        },
        {
            "label": "RSI rollover",
            "rule": (
                f"rsi_max_10d ≥ {BEARISH_RSI_MAX_RECENT} AND "
                f"today_rsi ≤ {BEARISH_RSI_TODAY_MAX} AND "
                "today_rsi < rsi_3d_ago"
            ),
            "why": "Strength broken and still declining.",
        },
        {
            "label": "Lower-low break",
            "rule": "today_low < rolling_low_20d_prev",
            "why": "Decisive break of 20-day floor.",
        },
        {
            "label": "Room to fall",
            "rule": (
                f"today_ltp / week52_low > {BEARISH_FLOOR_RATIO}"
            ),
            "why": "Not already capitulated — swing-shortable.",
        },
        {
            "label": "Liquidity floor",
            "rule": (
                f"today_not > ₹{BEARISH_NOT_FLOOR_INR:,.0f} (IN) / "
                f"${BEARISH_NOT_FLOOR_USD:,.0f} (US)"
            ),
            "why": "Avoid illiquid noise masking as breakdowns.",
        },
    ]


_SUMMARY = {
    "bull": (
        "Trend-up stocks with fresh delivery-backed demand "
        "confirmed by the LLM recommendation engine."
    ),
    "sideways": (
        "Range-bound, liquid stocks oscillating around SMA-50 "
        "with neutral momentum — mean-reversion candidates."
    ),
    "bearish": (
        "Active downtrends with RSI rolling over and breaking "
        "below their 20-day low — swing-shortable structure."
    ),
}

_RANK = {
    "bull": {
        "formula": (
            "max(rec_expected_return_pct, 0) * x_dv_20d * "
            "today_x_vol"
        ),
        "direction": "DESC",
        "cap": SWING_CAP,
        "degraded": (
            "When no rec run for user this month, reduces to "
            "x_dv_20d * today_x_vol."
        ),
    },
    "sideways": {
        "formula": (
            "min(today_ltp - rolling_low_20d_prev, "
            "rolling_high_20d_prev - today_ltp) / today_ltp"
        ),
        "direction": "ASC",
        "cap": SWING_CAP,
        "degraded": None,
    },
    "bearish": {
        "formula": (
            "(1 / (death_cross_days_ago + 1)) * "
            "max(0, 60 - today_rsi) * "
            "(rolling_low_20d_prev - today_low) / "
            "rolling_low_20d_prev"
        ),
        "direction": "DESC",
        "cap": SWING_CAP,
        "degraded": None,
    },
}


def build_methodology(regime: Regime) -> dict[str, Any]:
    """Return the structured methodology block for a regime.

    Consumed by the route as the ``methodology`` field of the
    response and by the standalone
    ``/swing-setups/methodology?regime=...`` endpoint.
    """
    if regime == "bull":
        gates = _bull_gates()
    elif regime == "sideways":
        gates = _sideways_gates()
    elif regime == "bearish":
        gates = _bearish_gates()
    else:
        raise ValueError(f"unknown regime: {regime!r}")
    return {
        "regime": regime,
        "summary": _SUMMARY[regime],
        "gates": gates,
        "rank": _RANK[regime],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k methodology
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_swing.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): methodology module — regime thresholds + build_methodology"
```

---

## Task 7: Bull-regime filter + rank

**Files:**
- Modify: `backend/advanced_analytics_swing.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from advanced_analytics_models import AdvancedRow
from advanced_analytics_swing import (
    passes_bull,
    rank_bull,
)


def _bull_row(**overrides: Any) -> AdvancedRow:
    base = dict(
        ticker="TCS.NS",
        today_ltp=120.0,
        sma_50=110.0,
        sma_200=100.0,
        golden_cross_days_ago=999,
        today_x_vol=3.0,
        current_dpc=55.0,
        avg_20d_dpc=45.0,
        x_dv_20d=1.5,
        rsi=60.0,
        pscore=7,
        pledged_pct=2.0,
        week52_high=150.0,
        rec_category="offensive",
        rec_severity="high",
        rec_expected_return_pct=12.0,
    )
    base.update(overrides)
    return AdvancedRow(**base)  # type: ignore[arg-type]


def test_passes_bull_happy_path() -> None:
    assert passes_bull(_bull_row(), rec_gate_applied=True) is True


def test_passes_bull_rejects_volume_above_band() -> None:
    assert passes_bull(_bull_row(today_x_vol=6.0), True) is False


def test_passes_bull_rejects_volume_below_band() -> None:
    assert passes_bull(_bull_row(today_x_vol=1.5), True) is False


def test_passes_bull_rejects_delivery_below_avg() -> None:
    assert (
        passes_bull(_bull_row(current_dpc=40.0, avg_20d_dpc=45.0), True)
        is False
    )


def test_passes_bull_rejects_at_52w_high() -> None:
    assert (
        passes_bull(_bull_row(today_ltp=149.0, week52_high=150.0), True)
        is False
    )


def test_passes_bull_rejects_rsi_overbought() -> None:
    assert passes_bull(_bull_row(rsi=75.0), True) is False


def test_passes_bull_rejects_low_pscore() -> None:
    assert passes_bull(_bull_row(pscore=3), True) is False


def test_passes_bull_rejects_high_pledged() -> None:
    assert passes_bull(_bull_row(pledged_pct=15.0), True) is False


def test_passes_bull_accepts_fresh_golden_cross_without_stack() -> None:
    """If SMA stack fails but golden_cross is fresh, gate passes."""
    row = _bull_row(
        today_ltp=105.0, sma_50=110.0, sma_200=108.0,
        golden_cross_days_ago=5,
    )
    assert passes_bull(row, True) is True


def test_passes_bull_rejects_non_bullish_category() -> None:
    """`risk_alert`, `rebalance`, `defensive` etc. are NOT in
    BULLISH_CATEGORIES."""
    assert (
        passes_bull(_bull_row(rec_category="risk_alert"), True)
        is False
    )
    assert (
        passes_bull(_bull_row(rec_category="rebalance"), True)
        is False
    )
    assert (
        passes_bull(_bull_row(rec_category="defensive"), True)
        is False
    )


def test_passes_bull_accepts_each_bullish_category() -> None:
    """All four pinned bullish categories pass the gate."""
    for cat in ("offensive", "value", "growth", "hold_accumulate"):
        assert passes_bull(_bull_row(rec_category=cat), True) is True


def test_passes_bull_severity_does_not_gate() -> None:
    """Phase A does NOT gate on severity; all severities pass when
    the category is bullish."""
    for sev in ("high", "medium", "low"):
        assert (
            passes_bull(_bull_row(rec_severity=sev), True) is True
        )


def test_passes_bull_skips_rec_gate_when_degraded() -> None:
    """When user has no rec run, rec-category gate is bypassed."""
    row = _bull_row(
        rec_category=None, rec_severity=None,
        rec_expected_return_pct=None,
    )
    assert passes_bull(row, rec_gate_applied=False) is True


def test_passes_bull_handles_nan_inputs() -> None:
    """NaN in any hard-gate column rejects the row (no crash)."""
    row = _bull_row(today_x_vol=float("nan"))
    assert passes_bull(row, True) is False


def test_rank_bull_uses_rec_score_when_present() -> None:
    row = _bull_row(
        rec_expected_return_pct=10.0, x_dv_20d=2.0, today_x_vol=3.0,
    )
    assert rank_bull(row, rec_gate_applied=True) == 10.0 * 2.0 * 3.0


def test_rank_bull_degrades_when_no_rec() -> None:
    row = _bull_row(
        rec_expected_return_pct=None, x_dv_20d=2.0, today_x_vol=3.0,
    )
    assert rank_bull(row, rec_gate_applied=False) == 1.0 * 2.0 * 3.0


def test_rank_bull_clamps_negative_rec_return() -> None:
    row = _bull_row(
        rec_expected_return_pct=-5.0, x_dv_20d=2.0, today_x_vol=3.0,
    )
    assert rank_bull(row, rec_gate_applied=True) == 0.0
```

*Note: if `AdvancedRow` does not accept some of these kwargs (e.g., `week52_high`, `pledged_pct`), adapt the fixture in this task to use whatever the real field names are. Verify with `grep -n "class AdvancedRow" backend/advanced_analytics_models.py` and adjust before running tests.*

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k bull
```

Expected: ImportError on `passes_bull, rank_bull`.

- [ ] **Step 3: Implement `passes_bull` and `rank_bull`**

Append to `backend/advanced_analytics_swing.py`:

```python
import math

from advanced_analytics_models import AdvancedRow


def _safe_float(v: float | int | None) -> float | None:
    """Return v as float unless NaN/None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def passes_bull(
    row: AdvancedRow, rec_gate_applied: bool,
) -> bool:
    """Return True iff the row passes ALL bull-regime gates.

    ``rec_gate_applied`` is False when the user has no rec run this
    IST month — in that case the rec-category gate is bypassed
    (graceful degrade; transparency chip surfaced by the route).
    """
    today_ltp = _safe_float(row.today_ltp)
    sma_50 = _safe_float(row.sma_50)
    sma_200 = _safe_float(row.sma_200)
    gxa = row.golden_cross_days_ago
    today_x_vol = _safe_float(row.today_x_vol)
    current_dpc = _safe_float(row.current_dpc)
    avg_20d_dpc = _safe_float(row.avg_20d_dpc)
    x_dv_20d = _safe_float(row.x_dv_20d)
    rsi = _safe_float(row.rsi)
    pscore = row.pscore
    pledged = _safe_float(row.pledged_pct)
    w52_high = _safe_float(row.week52_high)

    # Trend stack OR fresh golden cross.
    stack_ok = (
        today_ltp is not None
        and sma_50 is not None
        and sma_200 is not None
        and today_ltp > sma_50 > sma_200
    )
    fresh_cross = (
        gxa is not None and 0 <= gxa <= BULL_GOLDEN_CROSS_FRESH_DAYS
    )
    if not (stack_ok or fresh_cross):
        return False

    # Volume sweet spot.
    if today_x_vol is None or not (
        BULL_VOL_MIN <= today_x_vol <= BULL_VOL_MAX
    ):
        return False

    # Delivery confirmation.
    if (
        current_dpc is None
        or avg_20d_dpc is None
        or current_dpc <= avg_20d_dpc
    ):
        return False

    # Accumulation.
    if x_dv_20d is None or x_dv_20d <= 1.0:
        return False

    # Not exhausted.
    if rsi is None or rsi >= BULL_RSI_MAX:
        return False

    # Quality.
    if pscore is None or pscore < BULL_PSCORE_MIN:
        return False
    if pledged is None or pledged >= BULL_PLEDGED_MAX:
        return False

    # Room to run.
    if (
        today_ltp is None
        or w52_high is None
        or w52_high == 0
        or today_ltp / w52_high >= BULL_RANGE_MAX
    ):
        return False

    # Rec engine — skip when degraded. Category only; severity is
    # surfaced on the row but does not gate in Phase A.
    if rec_gate_applied:
        if row.rec_category not in BULLISH_CATEGORIES:
            return False

    return True


def rank_bull(row: AdvancedRow, rec_gate_applied: bool) -> float:
    """Bull rank score; sort DESC. Degrades to vol*delivery when
    rec-engine is unavailable for the user this month.
    """
    rec_ret = (
        _safe_float(row.rec_expected_return_pct) if rec_gate_applied
        else None
    )
    rec_mult = max(rec_ret or 1.0, 0.0) if rec_gate_applied else 1.0
    x_dv = _safe_float(row.x_dv_20d) or 0.0
    x_vol = _safe_float(row.today_x_vol) or 0.0
    return rec_mult * x_dv * x_vol
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k bull
```

Expected: all bull tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_swing.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): passes_bull + rank_bull"
```

---

## Task 8: Sideways-regime filter + rank

**Files:**
- Modify: `backend/advanced_analytics_swing.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from advanced_analytics_swing import passes_sideways, rank_sideways


def _sideways_row(market: str = "india", **overrides: Any) -> AdvancedRow:
    base = dict(
        ticker="ITC.NS",
        today_ltp=100.0,
        sma_50=100.5,
        sma_200=100.0,  # |0.5|/100 = 0.005 (well below 0.05)
        rsi=50.0,
        today_x_vol=1.0,
        today_not=100_000_000.0,  # ₹10cr > floor
        pscore=5,
        rolling_low_20d_prev=95.0,
        rolling_high_20d_prev=105.0,
    )
    base.update(overrides)
    return AdvancedRow(**base)  # type: ignore[arg-type]


def test_passes_sideways_happy_path() -> None:
    assert passes_sideways(_sideways_row(), market="india") is True


def test_passes_sideways_rejects_diverged_mas() -> None:
    # |sma_50 - sma_200| / sma_200 = 0.10 (above 0.05)
    row = _sideways_row(sma_50=110.0, sma_200=100.0)
    assert passes_sideways(row, "india") is False


def test_passes_sideways_rejects_price_far_from_sma50() -> None:
    # |105 - 100| / 100 = 0.05 (above 0.03)
    row = _sideways_row(today_ltp=105.0, sma_50=100.0)
    assert passes_sideways(row, "india") is False


def test_passes_sideways_rejects_rsi_outside_band() -> None:
    assert passes_sideways(_sideways_row(rsi=35.0), "india") is False
    assert passes_sideways(_sideways_row(rsi=65.0), "india") is False


def test_passes_sideways_rejects_volume_surge() -> None:
    assert (
        passes_sideways(_sideways_row(today_x_vol=1.5), "india")
        is False
    )


def test_passes_sideways_rejects_volume_drought() -> None:
    assert (
        passes_sideways(_sideways_row(today_x_vol=0.5), "india")
        is False
    )


def test_passes_sideways_applies_inr_floor() -> None:
    row = _sideways_row(today_not=30_000_000.0)  # ₹3cr < ₹5cr
    assert passes_sideways(row, "india") is False


def test_passes_sideways_applies_usd_floor_for_us_market() -> None:
    # Notional in USD for US ticker.
    row = _sideways_row(today_not=500_000.0)  # $500k < $600k
    assert passes_sideways(row, "us") is False


def test_passes_sideways_rejects_low_pscore() -> None:
    assert passes_sideways(_sideways_row(pscore=3), "india") is False


def test_rank_sideways_lower_at_band_edge() -> None:
    """A row near the band edge scores LOWER (closer to 0) → ranks
    higher when sorted ASC."""
    near_low = _sideways_row(
        today_ltp=96.0,
        rolling_low_20d_prev=95.0,
        rolling_high_20d_prev=105.0,
    )
    mid_band = _sideways_row(
        today_ltp=100.0,
        rolling_low_20d_prev=95.0,
        rolling_high_20d_prev=105.0,
    )
    assert rank_sideways(near_low) < rank_sideways(mid_band)


def test_rank_sideways_nan_returns_inf() -> None:
    """Missing band data sorts last (inf)."""
    row = _sideways_row(
        rolling_low_20d_prev=None, rolling_high_20d_prev=None,
    )
    assert rank_sideways(row) == float("inf")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k sideways
```

Expected: ImportError on `passes_sideways, rank_sideways`.

- [ ] **Step 3: Implement**

Append to `backend/advanced_analytics_swing.py`:

```python
def passes_sideways(row: AdvancedRow, market: str) -> bool:
    today_ltp = _safe_float(row.today_ltp)
    sma_50 = _safe_float(row.sma_50)
    sma_200 = _safe_float(row.sma_200)
    rsi = _safe_float(row.rsi)
    today_x_vol = _safe_float(row.today_x_vol)
    today_not = _safe_float(row.today_not)
    pscore = row.pscore

    # MA convergence.
    if (
        sma_50 is None or sma_200 is None or sma_200 == 0
        or abs(sma_50 - sma_200) / abs(sma_200) >= SIDEWAYS_MA_CONV_MAX
    ):
        return False

    # Price near SMA-50.
    if (
        today_ltp is None or sma_50 is None or sma_50 == 0
        or abs(today_ltp - sma_50) / abs(sma_50)
        >= SIDEWAYS_PRICE_NEAR_SMA50
    ):
        return False

    # RSI band.
    if (
        rsi is None
        or not (SIDEWAYS_RSI_MIN <= rsi <= SIDEWAYS_RSI_MAX)
    ):
        return False

    # Volume band.
    if (
        today_x_vol is None
        or not (
            SIDEWAYS_VOL_MIN <= today_x_vol <= SIDEWAYS_VOL_MAX
        )
    ):
        return False

    # Liquidity floor (native currency).
    floor = (
        SIDEWAYS_NOT_FLOOR_USD if market == "us"
        else SIDEWAYS_NOT_FLOOR_INR
    )
    if today_not is None or today_not <= floor:
        return False

    # Quality.
    if pscore is None or pscore < SIDEWAYS_PSCORE_MIN:
        return False

    return True


def rank_sideways(row: AdvancedRow) -> float:
    """Distance-to-band-edge fraction. Sort ASC (smaller = nearer
    edge = higher priority). Returns inf when band is missing.
    """
    today_ltp = _safe_float(row.today_ltp)
    low = _safe_float(row.rolling_low_20d_prev)
    high = _safe_float(row.rolling_high_20d_prev)
    if today_ltp is None or low is None or high is None or today_ltp == 0:
        return float("inf")
    return min(today_ltp - low, high - today_ltp) / today_ltp
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k sideways
```

Expected: all sideways tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_swing.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): passes_sideways + rank_sideways"
```

---

## Task 9: Bearish-regime filter + rank

**Files:**
- Modify: `backend/advanced_analytics_swing.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from advanced_analytics_swing import passes_bearish, rank_bearish


def _bearish_row(market: str = "india", **overrides: Any) -> AdvancedRow:
    base = dict(
        ticker="YESBANK.NS",
        today_ltp=20.0,
        today_low=19.5,
        sma_50=22.0,
        sma_200=25.0,  # 50 < 200 → death-cross territory
        death_cross_days_ago=10,
        rsi=45.0,
        rsi_3d_ago=55.0,
        rsi_max_10d=65.0,
        rolling_low_20d_prev=20.0,  # today_low=19.5 < 20 → break ok
        week52_low=18.0,  # 20 / 18 = 1.11 > 1.05
        today_not=80_000_000.0,
    )
    base.update(overrides)
    return AdvancedRow(**base)  # type: ignore[arg-type]


def test_passes_bearish_happy_path() -> None:
    assert passes_bearish(_bearish_row(), "india") is True


def test_passes_bearish_rejects_stale_death_cross() -> None:
    row = _bearish_row(death_cross_days_ago=100)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_no_death_cross() -> None:
    row = _bearish_row(
        sma_50=25.0, sma_200=22.0, death_cross_days_ago=None,
    )
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_weak_rsi_history() -> None:
    """RSI never reached 60 in last 10d → not a rollover, just weak."""
    row = _bearish_row(rsi_max_10d=55.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_rsi_not_rolled_over() -> None:
    """Today's RSI still >= 50."""
    row = _bearish_row(rsi=55.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_rsi_recovering() -> None:
    """Today's RSI > 3-days-ago → recovering, not declining."""
    row = _bearish_row(rsi=48.0, rsi_3d_ago=40.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_no_lower_low() -> None:
    """today_low ≥ 20-day prev low → no decisive break."""
    row = _bearish_row(today_low=21.0, rolling_low_20d_prev=20.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_at_52w_floor() -> None:
    row = _bearish_row(today_ltp=18.5, week52_low=18.0)  # 1.027 < 1.05
    assert passes_bearish(row, "india") is False


def test_passes_bearish_applies_liquidity_floor() -> None:
    row = _bearish_row(today_not=10_000_000.0)
    assert passes_bearish(row, "india") is False


def test_rank_bearish_higher_for_fresher_cross() -> None:
    fresh = _bearish_row(death_cross_days_ago=2)
    stale = _bearish_row(death_cross_days_ago=50)
    assert rank_bearish(fresh) > rank_bearish(stale)


def test_rank_bearish_nan_returns_zero() -> None:
    row = _bearish_row(death_cross_days_ago=None)
    assert rank_bearish(row) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k bearish
```

Expected: ImportError.

- [ ] **Step 3: Implement**

Append:

```python
def passes_bearish(row: AdvancedRow, market: str) -> bool:
    sma_50 = _safe_float(row.sma_50)
    sma_200 = _safe_float(row.sma_200)
    dxa = row.death_cross_days_ago
    rsi = _safe_float(row.rsi)
    rsi_3d = _safe_float(row.rsi_3d_ago)
    rsi_max10 = _safe_float(row.rsi_max_10d)
    today_low = _safe_float(row.today_low)
    rb_low = _safe_float(row.rolling_low_20d_prev)
    today_ltp = _safe_float(row.today_ltp)
    w52_low = _safe_float(row.week52_low)
    today_not = _safe_float(row.today_not)

    # Death-cross active + fresh.
    if sma_50 is None or sma_200 is None or sma_50 >= sma_200:
        return False
    if dxa is None or dxa > BEARISH_DEATH_CROSS_FRESH_DAYS:
        return False

    # RSI rollover.
    if rsi_max10 is None or rsi_max10 < BEARISH_RSI_MAX_RECENT:
        return False
    if rsi is None or rsi > BEARISH_RSI_TODAY_MAX:
        return False
    if rsi_3d is None or rsi >= rsi_3d:
        return False

    # Lower-low break.
    if today_low is None or rb_low is None or today_low >= rb_low:
        return False

    # Room to fall.
    if (
        today_ltp is None or w52_low is None or w52_low == 0
        or today_ltp / w52_low <= BEARISH_FLOOR_RATIO
    ):
        return False

    # Liquidity.
    floor = (
        BEARISH_NOT_FLOOR_USD if market == "us"
        else BEARISH_NOT_FLOOR_INR
    )
    if today_not is None or today_not <= floor:
        return False

    return True


def rank_bearish(row: AdvancedRow) -> float:
    dxa = row.death_cross_days_ago
    rsi = _safe_float(row.rsi)
    today_low = _safe_float(row.today_low)
    rb_low = _safe_float(row.rolling_low_20d_prev)
    if dxa is None or rsi is None or today_low is None or rb_low is None:
        return 0.0
    if rb_low == 0:
        return 0.0
    fresh = 1.0 / (dxa + 1)
    severity = max(0.0, 60.0 - rsi)
    decisiveness = (rb_low - today_low) / rb_low
    return fresh * severity * decisiveness
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k bearish
```

Expected: all bearish tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_swing.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): passes_bearish + rank_bearish"
```

---

## Task 10: Batched rec-engine lookup

**Files:**
- Modify: `backend/advanced_analytics_routes.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
import asyncio
from unittest.mock import AsyncMock, patch


def test_load_latest_recommendations_returns_dict() -> None:
    """Returns {ticker: (category, severity, expected_return_pct)} +
    rec_run_id metadata for the most recent run of the user."""
    import advanced_analytics_routes as aar

    fake_rows = [
        {
            "ticker": "TCS.NS",
            "category": "offensive",
            "severity": "high",
            "expected_return_pct": 12.0,
        },
        {
            "ticker": "ITC.NS",
            "category": "rebalance",
            "severity": "low",
            "expected_return_pct": 0.0,
        },
    ]

    async def fake_pg(_cb):
        # _cb is a callable; we ignore it and return fixture data
        # mimicking what the SQL query would yield.
        return {
            "run_id": "uuid-abc",
            "run_date": "2026-05-01",
            "rows": fake_rows,
        }

    with patch.object(aar, "_run_pg", new=fake_pg):
        result = asyncio.run(
            aar._load_latest_recommendations(
                user_id="user-1", tickers=["TCS.NS", "ITC.NS"],
            )
        )
    assert result["run_id"] == "uuid-abc"
    assert result["run_date"] == "2026-05-01"
    assert result["recs"]["TCS.NS"] == ("offensive", "high", 12.0)
    assert result["recs"]["ITC.NS"] == ("rebalance", "low", 0.0)


def test_load_latest_recommendations_no_run() -> None:
    """No run for user this month → returns empty dict with
    run_id=None."""
    import advanced_analytics_routes as aar

    async def fake_pg(_cb):
        return {"run_id": None, "run_date": None, "rows": []}

    with patch.object(aar, "_run_pg", new=fake_pg):
        result = asyncio.run(
            aar._load_latest_recommendations(
                user_id="user-2", tickers=["TCS.NS"],
            )
        )
    assert result["run_id"] is None
    assert result["recs"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k load_latest_recommendations
```

Expected: ImportError.

- [ ] **Step 3: Implement the helper**

In `backend/advanced_analytics_routes.py`, add near the other PG helpers (after `_load_indicators_latest`):

```python
from sqlalchemy import text


async def _load_latest_recommendations(
    user_id: str, tickers: list[str],
) -> dict[str, Any]:
    """Batched fetch of the most recent active recommendations for a
    user, restricted to the supplied ticker list.

    Returns {
        "run_id": str | None,
        "run_date": str | None,
        "recs": {
            ticker: (category, severity, expected_return_pct),
            ...
        },
    }
    """
    if not tickers:
        return {"run_id": None, "run_date": None, "recs": {}}

    def _call() -> dict[str, Any]:
        with _pg_session() as session:
            # Find user's most recent run (any scope) within
            # current IST month — same semantics as the rec engine.
            row = session.execute(
                text(
                    "SELECT run_id, run_date "
                    "FROM stocks.recommendation_runs "
                    "WHERE user_id = :uid "
                    "ORDER BY run_date DESC LIMIT 1"
                ),
                {"uid": user_id},
            ).fetchone()
            if row is None:
                return {
                    "run_id": None, "run_date": None, "rows": [],
                }
            run_id, run_date = row
            rec_rows = session.execute(
                text(
                    "SELECT ticker, category, severity, "
                    "expected_return_pct "
                    "FROM stocks.recommendations "
                    "WHERE run_id = :rid "
                    "AND status = 'active' "
                    "AND ticker = ANY(:tk)"
                ),
                {"rid": str(run_id), "tk": tickers},
            ).fetchall()
            return {
                "run_id": str(run_id),
                "run_date": run_date.isoformat() if run_date else None,
                "rows": [dict(r._mapping) for r in rec_rows],
            }

    pg = await _run_pg(_call)
    recs: dict[str, tuple[str | None, str | None, float | None]] = {}
    for r in pg["rows"]:
        recs[r["ticker"]] = (
            r.get("category"),
            r.get("severity"),
            r.get("expected_return_pct"),
        )
    return {
        "run_id": pg["run_id"],
        "run_date": pg["run_date"],
        "recs": recs,
    }
```

*Verify `_run_pg` and `_pg_session` symbol names match what's already imported / defined in `advanced_analytics_routes.py` — grep first; adapt the import line if these helpers live in `pg_async.py` or similar.*

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k load_latest_recommendations
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): _load_latest_recommendations batched PG join"
```

---

## Task 11: Wire rec-join data onto rows

**Files:**
- Modify: `backend/advanced_analytics_routes.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_apply_rec_data_populates_row_fields() -> None:
    """Stamp rec_* fields onto rows from the rec dict."""
    import advanced_analytics_routes as aar

    rows = [
        AdvancedRow(ticker="TCS.NS"),
        AdvancedRow(ticker="ITC.NS"),
    ]
    recs = {
        "TCS.NS": ("offensive", "high", 12.0),
        # ITC.NS missing
    }
    aar._apply_rec_data(rows, recs)
    assert rows[0].rec_category == "offensive"
    assert rows[0].rec_severity == "high"
    assert rows[0].rec_expected_return_pct == 12.0
    assert rows[1].rec_category is None
    assert rows[1].rec_severity is None
    assert rows[1].rec_expected_return_pct is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k apply_rec_data
```

- [ ] **Step 3: Implement**

Append to `backend/advanced_analytics_routes.py`:

```python
def _apply_rec_data(
    rows: list[AdvancedRow],
    recs: dict[str, tuple[str | None, str | None, float | None]],
) -> None:
    """Stamp rec_* fields onto each row in-place from the rec map."""
    for r in rows:
        rec = recs.get(r.ticker)
        if rec is None:
            continue
        cat, sev, ret = rec
        r.rec_category = cat
        r.rec_severity = sev
        r.rec_expected_return_pct = ret
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k apply_rec_data
```

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): _apply_rec_data stamps rec fields onto rows"
```

---

## Task 12: Response models

**Files:**
- Modify: `backend/advanced_analytics_models.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from advanced_analytics_models import (
    SwingMethodology,
    SwingMethodologyGate,
    SwingMethodologyRank,
    SwingSetupsResponse,
)


def test_swing_methodology_constructs() -> None:
    m = SwingMethodology(
        regime="bull",
        summary="Trend-up + demand + quality.",
        gates=[
            SwingMethodologyGate(
                label="Trend stack", rule="x>y", why="trend",
            ),
        ],
        rank=SwingMethodologyRank(
            formula="a*b", direction="DESC", cap=25, degraded=None,
        ),
    )
    assert m.regime == "bull"
    assert m.gates[0].label == "Trend stack"


def test_swing_setups_response_constructs() -> None:
    resp = SwingSetupsResponse(
        rows=[],
        total=0,
        regime="bull",
        as_of="2026-05-12",
        rec_gate_applied=False,
        rec_run_id=None,
        rec_run_date=None,
        notes=[
            "Recommendation gate not applied — no rec run this "
            "month",
        ],
        methodology=SwingMethodology(
            regime="bull", summary="x", gates=[],
            rank=SwingMethodologyRank(
                formula="a", direction="DESC", cap=25, degraded=None,
            ),
        ),
    )
    assert resp.regime == "bull"
    assert resp.rec_gate_applied is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k swing_methodology -k swing_setups_response
```

- [ ] **Step 3: Add models**

Append to `backend/advanced_analytics_models.py`:

```python
class SwingMethodologyGate(BaseModel):
    label: str
    rule: str
    why: str


class SwingMethodologyRank(BaseModel):
    formula: str
    direction: Literal["ASC", "DESC"]
    cap: int
    degraded: str | None = None


class SwingMethodology(BaseModel):
    regime: Literal["bull", "sideways", "bearish"]
    summary: str
    gates: list[SwingMethodologyGate]
    rank: SwingMethodologyRank


class SwingSetupsResponse(BaseModel):
    rows: list[AdvancedRow]
    total: int
    regime: Literal["bull", "sideways", "bearish"]
    as_of: str
    rec_gate_applied: bool
    rec_run_id: str | None = None
    rec_run_date: str | None = None
    notes: list[str] = []
    methodology: SwingMethodology
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k swing
```

Expected: all swing model tests pass.

- [ ] **Step 5: Restart backend** (new response_model fields — §6.2)

```bash
docker compose restart backend && sleep 5
```

- [ ] **Step 6: Commit**

```bash
git add backend/advanced_analytics_models.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): SwingMethodology + SwingSetupsResponse models"
```

---

## Task 13: `_compute_swing_setup` orchestrator

**Files:**
- Modify: `backend/advanced_analytics_routes.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing integration test**

Append:

```python
def test_compute_swing_setup_bull_filters_and_ranks(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """End-to-end: passing rows survive filter, ranked DESC, capped
    at SWING_CAP, methodology + degraded-flag set correctly."""
    import advanced_analytics_routes as aar
    from advanced_analytics_swing import SWING_CAP

    # Build SWING_CAP + 3 rows that all pass the bull filter, with
    # rank scores strictly increasing so we can verify ordering.
    rows: list[AdvancedRow] = []
    for i in range(SWING_CAP + 3):
        rows.append(_bull_row(
            ticker=f"T{i}.NS",
            today_x_vol=2.0 + i * 0.01,
            x_dv_20d=1.5,
            rec_expected_return_pct=10.0 + i,
        ))

    async def fake_rows(*_a, **_kw):
        return rows

    async def fake_recs(*_a, **_kw):
        return {
            "run_id": "uuid-x",
            "run_date": "2026-05-01",
            "recs": {r.ticker: ("Buy", "high", float(10.0 + i))
                     for i, r in enumerate(rows)},
        }

    monkeypatch.setattr(aar, "_load_rows_for_universe", fake_rows)
    monkeypatch.setattr(aar, "_load_latest_recommendations", fake_recs)

    resp = asyncio.run(aar._compute_swing_setup(
        regime="bull",
        user_id="user-1",
        tickers=[r.ticker for r in rows],
        market="india",
        as_of=date(2026, 5, 12),
        page=1, page_size=25,
        sort_key=None, sort_dir=None,
    ))
    assert resp.total == SWING_CAP + 3
    assert len(resp.rows) == 25  # page_size cap
    assert resp.rec_gate_applied is True
    # First row should be the highest-ranked (last index).
    assert resp.rows[0].ticker == f"T{SWING_CAP + 2}.NS"
    assert resp.methodology.regime == "bull"


def test_compute_swing_setup_degrades_when_no_rec_run(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """No rec run for user → rec_gate_applied False, bypass rec gate
    but still rank by vol*delivery."""
    import advanced_analytics_routes as aar

    rows = [
        _bull_row(
            ticker="X.NS", rec_category=None, rec_severity=None,
            rec_expected_return_pct=None,
        ),
    ]

    async def fake_rows(*_a, **_kw):
        return rows

    async def fake_recs(*_a, **_kw):
        return {"run_id": None, "run_date": None, "recs": {}}

    monkeypatch.setattr(aar, "_load_rows_for_universe", fake_rows)
    monkeypatch.setattr(aar, "_load_latest_recommendations", fake_recs)

    resp = asyncio.run(aar._compute_swing_setup(
        regime="bull",
        user_id="user-2",
        tickers=["X.NS"],
        market="india",
        as_of=date(2026, 5, 12),
        page=1, page_size=25,
        sort_key=None, sort_dir=None,
    ))
    assert resp.rec_gate_applied is False
    assert resp.total == 1
    assert any("not applied" in n.lower() for n in resp.notes)
```

*The fake helper name `_load_rows_for_universe` is a placeholder for whatever existing AA function produces the per-ticker `AdvancedRow` list. Grep `_compute_report` in `advanced_analytics_routes.py` and use the actual internal that builds rows for a list of tickers; rename in the test if needed.*

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k compute_swing
```

Expected: ImportError on `_compute_swing_setup`.

- [ ] **Step 3: Implement the orchestrator**

In `backend/advanced_analytics_routes.py`, add near `_compute_report`:

```python
from advanced_analytics_swing import (
    REGIMES,
    SWING_CAP,
    build_methodology,
    passes_bearish,
    passes_bull,
    passes_sideways,
    rank_bearish,
    rank_bull,
    rank_sideways,
)


_REGIME_FILTERS = {
    "bull": passes_bull,
    "sideways": passes_sideways,
    "bearish": passes_bearish,
}

_REGIME_RANKERS = {
    "bull": rank_bull,
    "sideways": rank_sideways,
    "bearish": rank_bearish,
}

_REGIME_SORT_DIR = {
    "bull": "desc", "sideways": "asc", "bearish": "desc",
}


async def _compute_swing_setup(
    *,
    regime: str,
    user_id: str,
    tickers: list[str],
    market: str,
    as_of: date,
    page: int,
    page_size: int,
    sort_key: str | None,
    sort_dir: str | None,
) -> SwingSetupsResponse:
    """Pull AdvancedRows, apply rec join, filter + rank by regime."""
    if regime not in REGIMES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown regime: {regime}",
        )

    # 1. Load rows for the user's universe. Reuse the existing
    #    row-loader used by the seven AA reports.
    rows = await _load_rows_for_universe(
        tickers=tickers, market=market, as_of=as_of,
    )

    # 2. Rec join.
    rec_payload = await _load_latest_recommendations(
        user_id=user_id, tickers=tickers,
    )
    rec_gate_applied = rec_payload["run_id"] is not None
    _apply_rec_data(rows, rec_payload["recs"])

    # 3. Filter.
    flt = _REGIME_FILTERS[regime]
    if regime == "bull":
        rows = [r for r in rows if flt(r, rec_gate_applied)]
    else:
        rows = [r for r in rows if flt(r, market)]

    # 4. Rank (default direction per regime; override only if
    #    sort_key provided).
    rank_fn = _REGIME_RANKERS[regime]
    default_dir = _REGIME_SORT_DIR[regime]
    if sort_key is None:
        # Decorate-sort-undecorate by rank fn.
        if regime == "bull":
            keyed = [
                (rank_fn(r, rec_gate_applied), r) for r in rows
            ]
        else:
            keyed = [(rank_fn(r), r) for r in rows]
        keyed.sort(key=lambda kr: kr[0], reverse=default_dir == "desc")
        rows = [r for _, r in keyed]
    else:
        # Defer to the standard AA sort utility.
        rows, _ = _apply_sort_paginate(
            rows, "swing-setups",  # type: ignore[arg-type]
            sort_key, sort_dir or default_dir,
            page=1, page_size=10_000,
        )

    # 5. Cap.
    rows = rows[:SWING_CAP]

    # 6. Notes.
    notes: list[str] = []
    if regime == "bull" and not rec_gate_applied:
        notes.append(
            "Recommendation gate not applied — no rec run this month"
        )

    # 7. Paginate.
    total = len(rows)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    page_rows = rows[start:end]

    return SwingSetupsResponse(
        rows=page_rows,
        total=total,
        regime=regime,  # type: ignore[arg-type]
        as_of=as_of.isoformat(),
        rec_gate_applied=rec_gate_applied,
        rec_run_id=rec_payload["run_id"],
        rec_run_date=rec_payload["run_date"],
        notes=notes,
        methodology=SwingMethodology.model_validate(
            build_methodology(regime),  # type: ignore[arg-type]
        ),
    )
```

*`_load_rows_for_universe` is a placeholder for the actual helper that exists in `advanced_analytics_routes.py` and builds `AdvancedRow` objects for a ticker list. Find it via `grep -n "build_row\|_compute_report" backend/advanced_analytics_routes.py` and either reuse directly or extract a helper if `_compute_report` is monolithic.*

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k compute_swing
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): _compute_swing_setup orchestrator"
```

---

## Task 14: `GET /swing-setups` endpoint

**Files:**
- Modify: `backend/advanced_analytics_routes.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing integration test**

Append:

```python
from fastapi.testclient import TestClient


def test_swing_setups_route_returns_200(
    backend_client: TestClient, auth_header_pro: dict[str, str],
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """Pro-user GET returns 200 + methodology + rows."""
    import advanced_analytics_routes as aar

    async def fake_compute(**_kw):
        return SwingSetupsResponse(
            rows=[],
            total=0,
            regime="bull",
            as_of="2026-05-12",
            rec_gate_applied=False,
            rec_run_id=None,
            rec_run_date=None,
            notes=["Recommendation gate not applied — no rec run "
                   "this month"],
            methodology=SwingMethodology.model_validate(
                build_methodology("bull"),
            ),
        )

    monkeypatch.setattr(aar, "_compute_swing_setup", fake_compute)

    resp = backend_client.get(
        "/v1/advanced-analytics/swing-setups?regime=bull",
        headers=auth_header_pro,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["regime"] == "bull"
    assert body["methodology"]["regime"] == "bull"
    assert body["rec_gate_applied"] is False


def test_swing_setups_route_rejects_unknown_regime(
    backend_client: TestClient, auth_header_pro: dict[str, str],
) -> None:
    resp = backend_client.get(
        "/v1/advanced-analytics/swing-setups?regime=mango",
        headers=auth_header_pro,
    )
    assert resp.status_code == 400


def test_swing_setups_route_requires_pro(
    backend_client: TestClient, auth_header_general: dict[str, str],
) -> None:
    resp = backend_client.get(
        "/v1/advanced-analytics/swing-setups?regime=bull",
        headers=auth_header_general,
    )
    assert resp.status_code == 403
```

*Use whatever existing pytest fixtures already serve `backend_client`, `auth_header_pro`, `auth_header_general` in the repo. Grep:*

```bash
grep -rn "backend_client\|auth_header_pro" backend/tests/ | head -10
```

*If existing fixtures use different names (e.g., `client_pro`, `pro_user_client`), adapt this test to use them.*

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k swing_setups_route
```

Expected: 404 on the route.

- [ ] **Step 3: Add the endpoint**

In `backend/advanced_analytics_routes.py`, find the existing AA router (around line 1271-1401 where the seven endpoints live) and add:

```python
@router.get(
    "/swing-setups",
    response_model=SwingSetupsResponse,
    summary="Swing-trade setups by regime",
)
async def swing_setups(
    regime: Literal["bull", "sideways", "bearish"] = Query(...),
    market: Literal["india", "us", "all"] = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort: str | None = None,
    response: Response = None,
    user: UserContext = Depends(pro_or_superuser),
) -> SwingSetupsResponse:
    """Ranked watchlist for a single regime — see methodology block
    in response for the exact gates applied."""
    cache = get_cache()
    cache_key = (
        f"cache:advanced_analytics:swing-setups:{regime}:"
        f"{user.user_id}:{market}:p{page}:ps{page_size}:s{sort or ''}"
    )
    hit = await cache.get(cache_key)
    if hit is not None:
        return SwingSetupsResponse.model_validate_json(hit)

    tickers = await _scoped_tickers(user, "discovery")
    sort_key: str | None
    sort_dir: str | None
    if sort and ":" in sort:
        sort_key, sort_dir = sort.split(":", 1)
    else:
        sort_key = sort
        sort_dir = None

    result = await _compute_swing_setup(
        regime=regime,
        user_id=user.user_id,
        tickers=tickers,
        market=market,
        as_of=date.today(),
        page=page,
        page_size=page_size,
        sort_key=sort_key,
        sort_dir=sort_dir,
    )
    await cache.set(
        cache_key, result.model_dump_json(), ttl=TTL_STABLE,
    )
    return result
```

- [ ] **Step 4: Restart backend** (new endpoint — §6.2)

```bash
docker compose restart backend && sleep 5
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k swing_setups_route
```

Expected: 3 passed.

- [ ] **Step 6: Manual smoke**

```bash
TOKEN=<pro user JWT>  # use scripts/get_test_token.sh if available
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8181/v1/advanced-analytics/swing-setups?regime=bull" \
  | python -m json.tool | head -50
```

Expected: 200 response with `methodology` block visible.

- [ ] **Step 7: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): GET /swing-setups endpoint"
```

---

## Task 15: `GET /swing-setups/methodology` standalone endpoint

**Files:**
- Modify: `backend/advanced_analytics_routes.py`
- Test: `backend/tests/test_advanced_analytics_swing.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_methodology_endpoint_returns_block(
    backend_client: TestClient, auth_header_pro: dict[str, str],
) -> None:
    resp = backend_client.get(
        "/v1/advanced-analytics/swing-setups/methodology"
        "?regime=sideways",
        headers=auth_header_pro,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["regime"] == "sideways"
    assert body["rank"]["direction"] == "ASC"
    assert len(body["gates"]) >= 6
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k methodology_endpoint
```

Expected: 404.

- [ ] **Step 3: Add the endpoint**

In `backend/advanced_analytics_routes.py`:

```python
@router.get(
    "/swing-setups/methodology",
    response_model=SwingMethodology,
    summary="Filter rules + ranking for a swing-setup regime",
)
async def swing_setups_methodology(
    regime: Literal["bull", "sideways", "bearish"] = Query(...),
    user: UserContext = Depends(pro_or_superuser),
) -> SwingMethodology:
    return SwingMethodology.model_validate(build_methodology(regime))
```

- [ ] **Step 4: Restart backend**

```bash
docker compose restart backend && sleep 5
```

- [ ] **Step 5: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v -k methodology_endpoint
```

- [ ] **Step 6: Commit**

```bash
git add backend/advanced_analytics_routes.py \
        backend/tests/test_advanced_analytics_swing.py
git commit -m "feat(aa-swing): GET /swing-setups/methodology endpoint"
```

---

## Task 16: Cache invalidation map

**Files:**
- Modify: `backend/advanced_analytics_routes.py` (or wherever `_CACHE_INVALIDATION_MAP` lives — grep first)

- [ ] **Step 1: Locate the invalidation map**

```bash
grep -rn "_CACHE_INVALIDATION_MAP" backend/ | head -5
```

- [ ] **Step 2: Add swing-setup invalidation patterns**

Add these globs to the entries for `stocks.ohlcv` and `stocks.nse_delivery`:

```python
"cache:advanced_analytics:swing-setups:*",
```

And for `stocks.recommendation_runs` / `stocks.recommendations` (the bull regime degrades when a new rec run lands):

```python
"cache:advanced_analytics:swing-setups:bull:*",
```

- [ ] **Step 3: Restart backend** (cache-touching code change → also FLUSHALL per §4.5 #34)

```bash
docker compose restart backend && sleep 5
docker compose exec redis redis-cli FLUSHALL
```

- [ ] **Step 4: Manual smoke — invalidation works**

```bash
# Warm cache, then trigger an OHLCV write or call a manual
# invalidator, then re-fetch and verify a fresh compute happened
# (look at backend logs for the cache-miss code path).
```

- [ ] **Step 5: Commit**

```bash
git add backend/advanced_analytics_routes.py
git commit -m "feat(aa-swing): cache invalidation for swing-setups keys"
```

---

## Task 17: Frontend types

**Files:**
- Create: `frontend/types/swingSetups.ts`

- [ ] **Step 1: Write the file**

```typescript
export type SwingRegime = "bull" | "sideways" | "bearish";

export interface SwingMethodologyGate {
  label: string;
  rule: string;
  why: string;
}

export interface SwingMethodologyRank {
  formula: string;
  direction: "ASC" | "DESC";
  cap: number;
  degraded: string | null;
}

export interface SwingMethodology {
  regime: SwingRegime;
  summary: string;
  gates: SwingMethodologyGate[];
  rank: SwingMethodologyRank;
}

// Reuses the existing AdvancedRow type from the AA shared module.
import type { AdvancedRow } from "@/types/advancedAnalytics";

export interface SwingSetupsResponse {
  rows: AdvancedRow[];
  total: number;
  regime: SwingRegime;
  as_of: string;
  rec_gate_applied: boolean;
  rec_run_id: string | null;
  rec_run_date: string | null;
  notes: string[];
  methodology: SwingMethodology;
}
```

*If the existing `AdvancedRow` type lives at a different path (verify with `grep -rn "interface AdvancedRow\|type AdvancedRow" frontend/`), adjust the import.*

- [ ] **Step 2: Confirm types compile**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json
```

Expected: no errors related to the new file.

- [ ] **Step 3: Commit**

```bash
git add frontend/types/swingSetups.ts
git commit -m "feat(aa-swing): TypeScript types for swing-setups response"
```

---

## Task 18: SWR hook `useSwingSetups`

**Files:**
- Create: `frontend/hooks/useSwingSetups.ts`

- [ ] **Step 1: Write the hook**

```typescript
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import type {
  SwingMethodology,
  SwingRegime,
  SwingSetupsResponse,
} from "@/types/swingSetups";

interface UseSwingSetupsArgs {
  regime: SwingRegime;
  market: "india" | "us" | "all";
  page: number;
  pageSize: number;
  sort: string | null;
}

export function useSwingSetups({
  regime, market, page, pageSize, sort,
}: UseSwingSetupsArgs) {
  const params = new URLSearchParams({
    regime,
    market,
    page: String(page),
    page_size: String(pageSize),
  });
  if (sort) params.set("sort", sort);
  const key = `/advanced-analytics/swing-setups?${params.toString()}`;
  return useSWR<SwingSetupsResponse>(
    key,
    (url: string) => apiFetch(url).then(r => r.json()),
    {
      dedupingInterval: 120_000,  // 2 min — CLAUDE.md §5.3
      revalidateOnFocus: false,
    },
  );
}

export function useSwingMethodology(regime: SwingRegime) {
  const key = `/advanced-analytics/swing-setups/methodology?regime=${regime}`;
  return useSWR<SwingMethodology>(
    key,
    (url: string) => apiFetch(url).then(r => r.json()),
    { dedupingInterval: 600_000, revalidateOnFocus: false },
  );
}
```

- [ ] **Step 2: Confirm types compile**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json
```

- [ ] **Step 3: Commit**

```bash
git add frontend/hooks/useSwingSetups.ts
git commit -m "feat(aa-swing): useSwingSetups + useSwingMethodology SWR hooks"
```

---

## Task 19: `SwingRegimePills` component

**Files:**
- Create: `frontend/components/advanced-analytics/SwingRegimePills.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SwingRegime } from "@/types/swingSetups";

const REGIMES: { value: SwingRegime; label: string }[] = [
  { value: "bull", label: "Bull" },
  { value: "sideways", label: "Sideways" },
  { value: "bearish", label: "Bearish" },
];

interface Props {
  value: SwingRegime;
  onChange: (regime: SwingRegime) => void;
}

export function SwingRegimePills({ value, onChange }: Props) {
  return (
    <div
      role="tablist"
      aria-label="Swing regime"
      className="inline-flex rounded-md border bg-muted p-1"
    >
      {REGIMES.map((r) => (
        <Button
          key={r.value}
          variant={value === r.value ? "default" : "ghost"}
          size="sm"
          role="tab"
          aria-selected={value === r.value}
          data-testid={`swing-regime-pill-${r.value}`}
          className={cn("rounded-sm", value === r.value && "shadow")}
          onClick={() => onChange(r.value)}
        >
          {r.label}
        </Button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json
```

- [ ] **Step 3: Commit**

```bash
git add frontend/components/advanced-analytics/SwingRegimePills.tsx
git commit -m "feat(aa-swing): SwingRegimePills segmented control"
```

---

## Task 20: `SwingMethodologyPanel` component

**Files:**
- Create: `frontend/components/advanced-analytics/SwingMethodologyPanel.tsx`
- Test: `frontend/components/advanced-analytics/__tests__/SwingMethodologyPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SwingMethodologyPanel } from "../SwingMethodologyPanel";
import type { SwingMethodology } from "@/types/swingSetups";

const M: SwingMethodology = {
  regime: "bull",
  summary: "Trend-up + demand + quality.",
  gates: [
    {
      label: "Trend stack",
      rule: "today_ltp > sma_50 > sma_200",
      why: "Establishes an uptrend.",
    },
    {
      label: "Volume sweet spot",
      rule: "2 <= today_x_vol <= 5",
      why: "Below 2x lacks conviction.",
    },
  ],
  rank: {
    formula: "a * b",
    direction: "DESC",
    cap: 25,
    degraded: null,
  },
};

describe("SwingMethodologyPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders all gates from the backend block", () => {
    render(
      <SwingMethodologyPanel
        methodology={M}
        recGateApplied
        notes={[]}
      />,
    );
    expect(screen.getByText("Trend stack")).toBeInTheDocument();
    expect(screen.getByText(/today_ltp > sma_50/)).toBeInTheDocument();
    expect(screen.getByText("Volume sweet spot")).toBeInTheDocument();
  });

  it("collapses after first view per regime via localStorage", () => {
    const { rerender, unmount } = render(
      <SwingMethodologyPanel
        methodology={M}
        recGateApplied
        notes={[]}
      />,
    );
    expect(screen.getByText("Trend stack")).toBeVisible();

    // Simulate user collapsing it.
    fireEvent.click(screen.getByTestId("swing-methodology-toggle"));
    expect(window.localStorage.getItem(
      "aa.swing.bull.methodology_seen",
    )).toBe("1");

    unmount();
    rerender(
      <SwingMethodologyPanel
        methodology={M}
        recGateApplied
        notes={[]}
      />,
    );
    // Second render should be collapsed (seen flag set).
    expect(screen.queryByText("Trend stack")).not.toBeVisible();
  });

  it("strikes through rec-gate row when degraded", () => {
    const mWithRecGate: SwingMethodology = {
      ...M,
      gates: [
        ...M.gates,
        {
          label: "Rec-engine bullish",
          rule: "rec_category in {Buy}",
          why: "LLM confirms.",
        },
      ],
    };
    render(
      <SwingMethodologyPanel
        methodology={mWithRecGate}
        recGateApplied={false}
        notes={["Recommendation gate not applied — no rec run "
          + "this month"]}
      />,
    );
    const recGateLabel = screen.getByText("Rec-engine bullish");
    expect(recGateLabel.className).toContain("line-through");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run \
  components/advanced-analytics/__tests__/SwingMethodologyPanel.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the panel**

```tsx
"use client";

import { ChevronDown, ChevronUp, Info } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { SwingMethodology } from "@/types/swingSetups";

interface Props {
  methodology: SwingMethodology;
  recGateApplied: boolean;
  notes: string[];
}

const RECT_LABEL = "Rec-engine bullish";

export function SwingMethodologyPanel({
  methodology, recGateApplied, notes,
}: Props) {
  const storageKey =
    `aa.swing.${methodology.regime}.methodology_seen`;
  const [open, setOpen] = useState<boolean>(true);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const seen = window.localStorage.getItem(storageKey);
    if (seen === "1") setOpen(false);
  }, [storageKey]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(storageKey, next ? "0" : "1");
    }
  };

  return (
    <section
      data-testid="swing-methodology-panel"
      aria-label="Methodology"
      className="rounded-md border bg-muted/40 mb-4"
    >
      <header className="flex items-center justify-between px-4 py-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Info className="h-4 w-4" />
          How this {labelFor(methodology.regime)} list is built
        </div>
        <button
          data-testid="swing-methodology-toggle"
          onClick={toggle}
          aria-expanded={open}
          aria-label={open ? "Collapse methodology" : "Expand methodology"}
          className="inline-flex items-center gap-1 text-xs"
        >
          {open ? <>collapse <ChevronUp className="h-3 w-3" /></>
                : <>expand <ChevronDown className="h-3 w-3" /></>}
        </button>
      </header>
      <div hidden={!open} className="px-4 pb-4 space-y-3 text-sm">
        <p>{methodology.summary}</p>

        <div>
          <p className="font-medium mb-1">Gates (all must hold):</p>
          <ol className="space-y-2 list-decimal pl-5">
            {methodology.gates.map((g) => {
              const struck =
                !recGateApplied && g.label === RECT_LABEL;
              return (
                <li key={g.label}>
                  <span className={cn(
                    "font-medium",
                    struck && "line-through text-muted-foreground",
                  )}>{g.label}</span>
                  <code className="ml-2 font-mono text-xs">
                    {g.rule}
                  </code>
                  <div className="text-xs text-muted-foreground ml-1">
                    ↳ {g.why}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <div>
          <p className="font-medium">
            Ranking:{" "}
            <code className="font-mono text-xs">
              {methodology.rank.formula}
            </code>{" "}
            ({methodology.rank.direction}, top {methodology.rank.cap})
          </p>
          {methodology.rank.degraded && !recGateApplied && (
            <p className="text-xs text-muted-foreground">
              {methodology.rank.degraded}
            </p>
          )}
        </div>

        {notes.length > 0 && (
          <ul className="text-xs text-amber-600 list-disc pl-5">
            {notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        )}
      </div>
    </section>
  );
}

function labelFor(r: SwingMethodology["regime"]): string {
  return r === "bull" ? "Bull-swing"
       : r === "sideways" ? "Sideways-swing"
       : "Bearish-swing";
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run \
  components/advanced-analytics/__tests__/SwingMethodologyPanel.test.tsx
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/advanced-analytics/SwingMethodologyPanel.tsx \
        frontend/components/advanced-analytics/__tests__/SwingMethodologyPanel.test.tsx
git commit -m "feat(aa-swing): SwingMethodologyPanel — backend-sourced rules display"
```

---

## Task 21: `SwingSetupsTab` component

**Files:**
- Create: `frontend/components/advanced-analytics/SwingSetupsTab.tsx`
- Test: `frontend/components/advanced-analytics/__tests__/SwingSetupsTab.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import useSWR from "swr";

import { SwingSetupsTab } from "../SwingSetupsTab";

vi.mock("swr");

const fakeResponse = {
  rows: [],
  total: 0,
  regime: "bull" as const,
  as_of: "2026-05-12",
  rec_gate_applied: true,
  rec_run_id: "uuid",
  rec_run_date: "2026-05-01",
  notes: [],
  methodology: {
    regime: "bull" as const,
    summary: "x",
    gates: [{ label: "Trend stack", rule: "a>b", why: "trend" }],
    rank: { formula: "a", direction: "DESC" as const, cap: 25,
            degraded: null },
  },
};

describe("SwingSetupsTab", () => {
  it("renders three pills", () => {
    (useSWR as any).mockReturnValue({ data: fakeResponse });
    render(<SwingSetupsTab />);
    expect(screen.getByTestId("swing-regime-pill-bull")).toBeInTheDocument();
    expect(screen.getByTestId("swing-regime-pill-sideways"))
      .toBeInTheDocument();
    expect(screen.getByTestId("swing-regime-pill-bearish"))
      .toBeInTheDocument();
  });

  it("switching pill triggers new SWR fetch with regime param", () => {
    const mock = vi.fn().mockReturnValue({ data: fakeResponse });
    (useSWR as any).mockImplementation(mock);
    render(<SwingSetupsTab />);
    fireEvent.click(screen.getByTestId("swing-regime-pill-bearish"));
    const calls = mock.mock.calls.map((c) => c[0]);
    const callsAsString = calls.join(",");
    expect(callsAsString).toContain("regime=bearish");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run \
  components/advanced-analytics/__tests__/SwingSetupsTab.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the tab**

```tsx
"use client";

import { useState } from "react";

import { AdvancedAnalyticsTable } from "./AdvancedAnalyticsTable";
import { SwingMethodologyPanel } from "./SwingMethodologyPanel";
import { SwingRegimePills } from "./SwingRegimePills";
import { useSwingSetups } from "@/hooks/useSwingSetups";
import type { SwingRegime } from "@/types/swingSetups";

const DEFAULT_COLS: Record<SwingRegime, string[]> = {
  bull: [
    "ticker", "sector", "today_ltp", "today_x_vol", "current_dpc",
    "x_dv_20d", "rsi", "sma_50", "rec_category",
    "rec_expected_return_pct", "pscore",
  ],
  sideways: [
    "ticker", "sector", "today_ltp", "sma_50", "rsi", "today_x_vol",
    "rolling_low_20d_prev", "rolling_high_20d_prev", "today_not",
    "pscore",
  ],
  bearish: [
    "ticker", "sector", "today_ltp", "today_low", "sma_50",
    "sma_200", "death_cross_days_ago", "rsi", "rsi_max_10d",
    "rolling_low_20d_prev",
  ],
};

export function SwingSetupsTab() {
  const [regime, setRegime] = useState<SwingRegime>("bull");
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const [sort, setSort] = useState<string | null>(null);

  const { data, isLoading } = useSwingSetups({
    regime, market: "all", page, pageSize, sort,
  });

  return (
    <div className="space-y-4">
      <div
        className="flex items-center justify-between"
        data-testid="swing-setups-header"
      >
        <SwingRegimePills value={regime} onChange={(r) => {
          setRegime(r);
          setPage(1);
        }} />
        {data && (
          <span className="text-xs text-muted-foreground">
            As of {data.as_of}
          </span>
        )}
      </div>

      {data && (
        <SwingMethodologyPanel
          methodology={data.methodology}
          recGateApplied={data.rec_gate_applied}
          notes={data.notes}
        />
      )}

      <AdvancedAnalyticsTable
        report={"swing-setups" as never}
        rows={data?.rows ?? []}
        total={data?.total ?? 0}
        loading={isLoading}
        page={page}
        pageSize={pageSize}
        onPageChange={setPage}
        sort={sort}
        onSortChange={setSort}
        defaultVisibleCols={DEFAULT_COLS[regime]}
        columnSelectorStorageKey={`aa.swing.${regime}`}
        lockedCols={["ticker"]}
        emptyState={
          <div className="py-12 text-center text-muted-foreground">
            No setups match today.
          </div>
        }
      />
    </div>
  );
}
```

*If `AdvancedAnalyticsTable` has a different API in the codebase (different prop names), adapt to its actual prop shape. The intent: reuse the shared table; pass per-regime defaults + storage key. Grep:*

```bash
grep -rn "AdvancedAnalyticsTable\|ScreenerTab\|ScreenQLTab" \
  frontend/components/advanced-analytics/ | head -10
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run \
  components/advanced-analytics/__tests__/SwingSetupsTab.test.tsx
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/advanced-analytics/SwingSetupsTab.tsx \
        frontend/components/advanced-analytics/__tests__/SwingSetupsTab.test.tsx
git commit -m "feat(aa-swing): SwingSetupsTab — pills + methodology + table"
```

---

## Task 22: Register tab in AA tab strip

**Files:**
- Modify: `frontend/components/advanced-analytics/AdvancedAnalyticsTabs.tsx` (verify exact filename — grep)

- [ ] **Step 1: Locate the tab strip**

```bash
grep -rn "current-day-upmove\|previous-day-breakout" \
  frontend/components/advanced-analytics/ | head -5
```

Open the file that owns the tab strip.

- [ ] **Step 2: Add the new tab definition**

In the tabs array/object (the exact shape depends on the existing pattern), add:

```typescript
{
  value: "swing-setups",
  label: "Swing Setups",
  content: <SwingSetupsTab />,
}
```

Import at the top:

```typescript
import { SwingSetupsTab } from "./SwingSetupsTab";
```

- [ ] **Step 3: Update tab-strip layout if testid registry is used**

If the file already references testids from `e2e/utils/selectors.ts`, add `swing-setups-tab` to the strip's mapping.

- [ ] **Step 4: Manual smoke**

```bash
./run.sh start
# Open browser: http://localhost:3000/advanced-analytics?tab=swing-setups
# Confirm: tab appears, defaults to Bull regime, methodology strip
# visible expanded on first load.
```

- [ ] **Step 5: Type-check**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json
```

- [ ] **Step 6: Commit**

```bash
git add frontend/components/advanced-analytics/AdvancedAnalyticsTabs.tsx
git commit -m "feat(aa-swing): register swing-setups in AA tab strip"
```

---

## Task 23: E2E selectors + spec

**Files:**
- Modify: `e2e/utils/selectors.ts`
- Create: `e2e/pages/frontend/AdvancedAnalyticsSwingPage.ts`
- Create: `e2e/tests/frontend/aa-swing-setups.spec.ts`

- [ ] **Step 1: Add testids to the selector registry**

In `e2e/utils/selectors.ts`, add to the `FE` object:

```typescript
  swingSetupsTab: "swing-setups-tab",
  swingRegimePillBull: "swing-regime-pill-bull",
  swingRegimePillSideways: "swing-regime-pill-sideways",
  swingRegimePillBearish: "swing-regime-pill-bearish",
  swingMethodologyPanel: "swing-methodology-panel",
  swingMethodologyToggle: "swing-methodology-toggle",
  swingSetupsHeader: "swing-setups-header",
```

- [ ] **Step 2: Create the page object**

`e2e/pages/frontend/AdvancedAnalyticsSwingPage.ts`:

```typescript
import { Locator, Page } from "@playwright/test";
import { BasePage } from "./BasePage";  // adapt path if needed
import { FE } from "../../utils/selectors";

export class AdvancedAnalyticsSwingPage extends BasePage {
  readonly methodologyPanel: Locator;
  readonly bullPill: Locator;
  readonly sidewaysPill: Locator;
  readonly bearishPill: Locator;
  readonly toggle: Locator;

  constructor(page: Page) {
    super(page);
    this.methodologyPanel = this.tid(FE.swingMethodologyPanel);
    this.bullPill = this.tid(FE.swingRegimePillBull);
    this.sidewaysPill = this.tid(FE.swingRegimePillSideways);
    this.bearishPill = this.tid(FE.swingRegimePillBearish);
    this.toggle = this.tid(FE.swingMethodologyToggle);
  }

  async goto() {
    await this.page.goto(
      "/advanced-analytics?tab=swing-setups",
    );
  }
}
```

- [ ] **Step 3: Create the spec**

`e2e/tests/frontend/aa-swing-setups.spec.ts`:

```typescript
import { expect, test } from "../../fixtures/portfolio.fixture";
import { AdvancedAnalyticsSwingPage } from "../../pages/frontend/AdvancedAnalyticsSwingPage";

test.describe("Swing Setups tab", () => {
  test("renders bull list with methodology visible", async ({
    proPage,
  }) => {
    const swing = new AdvancedAnalyticsSwingPage(proPage);
    await swing.goto();

    await expect(swing.bullPill).toBeVisible();
    await expect(swing.methodologyPanel).toBeVisible();
    // Methodology shows gate labels from backend.
    await expect(
      proPage.getByText(/Trend stack|Volume sweet spot/),
    ).toBeVisible();
  });

  test("switching to bearish refetches with new regime", async ({
    proPage,
  }) => {
    const swing = new AdvancedAnalyticsSwingPage(proPage);
    await swing.goto();
    await swing.bearishPill.click();
    // Methodology header swaps to Bearish.
    await expect(
      proPage.getByText(/Bearish-swing list/),
    ).toBeVisible();
  });

  test("methodology panel collapses + persists", async ({
    proPage,
  }) => {
    const swing = new AdvancedAnalyticsSwingPage(proPage);
    await swing.goto();
    await swing.toggle.click();  // collapse
    await proPage.reload();
    // After reload, panel is still collapsed (gates hidden).
    await expect(
      proPage.getByText(/Trend stack/),
    ).not.toBeVisible();
  });
});
```

*Fixture name `proPage` is a placeholder — adapt to whatever the project uses (per CLAUDE.md §5.14, fixtures are pre-loaded under `e2e/fixtures/`). Grep `proPage|fixtures/.*pro` in e2e/.*

- [ ] **Step 4: Run the new spec**

```bash
cd e2e && npx playwright test \
  --project=analytics-chromium \
  tests/frontend/aa-swing-setups.spec.ts
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add e2e/utils/selectors.ts \
        e2e/pages/frontend/AdvancedAnalyticsSwingPage.ts \
        e2e/tests/frontend/aa-swing-setups.spec.ts
git commit -m "test(aa-swing): E2E selectors + spec"
```

---

## Task 24: Methodology↔filter snapshot test

**Files:**
- Modify: `backend/tests/test_advanced_analytics_swing.py`

This is the spec's §9 "methodology drift" guarantee: if someone tunes a threshold in `passes_bull` but forgets to update the methodology block (or vice versa), CI fails.

- [ ] **Step 1: Add the snapshot test**

Append:

```python
def test_methodology_thresholds_match_filter_constants() -> None:
    """Methodology rule strings must reference the same threshold
    constants the filters actually use. Catches drift.
    """
    from advanced_analytics_swing import (
        BULL_VOL_MIN, BULL_VOL_MAX, BULL_RSI_MAX, BULL_PSCORE_MIN,
        BULL_PLEDGED_MAX, BULL_RANGE_MAX,
        SIDEWAYS_MA_CONV_MAX, SIDEWAYS_RSI_MIN, SIDEWAYS_RSI_MAX,
        BEARISH_DEATH_CROSS_FRESH_DAYS, BEARISH_RSI_MAX_RECENT,
        BEARISH_RSI_TODAY_MAX,
    )

    bull = build_methodology("bull")
    rules_bull = " ".join(g["rule"] for g in bull["gates"])
    assert str(BULL_VOL_MIN) in rules_bull
    assert str(BULL_VOL_MAX) in rules_bull
    assert str(BULL_RSI_MAX) in rules_bull
    assert str(BULL_PSCORE_MIN) in rules_bull
    assert str(BULL_PLEDGED_MAX) in rules_bull
    assert str(BULL_RANGE_MAX) in rules_bull

    sw = build_methodology("sideways")
    rules_sw = " ".join(g["rule"] for g in sw["gates"])
    assert str(SIDEWAYS_MA_CONV_MAX) in rules_sw
    assert str(SIDEWAYS_RSI_MIN) in rules_sw
    assert str(SIDEWAYS_RSI_MAX) in rules_sw

    bear = build_methodology("bearish")
    rules_bear = " ".join(g["rule"] for g in bear["gates"])
    assert str(BEARISH_DEATH_CROSS_FRESH_DAYS) in rules_bear
    assert str(BEARISH_RSI_MAX_RECENT) in rules_bear
    assert str(BEARISH_RSI_TODAY_MAX) in rules_bear
```

- [ ] **Step 2: Run the test**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_swing.py -v \
  -k methodology_thresholds_match
```

Expected: PASS (Task 6 already encoded the thresholds in the methodology block).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_advanced_analytics_swing.py
git commit -m "test(aa-swing): snapshot test ensuring methodology mirrors filter constants"
```

---

## Task 25: Documentation + Serena memory + PROGRESS

**Files:**
- Modify: `CLAUDE.md`
- Create: `.serena/memories/shared/architecture/swing-setups-design.md`
- Modify: `PROGRESS.md`

- [ ] **Step 1: Add pattern-index row to CLAUDE.md §9**

In the table under §9 Pattern Index, add a row near the AA-tab-related entries:

```markdown
| Add a swing-setup regime | 5.4, 5.13 | `swing-setups-design` |
```

And in the Pattern-Index "Tabular page pattern" reference list, add `SwingSetupsTab` as one of the canonical examples.

- [ ] **Step 2: Create the Serena memory**

`.serena/memories/shared/architecture/swing-setups-design.md`:

```markdown
# swing-setups-design

**Created:** 2026-05-12
**Source:** docs/superpowers/specs/2026-05-12-swing-setups-design.md
**Plan:** docs/superpowers/plans/2026-05-12-swing-setups.md

## Summary

New Advanced Analytics tab `swing-setups` emits three ranked,
user-scoped watchlists per trading day: Bull / Sideways / Bearish.
Each regime has its own filter set and rank formula encoded in
`backend/advanced_analytics_swing.py` (the single source of truth
that the route consumes AND the on-page methodology panel renders).

## Where to look

- `backend/advanced_analytics_swing.py` — thresholds, BULLISH_CATEGORIES,
  build_methodology, passes_{bull,sideways,bearish}, rank_*.
- `backend/advanced_analytics_routes.py` — `_compute_swing_setup`,
  `/swing-setups`, `/swing-setups/methodology` endpoints,
  `_load_latest_recommendations`, the 5 new computed cols
  (`_death_cross_days_ago`, `_rolling_band_20d_prev`,
  `_rsi_lookback`).
- `frontend/components/advanced-analytics/SwingSetupsTab.tsx` —
  composition tab.
- `frontend/components/advanced-analytics/SwingMethodologyPanel.tsx` —
  backend-sourced rules panel; rules come from the response, never
  hardcoded.

## Conventions

- **Single source of truth**: tune thresholds in
  `advanced_analytics_swing.py` constants; the methodology block AND
  the filter behaviour move together. Drift is caught by
  `test_methodology_thresholds_match_filter_constants`.
- **Rec-engine graceful degrade**: when user has no rec run this
  month, `rec_gate_applied: false` is surfaced in the response. UI
  strikes through the Rec-engine bullish gate row + shows an amber
  transparency chip per §5.5.
- **New computed cols on AdvancedRow**: 8 optional fields added
  (5 computed + 3 rec-join). Defaults `None` so the seven existing AA
  reports remain unchanged.
- **Caching**: key shape
  `cache:advanced_analytics:swing-setups:<regime>:{user_id}:<market>:p{page}:ps{page_size}:s{sort}`,
  TTL_STABLE=300, invalidation on writes to `stocks.ohlcv`,
  `stocks.nse_delivery`, `stocks.recommendations`,
  `stocks.recommendation_runs`.

## Pinned bullish category set

Pinned 2026-05-12 from DB inspection (see plan Task 0):

- `offensive` (aggressive long signal)
- `value` (value pick)
- `growth` (growth pick)
- `hold_accumulate` (accumulate / hold)

Rec engine uses a portfolio-action vocabulary — not stock-rating.
Other live categories (`defensive`, `rebalance`, `risk_alert`,
`gap_fill`, `diversification`) are direction-agnostic or bearish.
Severity is surfaced on the row for analysis but NOT used as a
hard gate in Phase A.

When the rec engine introduces new categories, update
`BULLISH_CATEGORIES` in `advanced_analytics_swing.py` AND the
test snapshot.

## Phase C hook

The three regime rule sets are the seed for the Phase C algo-DSL
strategy templates (`project_algo_v3_complete`). Once a few weeks of
hit-rate data are observed, lift each `passes_*` function into an
AST template that runs in backtest / paper / live.
```

- [ ] **Step 3: Update PROGRESS.md**

Append a dated entry:

```markdown
## 2026-05-12 — Swing Setups (Advanced Analytics)

- New `/swing-setups` + `/swing-setups/methodology` endpoints.
- New `SwingSetupsTab` with Bull / Sideways / Bearish pills.
- Backend-sourced methodology block — single source of truth for
  filter thresholds AND the on-page explanation; drift snapshot
  test in place.
- 8 new optional fields on `AdvancedRow`; 5 derived from existing
  215-row OHLCV history (no new I/O), 3 from a batched rec-engine
  join.
- Bullish category set pinned: Strong Buy / Buy / Accumulate.
- Graceful degrade when user has no rec run this month — chip +
  strike-through on the panel.
- Ships as feature/aa-swing-setups, single PR.
```

- [ ] **Step 4: Stage Serena memory**

```bash
git add .serena/
```

(§4.4 #24 — Serena memories tracked in git.)

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md PROGRESS.md .serena/memories/shared/architecture/swing-setups-design.md
git commit -m "docs(aa-swing): CLAUDE.md pattern row, Serena memory, PROGRESS entry"
```

---

## Task 26: Run the full sweep before PR

- [ ] **Step 1: Backend full test**

```bash
docker compose exec backend python -m pytest backend/tests/ -v
```

Expected: all green. If a previously-existing AA test fails because of the new optional `AdvancedRow` fields, that's a Task-1/Task-5 regression — fix by passing the new fields as `None`.

- [ ] **Step 2: Frontend vitest**

```bash
cd frontend && npx vitest run
```

Expected: all green (existing + new tests).

- [ ] **Step 3: Lint**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui && \
  black backend/ && isort backend/ --profile black && \
  flake8 backend/
cd frontend && npx eslint . --fix
```

Expected: no violations.

- [ ] **Step 4: Type-check**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json
```

Expected: clean.

- [ ] **Step 5: E2E (1 worker, analytics project)**

```bash
cd e2e && npx playwright test \
  --project=analytics-chromium tests/frontend/aa-swing-setups.spec.ts
```

Expected: 3 passed.

- [ ] **Step 6: Manual smoke**

```bash
./run.sh start
# Open http://localhost:3000/advanced-analytics?tab=swing-setups
# Click each pill, verify methodology re-renders.
# Toggle collapse, reload, verify persistence.
# Sign in as a general user → tab is hidden (Pro-only).
```

- [ ] **Step 7: Open PR**

```bash
git push -u origin feature/aa-swing-setups
gh pr create --base dev --title \
  "feat(aa-swing): Swing Setups tab — Bull / Sideways / Bearish ranked lists" \
  --body "$(cat <<'EOF'
## Summary

- New Advanced Analytics tab `swing-setups` with three ranked
  watchlists per trading day (Bull / Sideways / Bearish).
- Backend single source of truth for filter thresholds in
  `advanced_analytics_swing.py`; same module powers the on-page
  methodology panel so rules ≡ explanation, always.
- Reuses existing AdvancedRow pipeline, two-layer cache, scoping
  helper, tabular-page-pattern — no new Iceberg tables, no
  pipeline step.

## Test plan

- [ ] Backend tests pass (`pytest backend/tests/`)
- [ ] Frontend vitest passes
- [ ] E2E `aa-swing-setups.spec.ts` passes
- [ ] Pro user can see + use all three regimes
- [ ] General user does NOT see the tab (Pro-only guard verified)
- [ ] User with no rec run this month → bull tab shows degraded
      chip + struck-through rec gate
- [ ] Methodology panel: expand/collapse persists across reload
- [ ] Cache invalidation: trigger OHLCV write, swing-setups recomputes

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Reset local `dev` to origin/dev** (cleanup, see spec session-end note)

After the PR is merged (squash) and you've pulled `dev`, the local-dev
fast-forward will reconcile. If `dev` ever needs manual sync before
that:

```bash
git checkout dev
git fetch origin
git reset --hard origin/dev  # destructive — recovers because the
                             # work lives on feature branch / PR
```

---

## Self-Review Summary

**Spec coverage:**

- §1 Goal — Tasks 14, 21 deliver the three-regime tab.
- §3 Where this fits — Tasks 14, 22.
- §4 Data model — Tasks 1, 2, 3, 4, 5, 10, 11.
- §5.1 Bull — Tasks 6 (constants), 7 (filter+rank).
- §5.2 Sideways — Tasks 6, 8.
- §5.3 Bearish — Tasks 6, 9.
- §6 API surface (route + methodology endpoint) — Tasks 12, 14, 15.
- §7.1-7.3 Frontend tab + pills + table — Tasks 19, 21, 22.
- §7.4 Methodology panel — Task 20.
- §7.5 Row actions — inherited from existing AdvancedAnalyticsTable.
- §8 Caching — Tasks 14 (cache key in route), 16 (invalidation map).
- §9 Testing — every backend task has a `_test_*` step;
  Tasks 20, 21 cover vitest; Task 23 covers E2E; Task 24 is the
  drift snapshot test.
- §10 Phased rollout — Phase A only here; Phase C referenced in
  memory (Task 25).
- §11 Open questions — Q1 in Task 0; Q2 / Q3 / Q4 already pinned in
  this plan's preamble.
- §12 Risks — graceful-degrade implementation in Tasks 13, 20.
- §13 Out of scope — respected (three-day-scan bug not touched;
  no `current-day-downmove` report; no algo-DSL port).

**Placeholder scan:** None of the forbidden patterns appear.
Test fixtures with placeholder helper-names (`_load_rows_for_universe`,
fixture names like `auth_header_pro`) are flagged as
"verify with grep, adapt to actual name" — that's a deliberate
acknowledgement that the plan can't predict the exact symbol; the
engineer has the explicit grep command to find it.

**Type consistency:** `passes_bull(row, rec_gate_applied)` signature
is consistent across Tasks 7, 13. `_load_latest_recommendations`
return shape consistent across Tasks 10, 11, 13. `SwingMethodology`
fields consistent across Tasks 12, 13, 14, 20.

**Scope check:** ~14 SP, single sprint, single PR. No decomposition
needed.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-12-swing-setups.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task (or per logical group of 2-3 tasks), review the diff between tasks, fast iteration. Best fit for this plan because tasks build on each other and a fresh context per task avoids drift.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints for your review.

**Which approach?**
