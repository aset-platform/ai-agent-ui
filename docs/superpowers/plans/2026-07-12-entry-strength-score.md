# Entry Strength Score (ESS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a screening-only Entry Strength Score (ESS) to the Watchlist Stocks
page, measuring whether today's pullback on a stock is healthy or a
breakdown-in-progress, orthogonal to the existing Quality Score, persisted daily
to a new Iceberg table for later effectiveness analysis.

**Architecture:** Pure-function scoring module (`backend/entry_strength_score.py`)
computes hard gates + 6 weighted continuous factors from data already loaded in
`_watchlist_stocks()`'s existing per-ticker loop (zero new fetches for the
per-page read path). A new low-write Iceberg table
(`stocks.entry_quality_daily`) persists QM Score + ESS + sub-factors once/day via
a new scheduled job, for the `allowed_tickers` ∪ QM≥58 universe. Frontend adds
columns + tooltips + filter chips to the existing Watchlist Stocks table,
following its established patterns exactly (no new page).

**Tech Stack:** Python 3.12 / pandas / PyIceberg (backend), Next.js 16 / React 19
/ TypeScript (frontend), pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-07-12-entry-strength-score-design.md`

## Global Constraints

- Line length 79 chars (black/isort/flake8) on all Python.
- No bare `print()` — use `_logger = logging.getLogger(__name__)`.
- `X | None` not `Optional[X]` (PEP 604).
- Iceberg writes MUST propagate errors — never silence.
- New scheduler jobs MUST be inserted into the `scheduled_jobs` PG table in the
  same PR as `@register_job` registration, or the job silently never runs.
- New write-heavy... N/A here — `stocks.entry_quality_daily` is **low-write**
  (~1 commit/day): only `ALL_TABLES` + `DATE_COLUMNS` enrollment, NOT
  `_HOT_ICEBERG_TABLES`.
- Patch at source module, not importer, in all tests (`mock-patching-gotchas`
  convention).
- `<span>` not `<div>` inside `<p>` in any new JSX (hydration).
- Every ESS-related table header needs a `ColumnTooltip` explaining the exact
  sub-fields/formula (user-requested transparency requirement).

---

## Interface contract (all tasks reference these exact names)

```python
# backend/entry_strength_score.py

def close_location_value(open_: float, high: float, low: float, close: float) -> float
def lower_wick_ratio(open_: float, high: float, low: float, close: float) -> float
def selling_absorption_score(open_: float, high: float, low: float, close: float) -> float

def relative_volume_ratio(volume_series: pd.Series, window: int = 20) -> float | None
def absorption_volume_score(absorption_score: float, rel_volume: float | None) -> float | None

def sma50_proximity_score(dist_sma50_pct: float | None) -> float | None
def check_hard_gates(close: float, sma200: float | None, dist_sma50_pct: float | None) -> tuple[bool, str | None]

def trend_stability_score(sma50_series: pd.Series, lookback: int = 10) -> float | None
def selling_deceleration_score(close_series: pd.Series) -> float | None
def roc5_score(close_series: pd.Series) -> tuple[float | None, float | None]  # (raw_pct, score)
def atr_expansion_score(atr_series: pd.Series, lookback: int = 10) -> float | None

@dataclass
class EssResult:
    ess_score: float | None
    gate_passed: bool
    gate_reason: str | None
    absorption_volume_score: float | None
    sma50_proximity_score: float | None
    trend_stability_score: float | None
    selling_deceleration_score: float | None
    roc5_score: float | None
    roc5_raw_pct: float | None
    atr_expansion_score: float | None

def compute_ess(
    open_: float, high: float, low: float, close: float,
    volume_series: pd.Series, sma50_series: pd.Series,
    atr_series: pd.Series, close_series: pd.Series,
    sma200: float | None, dist_sma50_pct: float | None,
) -> EssResult

@dataclass
class NiftyMarketContext:
    nifty_return_pct: float | None
    nifty_roc5_pct: float | None
    nifty_below_sma200: bool | None
    nifty_roc5_extreme: bool

def compute_nifty_market_context(nifty_close_series: pd.Series) -> NiftyMarketContext
```

`close_series`/`sma50_series`/`atr_series`/`nifty_close_series` are all
oldest-to-newest ordered `pd.Series` (matching `grp["close"]` / `ind["SMA_50"]`
/ `ind["ATR_14"]` ordering already used in `insights_routes.py`).

---

### Task 1: Selling Absorption (CLV + lower wick)

**Files:**
- Create: `backend/entry_strength_score.py`
- Test: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Produces: `close_location_value`, `lower_wick_ratio`, `selling_absorption_score`
  (see contract above) — consumed by Task 2 and Task 8.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_entry_strength_score.py
from __future__ import annotations

from entry_strength_score import (
    close_location_value,
    lower_wick_ratio,
    selling_absorption_score,
)


def test_clv_close_at_high_is_one():
    assert close_location_value(100, 120, 100, 120) == 1.0


def test_clv_close_at_low_is_zero():
    assert close_location_value(120, 120, 100, 100) == 0.0


def test_clv_no_range_defaults_half():
    assert close_location_value(100, 100, 100, 100) == 0.5


def test_lower_wick_ratio_strong_reversal():
    # Open 120, Low 112, Close 119 — buyers absorbed selling.
    ratio = lower_wick_ratio(120, 120, 112, 119)
    assert round(ratio, 3) == round((119 - 112) / (120 - 112), 3)


def test_lower_wick_ratio_marubozu_close_at_low():
    # Open 120, Low 112, Close 113 — still closing near the low.
    ratio = lower_wick_ratio(120, 120, 112, 113)
    assert round(ratio, 3) == round((113 - 112) / (120 - 112), 3)


def test_selling_absorption_blends_60_40():
    clv = close_location_value(120, 120, 112, 119)
    wick = lower_wick_ratio(120, 120, 112, 119)
    expected = round((0.6 * clv + 0.4 * wick) * 100, 4)
    assert selling_absorption_score(120, 120, 112, 119) == expected


def test_selling_absorption_marubozu_scores_low():
    strong = selling_absorption_score(120, 120, 112, 119)
    weak = selling_absorption_score(120, 120, 112, 113)
    assert weak < strong
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'entry_strength_score'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/entry_strength_score.py
"""Entry Strength Score (ESS) — pullback-health scoring for Watchlist
Stocks, orthogonal to the existing Quality Score."""

from __future__ import annotations


def close_location_value(
    open_: float, high: float, low: float, close: float
) -> float:
    rng = high - low
    if rng <= 0:
        return 0.5
    return (close - low) / rng


def lower_wick_ratio(
    open_: float, high: float, low: float, close: float
) -> float:
    rng = high - low
    if rng <= 0:
        return 0.0
    body_low = min(open_, close)
    return (body_low - low) / rng


def selling_absorption_score(
    open_: float, high: float, low: float, close: float
) -> float:
    clv = close_location_value(open_, high, low, close)
    wick = lower_wick_ratio(open_, high, low, close)
    return round((0.6 * clv + 0.4 * wick) * 100, 4)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add Selling Absorption score (CLV + lower wick)"
```

---

### Task 2: Relative Volume + Absorption-Volume interaction grid

**Files:**
- Modify: `backend/entry_strength_score.py`
- Modify: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Consumes: `selling_absorption_score` (Task 1).
- Produces: `relative_volume_ratio`, `absorption_volume_score` — consumed by
  Task 8.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from entry_strength_score import absorption_volume_score, relative_volume_ratio


def test_relative_volume_ratio_basic():
    # Last value is "today"; window average excludes it.
    vols = pd.Series([100.0] * 20 + [150.0])
    assert relative_volume_ratio(vols, window=20) == 1.5


def test_relative_volume_ratio_insufficient_history_returns_none():
    vols = pd.Series([100.0, 110.0])
    assert relative_volume_ratio(vols, window=20) is None


def test_absorption_volume_strong_and_elevated_scores_best():
    score = absorption_volume_score(absorption_score=85, rel_volume=2.0)
    assert score == 95


def test_absorption_volume_weak_and_elevated_scores_worst():
    score = absorption_volume_score(absorption_score=20, rel_volume=2.0)
    assert score == 25


def test_absorption_volume_none_rel_volume_returns_none():
    assert absorption_volume_score(absorption_score=85, rel_volume=None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ImportError` (names not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `backend/entry_strength_score.py`:

```python
import pandas as pd

# (absorption_band, volume_band) -> score. Illustrative starting grid —
# refine once real allowed_tickers outcome data accumulates (see spec §6.1).
_ABSORPTION_VOLUME_GRID: dict[tuple[str, str], float] = {
    ("weak", "low"): 55, ("weak", "normal"): 45,
    ("weak", "elevated"): 25, ("weak", "extreme"): 10,
    ("neutral", "low"): 65, ("neutral", "normal"): 70,
    ("neutral", "elevated"): 55, ("neutral", "extreme"): 35,
    ("strong", "low"): 70, ("strong", "normal"): 85,
    ("strong", "elevated"): 95, ("strong", "extreme"): 80,
}


def relative_volume_ratio(
    volume_series: pd.Series, window: int = 20
) -> float | None:
    if len(volume_series) < window + 1:
        return None
    today = float(volume_series.iloc[-1])
    avg = float(volume_series.iloc[-(window + 1):-1].mean())
    if avg <= 0:
        return None
    return round(today / avg, 4)


def _absorption_band(score: float) -> str:
    if score < 40:
        return "weak"
    if score <= 70:
        return "neutral"
    return "strong"


def _volume_band(ratio: float) -> str:
    if ratio < 0.8:
        return "low"
    if ratio <= 1.5:
        return "normal"
    if ratio <= 2.5:
        return "elevated"
    return "extreme"


def absorption_volume_score(
    absorption_score: float, rel_volume: float | None
) -> float | None:
    if rel_volume is None:
        return None
    key = (_absorption_band(absorption_score), _volume_band(rel_volume))
    return _ABSORPTION_VOLUME_GRID[key]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add Relative Volume + Absorption-Volume interaction grid"
```

---

### Task 3: Piecewise closeness helper, SMA50 Proximity, hard gates

**Files:**
- Modify: `backend/entry_strength_score.py`
- Modify: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Produces: `sma50_proximity_score`, `check_hard_gates`, private
  `_piecewise_closeness` — consumed by Task 4 (trend stability reuses the same
  interpolation shape) and Task 8.

- [ ] **Step 1: Write the failing tests**

```python
from entry_strength_score import check_hard_gates, sma50_proximity_score


def test_sma50_proximity_peaks_near_ideal_range():
    assert sma50_proximity_score(-3.0) == 100
    assert sma50_proximity_score(-2.0) == 95


def test_sma50_proximity_drops_at_extension():
    assert sma50_proximity_score(-10.0) == 40


def test_sma50_proximity_none_when_missing():
    assert sma50_proximity_score(None) is None


def test_hard_gate_rejects_price_below_sma200():
    passed, reason = check_hard_gates(close=90, sma200=100, dist_sma50_pct=-1)
    assert passed is False
    assert reason == "price_below_sma200"


def test_hard_gate_rejects_extended_below_sma50():
    passed, reason = check_hard_gates(
        close=100, sma200=90, dist_sma50_pct=-12
    )
    assert passed is False
    assert reason == "sma50_extended_beyond_10pct"


def test_hard_gate_passes_healthy_pullback():
    passed, reason = check_hard_gates(
        close=100, sma200=90, dist_sma50_pct=-3
    )
    assert passed is True
    assert reason is None


def test_hard_gate_missing_data_defaults_pass():
    # Can't evaluate a gate without data — don't reject on missing inputs.
    passed, reason = check_hard_gates(close=100, sma200=None, dist_sma50_pct=None)
    assert passed is True
    assert reason is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/entry_strength_score.py`:

```python
_SMA50_PROXIMITY_POINTS: list[tuple[float, float]] = [
    (0.0, 60.0), (-2.0, 95.0), (-3.0, 100.0),
    (-5.0, 90.0), (-7.0, 70.0), (-10.0, 40.0),
]


def _piecewise_closeness(
    points: list[tuple[float, float]], value: float
) -> float:
    pts = sorted(points, key=lambda p: p[0])
    if value <= pts[0][0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
    elif value >= pts[-1][0]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
    else:
        x0 = y0 = x1 = y1 = None
        for i in range(len(pts) - 1):
            if pts[i][0] <= value <= pts[i + 1][0]:
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                break
    slope = (y1 - y0) / (x1 - x0)
    result = y0 + slope * (value - x0)
    return round(max(0.0, min(100.0, result)), 4)


def sma50_proximity_score(dist_sma50_pct: float | None) -> float | None:
    if dist_sma50_pct is None:
        return None
    return _piecewise_closeness(_SMA50_PROXIMITY_POINTS, dist_sma50_pct)


def check_hard_gates(
    close: float,
    sma200: float | None,
    dist_sma50_pct: float | None,
) -> tuple[bool, str | None]:
    if sma200 is not None and close < sma200:
        return False, "price_below_sma200"
    if dist_sma50_pct is not None and dist_sma50_pct < -10.0:
        return False, "sma50_extended_beyond_10pct"
    return True, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add SMA50 Proximity closeness curve + ESS hard gates"
```

---

### Task 4: Trend Stability (SMA50 slope)

**Files:**
- Modify: `backend/entry_strength_score.py`
- Modify: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Consumes: `_piecewise_closeness` (Task 3).
- Produces: `trend_stability_score` — consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from entry_strength_score import trend_stability_score


def test_trend_stability_rising_sma50_scores_high():
    sma50 = pd.Series([100.0 + i * 0.05 for i in range(11)])  # +0.5% over 10d
    score = trend_stability_score(sma50, lookback=10)
    assert score > 70


def test_trend_stability_flat_sma50_scores_mid():
    sma50 = pd.Series([100.0] * 11)
    assert trend_stability_score(sma50, lookback=10) == 50


def test_trend_stability_declining_sma50_scores_low():
    sma50 = pd.Series([100.0 - i * 0.4 for i in range(11)])  # -4% over 10d
    score = trend_stability_score(sma50, lookback=10)
    assert score < 30


def test_trend_stability_insufficient_history_returns_none():
    sma50 = pd.Series([100.0, 101.0])
    assert trend_stability_score(sma50, lookback=10) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/entry_strength_score.py`:

```python
_TREND_STABILITY_POINTS: list[tuple[float, float]] = [
    (-3.0, 20.0), (0.0, 50.0), (1.0, 80.0), (3.0, 100.0),
]


def trend_stability_score(
    sma50_series: pd.Series, lookback: int = 10
) -> float | None:
    if len(sma50_series) < lookback + 1:
        return None
    today = float(sma50_series.iloc[-1])
    past = float(sma50_series.iloc[-(lookback + 1)])
    if past <= 0:
        return None
    slope_pct = (today - past) / past * 100
    return _piecewise_closeness(_TREND_STABILITY_POINTS, slope_pct)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (23 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add Trend Stability (SMA50 slope) score"
```

---

### Task 5: Selling Deceleration

**Files:**
- Modify: `backend/entry_strength_score.py`
- Modify: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Consumes: `_piecewise_closeness` (Task 3).
- Produces: `selling_deceleration_score` — consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from entry_strength_score import selling_deceleration_score


def test_selling_deceleration_improving_scores_high():
    # Daily closes implying returns roughly -4%,-3%,-1%,-0.3%
    closes = pd.Series([104.5, 100.32, 97.31, 96.34, 96.05])
    score = selling_deceleration_score(closes)
    assert score > 70


def test_selling_deceleration_accelerating_scores_low():
    # Returns roughly -1%,-1%,-4%,-4%
    closes = pd.Series([100.0, 99.0, 98.01, 94.09, 90.33])
    score = selling_deceleration_score(closes)
    assert score < 30


def test_selling_deceleration_insufficient_history_returns_none():
    closes = pd.Series([100.0, 99.0, 98.0])
    assert selling_deceleration_score(closes) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/entry_strength_score.py`:

```python
_SELLING_DECELERATION_POINTS: list[tuple[float, float]] = [
    (-2.0, 20.0), (0.0, 50.0), (1.0, 80.0), (3.0, 100.0),
]


def selling_deceleration_score(close_series: pd.Series) -> float | None:
    if len(close_series) < 5:
        return None
    returns = close_series.pct_change().dropna() * 100
    if len(returns) < 4:
        return None
    last2 = returns.iloc[-2:].mean()
    prev2 = returns.iloc[-4:-2].mean()
    deceleration = float(last2 - prev2)
    return _piecewise_closeness(_SELLING_DECELERATION_POINTS, deceleration)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (26 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add Selling Deceleration score"
```

---

### Task 6: ROC5

**Files:**
- Modify: `backend/entry_strength_score.py`
- Modify: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Consumes: `_piecewise_closeness` (Task 3).
- Produces: `roc5_score` — consumed by Task 8 and the Iceberg schema (raw pct).

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from entry_strength_score import roc5_score


def test_roc5_healthy_dip_scores_high():
    closes = pd.Series([100.0] * 5 + [96.0])  # -4%
    raw, score = roc5_score(closes)
    assert raw == -4.0
    assert score == 100


def test_roc5_falling_knife_scores_low():
    closes = pd.Series([100.0] * 5 + [82.0])  # -18%
    raw, score = roc5_score(closes)
    assert raw == -18.0
    assert score == 10


def test_roc5_insufficient_history_returns_none():
    closes = pd.Series([100.0, 99.0])
    assert roc5_score(closes) == (None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/entry_strength_score.py`:

```python
_ROC5_POINTS: list[tuple[float, float]] = [
    (0.0, 90.0), (-4.0, 100.0), (-8.0, 70.0),
    (-12.0, 40.0), (-18.0, 10.0),
]


def roc5_score(
    close_series: pd.Series,
) -> tuple[float | None, float | None]:
    if len(close_series) < 6:
        return None, None
    today = float(close_series.iloc[-1])
    past = float(close_series.iloc[-6])
    if past <= 0:
        return None, None
    raw = round((today - past) / past * 100, 4)
    return raw, _piecewise_closeness(_ROC5_POINTS, raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (29 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add ROC5 score"
```

---

### Task 7: ATR Expansion

**Files:**
- Modify: `backend/entry_strength_score.py`
- Modify: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Consumes: `_piecewise_closeness` (Task 3).
- Produces: `atr_expansion_score` — consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from entry_strength_score import atr_expansion_score


def test_atr_expansion_stable_scores_high():
    atr = pd.Series([2.0] * 10 + [1.9])  # ratio 0.95, within flat plateau
    assert atr_expansion_score(atr, lookback=10) == 90


def test_atr_expansion_exploding_scores_low():
    atr = pd.Series([2.0] * 10 + [4.0])  # ratio 2.0
    assert atr_expansion_score(atr, lookback=10) == 10


def test_atr_expansion_insufficient_history_returns_none():
    atr = pd.Series([2.0, 2.1])
    assert atr_expansion_score(atr, lookback=10) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/entry_strength_score.py`:

```python
_ATR_EXPANSION_POINTS: list[tuple[float, float]] = [
    (0.7, 90.0), (1.0, 90.0), (1.3, 60.0),
    (1.6, 30.0), (2.0, 10.0),
]


def atr_expansion_score(
    atr_series: pd.Series, lookback: int = 10
) -> float | None:
    if len(atr_series) < lookback + 1:
        return None
    today = float(atr_series.iloc[-1])
    past = float(atr_series.iloc[-(lookback + 1)])
    if past <= 0:
        return None
    ratio = today / past
    return _piecewise_closeness(_ATR_EXPANSION_POINTS, ratio)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (32 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add ATR Expansion score"
```

---

### Task 8: `compute_ess` orchestrator (weighted blend + renormalization)

**Files:**
- Modify: `backend/entry_strength_score.py`
- Modify: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Consumes: every function from Tasks 1-7.
- Produces: `EssResult`, `compute_ess` — consumed by Task 11 (route wiring) and
  Task 14 (scheduled job).

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from entry_strength_score import compute_ess


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals)


def test_compute_ess_gated_row_still_has_score_and_reason():
    result = compute_ess(
        open_=100, high=100, low=80, close=82,  # >10% below SMA50 fixture
        volume_series=_series([1_000_000.0] * 21),
        sma50_series=_series([100.0] * 11),
        atr_series=_series([2.0] * 11),
        close_series=_series([100.0] * 6),
        sma200=70,
        dist_sma50_pct=-18.0,
    )
    assert result.gate_passed is False
    assert result.gate_reason == "sma50_extended_beyond_10pct"
    assert result.ess_score is not None  # still computed for review, per spec §3


def test_compute_ess_healthy_pullback_scores_high():
    result = compute_ess(
        open_=120, high=120, low=112, close=119,
        volume_series=_series([1_000_000.0] * 20 + [1_800_000.0]),
        sma50_series=_series([100.0 + i * 0.05 for i in range(11)]),
        atr_series=_series([2.0] * 10 + [2.1]),
        close_series=_series([104.0, 100.0, 97.5, 96.5, 96.2, 96.0]),
        sma200=90,
        dist_sma50_pct=-3.0,
    )
    assert result.gate_passed is True
    assert result.ess_score is not None
    assert result.ess_score > 60


def test_compute_ess_missing_factor_renormalizes():
    # Too little history for trend_stability/selling_deceleration/roc5/
    # atr_expansion — only absorption+volume and sma50_proximity available.
    result = compute_ess(
        open_=120, high=120, low=112, close=119,
        volume_series=_series([1_000_000.0] * 20 + [1_800_000.0]),
        sma50_series=_series([100.0]),
        atr_series=_series([2.0]),
        close_series=_series([100.0]),
        sma200=90,
        dist_sma50_pct=-3.0,
    )
    assert result.ess_score is not None
    assert result.trend_stability_score is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/entry_strength_score.py`:

```python
from dataclasses import dataclass

_ESS_WEIGHTS: list[tuple[str, float]] = [
    ("absorption_volume_score", 0.30),
    ("sma50_proximity_score", 0.20),
    ("trend_stability_score", 0.15),
    ("selling_deceleration_score", 0.15),
    ("roc5_score", 0.12),
    ("atr_expansion_score", 0.08),
]


@dataclass
class EssResult:
    ess_score: float | None
    gate_passed: bool
    gate_reason: str | None
    absorption_volume_score: float | None
    sma50_proximity_score: float | None
    trend_stability_score: float | None
    selling_deceleration_score: float | None
    roc5_score: float | None
    roc5_raw_pct: float | None
    atr_expansion_score: float | None


def compute_ess(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume_series: pd.Series,
    sma50_series: pd.Series,
    atr_series: pd.Series,
    close_series: pd.Series,
    sma200: float | None,
    dist_sma50_pct: float | None,
) -> EssResult:
    gate_passed, gate_reason = check_hard_gates(close, sma200, dist_sma50_pct)

    absorption = selling_absorption_score(open_, high, low, close)
    rel_vol = relative_volume_ratio(volume_series)
    parts = {
        "absorption_volume_score": absorption_volume_score(absorption, rel_vol),
        "sma50_proximity_score": sma50_proximity_score(dist_sma50_pct),
        "trend_stability_score": trend_stability_score(sma50_series),
        "selling_deceleration_score": selling_deceleration_score(close_series),
        "roc5_score": None,
        "atr_expansion_score": atr_expansion_score(atr_series),
    }
    roc5_raw, roc5_val = roc5_score(close_series)
    parts["roc5_score"] = roc5_val

    available = [
        (parts[name], weight)
        for name, weight in _ESS_WEIGHTS
        if parts[name] is not None
    ]
    ess_score: float | None
    if available:
        total_weight = sum(w for _, w in available)
        ess_score = round(
            sum(v * w for v, w in available) / total_weight, 4
        )
    else:
        ess_score = None

    return EssResult(
        ess_score=ess_score,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
        absorption_volume_score=parts["absorption_volume_score"],
        sma50_proximity_score=parts["sma50_proximity_score"],
        trend_stability_score=parts["trend_stability_score"],
        selling_deceleration_score=parts["selling_deceleration_score"],
        roc5_score=parts["roc5_score"],
        roc5_raw_pct=roc5_raw,
        atr_expansion_score=parts["atr_expansion_score"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (35 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add compute_ess orchestrator with weighted blend"
```

---

### Task 9: Nifty market context (regime + ROC5 banners)

**Files:**
- Modify: `backend/entry_strength_score.py`
- Modify: `backend/tests/test_entry_strength_score.py`

**Interfaces:**
- Produces: `NiftyMarketContext`, `compute_nifty_market_context` — consumed by
  Task 11 (route) and Task 14 (scheduled job).

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from entry_strength_score import compute_nifty_market_context


def test_nifty_context_below_sma200_and_roc5_extreme():
    closes = pd.Series([100.0] * 194 + [100.0] * 5 + [93.0])
    ctx = compute_nifty_market_context(closes)
    assert ctx.nifty_below_sma200 is True
    assert ctx.nifty_roc5_extreme is True


def test_nifty_context_healthy_market():
    closes = pd.Series([90.0 + i * 0.1 for i in range(199)] + [110.0])
    ctx = compute_nifty_market_context(closes)
    assert ctx.nifty_below_sma200 is False
    assert ctx.nifty_roc5_extreme is False


def test_nifty_context_insufficient_history_returns_none_fields():
    closes = pd.Series([100.0, 101.0])
    ctx = compute_nifty_market_context(closes)
    assert ctx.nifty_below_sma200 is None
    assert ctx.nifty_roc5_pct is None
    assert ctx.nifty_roc5_extreme is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/entry_strength_score.py`:

```python
@dataclass
class NiftyMarketContext:
    nifty_return_pct: float | None
    nifty_roc5_pct: float | None
    nifty_below_sma200: bool | None
    nifty_roc5_extreme: bool


def compute_nifty_market_context(
    nifty_close_series: pd.Series,
) -> NiftyMarketContext:
    n = len(nifty_close_series)

    nifty_return_pct: float | None = None
    if n >= 2:
        prev = float(nifty_close_series.iloc[-2])
        today = float(nifty_close_series.iloc[-1])
        if prev > 0:
            nifty_return_pct = round((today - prev) / prev * 100, 4)

    nifty_roc5_pct, _ = roc5_score(nifty_close_series)

    nifty_below_sma200: bool | None = None
    if n >= 200:
        sma200 = float(nifty_close_series.iloc[-200:].mean())
        nifty_below_sma200 = float(nifty_close_series.iloc[-1]) < sma200

    nifty_roc5_extreme = (
        nifty_roc5_pct is not None and nifty_roc5_pct < -6.0
    )

    return NiftyMarketContext(
        nifty_return_pct=nifty_return_pct,
        nifty_roc5_pct=nifty_roc5_pct,
        nifty_below_sma200=nifty_below_sma200,
        nifty_roc5_extreme=nifty_roc5_extreme,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_entry_strength_score.py -v`
Expected: PASS (38 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/entry_strength_score.py backend/tests/test_entry_strength_score.py
git commit -m "feat(insights): add Nifty market context (regime + ROC5 banners)"
```

---

### Task 10: Extend `WatchlistStockRow` + response models

**Files:**
- Modify: `backend/insights_models.py:475-505`

**Interfaces:**
- Produces: new fields on `WatchlistStockRow` and a `WatchlistMarketContext`
  model on `WatchlistStocksResponse` — consumed by Task 11 (route) and Task 16
  (frontend types).

- [ ] **Step 1: Read the current models to confirm exact line numbers**

Run: `sed -n '470,510p' backend/insights_models.py`

- [ ] **Step 2: Add the new fields**

In `WatchlistStockRow` (after the existing `score: float | None = None` field),
add:

```python
    ess_score: float | None = None
    ess_gate_passed: bool | None = None
    ess_gate_reason: str | None = None
```

Add a new model above `WatchlistStocksResponse`:

```python
class WatchlistMarketContext(BaseModel):
    nifty_return_pct: float | None = None
    nifty_roc5_pct: float | None = None
    nifty_below_sma200: bool | None = None
    nifty_roc5_extreme: bool = False
```

Add a field to `WatchlistStocksResponse`:

```python
    market_context: WatchlistMarketContext | None = None
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run: `python -c "import backend.insights_models"`
Expected: no output, exit code 0

- [ ] **Step 4: Commit**

```bash
git add backend/insights_models.py
git commit -m "feat(insights): add ESS + market context fields to watchlist models"
```

---

### Task 11: Wire ESS into `_watchlist_stocks()` route

**Files:**
- Modify: `backend/insights_routes.py` (per-ticker loop starting ~line 2309,
  Nifty fetch ~line 2253, `WatchlistStockRow(...)` construction ~line 2520,
  response construction ~line 2667)
- Test: `backend/tests/test_entry_strength_score_route.py`

**Interfaces:**
- Consumes: `compute_ess`, `compute_nifty_market_context` (Tasks 8-9);
  `WatchlistMarketContext` (Task 10).

- [ ] **Step 1: Re-read the exact current route code before editing**

Run: `sed -n '2245,2260p;2305,2330p;2510,2530p;2660,2672p' backend/insights_routes.py`

Confirm against the plan's assumptions: the Nifty fetch's `LIMIT 135`, the
per-ticker loop variable names (`grp`, `ind`, `last`), and the exact
`WatchlistStockRow(...)` call site. If line numbers have drifted, use these as
search anchors instead of literal line numbers.

- [ ] **Step 2: Widen the Nifty fetch window (135 → 300 bars)**

Find the Nifty query (`SELECT date, close FROM ohlcv WHERE ticker = '^NSEI' ...
LIMIT 135`) and change `LIMIT 135` to `LIMIT 300`. This is the same window
already used per-ticker; 300 bars covers SMA200 (needs 200) + ROC5, so no new
fetch is introduced — reuses the query that's already there.

- [ ] **Step 3: Compute Nifty market context once, before the per-ticker loop**

Immediately after the Nifty dataframe is loaded (same place `_nifty_6m_return`
etc. are derived), add:

```python
from entry_strength_score import compute_nifty_market_context

_nifty_close_series = nifty_df.sort_values("date")["close"].astype(float)
_nifty_ctx = compute_nifty_market_context(_nifty_close_series)
```

(Adjust `nifty_df` to whatever the existing local variable name is per Step 1's
read-back — do not introduce a second Nifty fetch.)

- [ ] **Step 4: Compute ESS per ticker inside the existing loop**

Inside the per-ticker loop, after `ind`/`last`/`grp` are computed (same point
`sharpe_ratio`/`atr_pct`/`dist_sma200` are derived), add:

```python
from entry_strength_score import compute_ess

_dist_sma50_pct = None
if last.get("SMA_50") and last.get("SMA_50") > 0:
    _dist_sma50_pct = round(
        (float(last["Close"]) - float(last["SMA_50"]))
        / float(last["SMA_50"]) * 100, 4,
    )

_ess = compute_ess(
    open_=float(grp["open"].iloc[-1]),
    high=float(grp["high"].iloc[-1]),
    low=float(grp["low"].iloc[-1]),
    close=float(grp["close"].iloc[-1]),
    volume_series=grp["volume"].astype(float),
    sma50_series=ind["SMA_50"].dropna(),
    atr_series=ind["ATR_14"].dropna(),
    close_series=_close_s,
    sma200=(
        float(last["SMA_200"]) if last.get("SMA_200") else None
    ),
    dist_sma50_pct=_dist_sma50_pct,
)
```

(`_close_s` already exists in this loop per the research pass — reuse it rather
than re-slicing `grp["close"]`.)

- [ ] **Step 5: Pass ESS fields into the row constructor**

In the existing `WatchlistStockRow(...)` call, add:

```python
        ess_score=_ess.ess_score,
        ess_gate_passed=_ess.gate_passed,
        ess_gate_reason=_ess.gate_reason,
```

- [ ] **Step 6: Attach market context to the response**

At the `WatchlistStocksResponse(stocks=rows)` construction, change to:

```python
    from backend.insights_models import WatchlistMarketContext

    market_context = WatchlistMarketContext(
        nifty_return_pct=_nifty_ctx.nifty_return_pct,
        nifty_roc5_pct=_nifty_ctx.nifty_roc5_pct,
        nifty_below_sma200=_nifty_ctx.nifty_below_sma200,
        nifty_roc5_extreme=_nifty_ctx.nifty_roc5_extreme,
    )
    return WatchlistStocksResponse(
        stocks=rows, market_context=market_context
    )
```

- [ ] **Step 7: Write a route-level test with mocked Iceberg reads**

```python
# backend/tests/test_entry_strength_score_route.py
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

import backend.insights_routes as ir


@pytest.mark.asyncio
async def test_watchlist_stocks_includes_ess_fields():
    # Patch at the source module per the mock-patching-gotchas convention.
    ohlcv_rows = []
    dates = pd.date_range("2025-01-01", periods=300, freq="D")
    for d in dates:
        ohlcv_rows.append(
            {
                "ticker": "TCS.NS", "date": d, "open": 100.0,
                "high": 101.0, "low": 99.0, "close": 100.0,
                "volume": 1_000_000.0,
            }
        )
    ohlcv_df = pd.DataFrame(ohlcv_rows)
    nifty_df = pd.DataFrame(
        {"date": dates, "close": [100.0] * 300}
    )

    with patch.object(
        ir, "query_iceberg_df", new_callable=AsyncMock
    ) as mock_query:
        mock_query.side_effect = [ohlcv_df, nifty_df]
        with patch.object(
            ir, "_scoped_tickers", new_callable=AsyncMock
        ) as mock_scoped:
            mock_scoped.return_value = ["TCS.NS"]
            # Call the route handler per whatever its actual FastAPI
            # dependency-injected signature is (confirm via Step 1's
            # read-back — adjust user/session args to match).
            response = await ir.get_watchlist_stocks(...)

    assert response.market_context is not None
    row = response.stocks[0]
    assert row.ess_score is not None
    assert row.ess_gate_passed is not None
```

**Note for implementer:** the exact call signature of `get_watchlist_stocks`
(auth dependency, query params) must be confirmed by reading the route
decorator at Step 1 before finishing this test — do not guess the FastAPI
dependency wiring.

- [ ] **Step 8: Run the test**

Run: `python -m pytest backend/tests/test_entry_strength_score_route.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/insights_routes.py backend/tests/test_entry_strength_score_route.py
git commit -m "feat(insights): wire ESS + Nifty market context into watchlist route"
```

---

### Task 12: `stocks.entry_quality_daily` Iceberg table

**Files:**
- Modify: `stocks/create_tables.py`

**Interfaces:**
- Produces: `stocks.entry_quality_daily` table, callable via
  `create_tables()` — consumed by Task 14 (scheduled job writes to it).

- [ ] **Step 1: Read the worked template before writing**

Run:
```bash
sed -n '1,35p' backend/algo/iceberg_init.py
sed -n '43,172p' backend/algo/iceberg_init.py
sed -n '100,135p' stocks/create_tables.py
sed -n '2500,2545p' stocks/create_tables.py
```
Confirm `_create_table` signature and `_get_catalog()` usage before writing.

- [ ] **Step 2: Add schema/partition/sort functions**

In `stocks/create_tables.py`, add (near the other per-table schema functions):

```python
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table.sorting import NullOrder, SortDirection, SortField, SortOrder
from pyiceberg.transforms import MonthTransform
from pyiceberg.types import (
    BooleanType,
    DateType,
    DoubleType,
    NestedField,
    StringType,
    TimestampType,
)


def _entry_quality_daily_schema() -> Schema:
    return Schema(
        NestedField(1, "trade_date", DateType(), required=True),
        NestedField(2, "ticker", StringType(), required=True),
        NestedField(3, "market", StringType(), required=True),
        NestedField(4, "qm_score", DoubleType(), required=False),
        NestedField(5, "qm_sharpe_pctile", DoubleType(), required=False),
        NestedField(6, "qm_rs_pctile", DoubleType(), required=False),
        NestedField(7, "qm_mdd_pctile", DoubleType(), required=False),
        NestedField(8, "qm_atr_closeness", DoubleType(), required=False),
        NestedField(9, "qm_sma200_closeness", DoubleType(), required=False),
        NestedField(10, "ess_score", DoubleType(), required=False),
        NestedField(11, "ess_gate_passed", BooleanType(), required=False),
        NestedField(12, "ess_gate_reason", StringType(), required=False),
        NestedField(
            13, "ess_absorption_volume_score", DoubleType(), required=False
        ),
        NestedField(
            14, "ess_sma50_proximity_score", DoubleType(), required=False
        ),
        NestedField(
            15, "ess_trend_stability_score", DoubleType(), required=False
        ),
        NestedField(
            16, "ess_selling_deceleration_score", DoubleType(),
            required=False,
        ),
        NestedField(17, "ess_roc5_score", DoubleType(), required=False),
        NestedField(
            18, "ess_atr_expansion_score", DoubleType(), required=False
        ),
        NestedField(19, "nifty_return_pct", DoubleType(), required=False),
        NestedField(20, "nifty_roc5_pct", DoubleType(), required=False),
        NestedField(21, "nifty_below_sma200", BooleanType(), required=False),
        NestedField(22, "in_allowed_tickers", BooleanType(), required=False),
        NestedField(23, "written_at", TimestampType(), required=True),
    )


def _entry_quality_daily_partition_spec(schema: Schema) -> PartitionSpec:
    trade_date_id = next(
        f.field_id for f in schema.fields if f.name == "trade_date"
    )
    return PartitionSpec(
        PartitionField(
            source_id=trade_date_id,
            field_id=1000,
            transform=MonthTransform(),
            name="trade_date_month",
        )
    )


def _entry_quality_daily_sort_order(schema: Schema) -> SortOrder:
    ticker_id = next(
        f.field_id for f in schema.fields if f.name == "ticker"
    )
    trade_date_id = next(
        f.field_id for f in schema.fields if f.name == "trade_date"
    )
    return SortOrder(
        SortField(
            source_id=ticker_id,
            transform=MonthTransform().__class__.__bases__[0]()
            if False else None,  # placeholder removed below
        )
    )
```

**Stop — do not copy that last `SortOrder` block as-is.** Re-read
`backend/algo/iceberg_init.py`'s actual `_intraday_bars_sort_order()` function
body from Step 1's read-back and mirror its exact `SortField(...)` construction
(likely `IdentityTransform()` per field, `direction=SortDirection.ASC`,
`null_order=NullOrder.NULLS_LAST`) — the snippet above is intentionally
incomplete because the research pass didn't capture this function's full body;
copy the real one instead of guessing its shape.

- [ ] **Step 3: Register the table in `create_tables()`**

In `create_tables()` (the idempotent "create all stocks Iceberg tables"
entrypoint), add:

```python
    schema = _entry_quality_daily_schema()
    _create_table(
        catalog,
        "stocks.entry_quality_daily",
        schema,
        _entry_quality_daily_partition_spec(schema),
        sort_order=_entry_quality_daily_sort_order(schema),
    )
    _ensure_table_properties(
        catalog,
        "stocks.entry_quality_daily",
        {
            "write.metadata.delete-after-commit.enabled": "true",
            "write.metadata.previous-versions-max": "20",
        },
    )
```

- [ ] **Step 4: Run table creation and verify**

Run: `docker compose exec backend python -c "from stocks.create_tables import create_tables; create_tables()"`
Expected: no error; table created idempotently (safe to re-run)

Run: `docker compose exec backend python -c "
from pyiceberg.catalog import load_catalog
cat = load_catalog('default')
tbl = cat.load_table('stocks.entry_quality_daily')
print(tbl.schema())
"`
Expected: prints the 23-field schema with no errors

- [ ] **Step 5: Commit**

```bash
git add stocks/create_tables.py
git commit -m "feat(insights): create stocks.entry_quality_daily Iceberg table"
```

---

### Task 13: Maintenance enrollment (low-write tier)

**Files:**
- Modify: `backend/maintenance/iceberg_maintenance.py:49-109` (`ALL_TABLES`),
  `:119-144` (`DATE_COLUMNS`)

**Interfaces:**
- Consumes: `stocks.entry_quality_daily` (Task 12).

- [ ] **Step 1: Add to `ALL_TABLES`**

Add, right before the closing `]` of the `ALL_TABLES` list:

```python
    # Entry Strength Score daily snapshot (2026-07). Low-write
    # (~1 commit/day, allowed_tickers ∪ QM>=58 universe only) —
    # weekly long-tail maintenance is sufficient, does NOT need
    # _HOT_ICEBERG_TABLES.
    "stocks.entry_quality_daily",
```

- [ ] **Step 2: Add to `DATE_COLUMNS`**

Add to the `DATE_COLUMNS` dict:

```python
    "stocks.entry_quality_daily": "trade_date",
```

- [ ] **Step 3: Verify enrollment with the standard grep recipe**

Run:
```bash
grep -n "entry_quality_daily" backend/maintenance/iceberg_maintenance.py
```
Expected: two matches (one in `ALL_TABLES`, one in `DATE_COLUMNS`)

Run: `grep -n "entry_quality_daily" backend/jobs/executor.py`
Expected: no match in `_HOT_ICEBERG_TABLES` — confirms it's correctly excluded
from the daily hot-compaction tier per the low-write design decision.

- [ ] **Step 4: Commit**

```bash
git add backend/maintenance/iceberg_maintenance.py
git commit -m "chore(insights): enroll entry_quality_daily in maintenance + retention"
```

---

### Task 14: Scheduled EOD snapshot job

**Files:**
- Create: `backend/jobs/entry_quality_snapshot.py`
- Modify: `backend/jobs/executor.py` (add `@register_job` wrapper, near the
  `algo_closed_trades_rollup` wrapper at line ~3815 for a consistent template)
- Test: `backend/tests/test_entry_quality_snapshot.py`

**Interfaces:**
- Consumes: `compute_ess`, `compute_nifty_market_context` (Tasks 8-9);
  `stocks.entry_quality_daily` (Task 12).
- Produces: `run_entry_quality_snapshot_job(payload: dict | None) -> dict`.

- [ ] **Step 1: Read the exact templates before writing**

Run:
```bash
sed -n '1,80p' backend/algo/jobs/closed_trades_rollup.py
sed -n '3810,3840p' backend/jobs/executor.py
grep -n "def disposable_pg_session" -A 15 backend/db/engine.py
grep -n "_algo_job_success" backend/jobs/executor.py | head -5
```
Confirm the exact `disposable_pg_session()` usage and whether
`_algo_job_success` is algo-scoped-only (check its call sites) before deciding
whether this non-algo job should call it — if it's algo-specific status
reporting tied to `algo.runs`, this job should NOT call it (it isn't an algo
run); just return the result dict, matching whatever the non-algo scheduled
jobs in this same file do (grep a couple of non-`algo_*`-prefixed
`@register_job` entries for the general pattern).

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_entry_quality_snapshot.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.jobs.entry_quality_snapshot import _run


@pytest.mark.asyncio
async def test_snapshot_job_writes_expected_row_count():
    ohlcv_df = pd.DataFrame(
        {
            "ticker": ["TCS.NS"] * 300,
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "open": [100.0] * 300, "high": [101.0] * 300,
            "low": [99.0] * 300, "close": [100.0] * 300,
            "volume": [1_000_000.0] * 300,
        }
    )
    nifty_df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "close": [100.0] * 300,
        }
    )

    with patch(
        "backend.jobs.entry_quality_snapshot.disposable_pg_session"
    ) as mock_pg, patch(
        "backend.jobs.entry_quality_snapshot.query_iceberg_df",
        new_callable=AsyncMock,
    ) as mock_query, patch(
        "backend.jobs.entry_quality_snapshot._append_snapshot_rows"
    ) as mock_append:
        mock_session = AsyncMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("TCS.NS",)
        ]
        mock_pg.return_value.__aenter__.return_value = mock_session
        mock_query.side_effect = [ohlcv_df, nifty_df]

        result = await _run({})

    assert result["rows_written"] == 1
    mock_append.assert_called_once()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_entry_quality_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write the job implementation**

```python
# backend/jobs/entry_quality_snapshot.py
"""Daily EOD snapshot job for stocks.entry_quality_daily — persists
QM Score + Entry Strength Score for the allowed_tickers ∪ QM>=58
universe, per docs/superpowers/specs/2026-07-12-entry-strength-score-design.md
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from backend.db.duckdb_engine import query_iceberg_df
from backend.db.engine import disposable_pg_session
from backend.tools._analysis_indicators import _calculate_technical_indicators
from entry_strength_score import compute_ess, compute_nifty_market_context

_logger = logging.getLogger(__name__)

_ALLOWED_TICKERS_SQL = text(
    """
    SELECT DISTINCT jsonb_array_elements_text(lc.allowed_tickers) AS ticker
    FROM algo.live_caps lc
    JOIN algo.strategies s ON s.id = lc.strategy_id
    WHERE s.mode = 'live' AND s.archived_at IS NULL
    """
)


async def _allowed_tickers_union() -> set[str]:
    async with disposable_pg_session() as session:
        result = await session.execute(_ALLOWED_TICKERS_SQL)
        return {row[0] for row in result.fetchall()}


def _append_snapshot_rows(rows: list[dict[str, Any]]) -> None:
    # Batched single-commit Iceberg append — see Task 12 for schema.
    from pyiceberg.catalog import load_catalog
    import pyarrow as pa

    catalog = load_catalog("default")
    table = catalog.load_table("stocks.entry_quality_daily")
    table.append(pa.Table.from_pylist(rows, schema=table.schema().as_arrow()))


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = await _allowed_tickers_union()

    # QM Score >= 58 universe: reuse the existing watchlist scoping +
    # score computation path. Exact call confirmed at implementation
    # time against backend/insights_routes.py's post-loop QM Score
    # logic (percentile ranks require the full cross-stock batch, so
    # this cannot be computed ticker-by-ticker in isolation here).
    qualifying = allowed  # extended below once QM>=58 set is resolved

    ohlcv_df = await query_iceberg_df(
        "stocks.ohlcv",
        "SELECT ticker, date, open, high, low, close, volume FROM ohlcv "
        "WHERE ticker IN ({ph}) ORDER BY ticker, date",
        list(qualifying),
    )
    nifty_df = await query_iceberg_df(
        "stocks.ohlcv",
        "SELECT date, close FROM ohlcv WHERE ticker = '^NSEI' "
        "ORDER BY date DESC LIMIT 300",
    )
    nifty_ctx = compute_nifty_market_context(
        nifty_df.sort_values("date")["close"].astype(float)
    )

    written_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows: list[dict[str, Any]] = []
    for ticker, grp in ohlcv_df.groupby("ticker"):
        grp = grp.sort_values("date")
        if len(grp) < 6:
            continue

        # MUST use the exact same indicator computation the watchlist
        # route uses (Task 11), not an approximation — otherwise the
        # persisted snapshot silently disagrees with what the page
        # showed that day, which breaks the §9 validation premise
        # (comparing "what ESS said" against real outcomes only works
        # if it's the same number the user actually saw).
        df_in = grp.rename(
            columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            }
        ).set_index(pd.DatetimeIndex(grp["date"]))
        ind = _calculate_technical_indicators(df_in)
        last = ind.iloc[-1]

        dist_sma50_pct = None
        if last.get("SMA_50") and last["SMA_50"] > 0:
            dist_sma50_pct = round(
                (float(last["Close"]) - float(last["SMA_50"]))
                / float(last["SMA_50"]) * 100, 4,
            )

        ess = compute_ess(
            open_=float(grp["open"].iloc[-1]),
            high=float(grp["high"].iloc[-1]),
            low=float(grp["low"].iloc[-1]),
            close=float(grp["close"].iloc[-1]),
            volume_series=grp["volume"].astype(float),
            sma50_series=ind["SMA_50"].dropna(),
            atr_series=ind["ATR_14"].dropna(),
            close_series=grp["close"].astype(float),
            sma200=(
                float(last["SMA_200"]) if last.get("SMA_200") else None
            ),
            dist_sma50_pct=dist_sma50_pct,
        )
        rows.append(
            {
                "trade_date": grp["date"].iloc[-1].date(),
                "ticker": ticker,
                "market": "india",
                "qm_score": None,  # wired once QM>=58 source is finalized
                "qm_sharpe_pctile": None,
                "qm_rs_pctile": None,
                "qm_mdd_pctile": None,
                "qm_atr_closeness": None,
                "qm_sma200_closeness": None,
                "ess_score": ess.ess_score,
                "ess_gate_passed": ess.gate_passed,
                "ess_gate_reason": ess.gate_reason,
                "ess_absorption_volume_score": ess.absorption_volume_score,
                "ess_sma50_proximity_score": ess.sma50_proximity_score,
                "ess_trend_stability_score": ess.trend_stability_score,
                "ess_selling_deceleration_score": (
                    ess.selling_deceleration_score
                ),
                "ess_roc5_score": ess.roc5_score,
                "ess_atr_expansion_score": ess.atr_expansion_score,
                "nifty_return_pct": nifty_ctx.nifty_return_pct,
                "nifty_roc5_pct": nifty_ctx.nifty_roc5_pct,
                "nifty_below_sma200": nifty_ctx.nifty_below_sma200,
                "in_allowed_tickers": ticker in allowed,
                "written_at": written_at,
            }
        )

    if rows:
        _append_snapshot_rows(rows)
    return {"rows_written": len(rows)}


def run_entry_quality_snapshot_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return asyncio.run(_run(payload or {}))
```

**Note for implementer:** the `qm_score`/`qm_*_pctile` fields and the "QM>=58"
half of the universe union are INTENTIONALLY left as `None`/placeholder in
THIS task — ship it as a valid, self-contained intermediate state where ESS
persistence works standalone. Do NOT attempt the QM Score extraction here.
**Task 15 (the very next task in this plan) does that extraction as its own
fully-specified task** (`compute_qm_scores(ohlcv_df) -> dict[str, QmResult]`,
factored out of `insights_routes.py`'s post-loop QM Score block) and retrofits
real values into both this job and the route. An earlier draft of this note
told the Task 14 implementer to do the extraction "before finishing this
task," which directly duplicates/contradicts Task 15's own scope — that
instruction is superseded by this correction; Task 15 owns it exclusively.

Also: the real `query_iceberg_df` import (confirmed during Task 11) is
`from backend.db.duckdb_engine import query_iceberg_df` — use that, not the
`backend.iceberg_reader` placeholder path shown in the code skeleton above.

- [ ] **Step 5: Run the test**

Run: `python -m pytest backend/tests/test_entry_quality_snapshot.py -v`
Expected: PASS

- [ ] **Step 6: Register the job**

In `backend/jobs/executor.py`, near the `algo_closed_trades_rollup` wrapper:

```python
@register_job("entry_quality_snapshot")
def _job_entry_quality_snapshot(
    scope: str | None = None,
    run_id: str | None = None,
    repo=None,
    cancel_event=None,
    force: bool = False,
    payload: dict | None = None,
) -> dict:
    from backend.jobs.entry_quality_snapshot import (
        run_entry_quality_snapshot_job,
    )
    return run_entry_quality_snapshot_job(payload or {})
```

- [ ] **Step 7: Commit**

```bash
git add backend/jobs/entry_quality_snapshot.py backend/jobs/executor.py backend/tests/test_entry_quality_snapshot.py
git commit -m "feat(insights): add entry_quality_snapshot EOD scheduled job"
```

---

### Task 15: Factor out `compute_qm_scores` for reuse (route + job)

**Files:**
- Modify: `backend/insights_routes.py` (extract the QM Score block, lines
  ~2565-2665 per Task 11's read-back)
- Create/modify: `backend/entry_strength_score.py` or a new
  `backend/qm_score.py` (place next to `entry_strength_score.py` for symmetry)
- Modify: `backend/jobs/entry_quality_snapshot.py` (call the extracted
  function instead of leaving `qm_score` fields as `None`)
- Test: `backend/tests/test_qm_score.py`

**Interfaces:**
- Produces: `compute_qm_scores(ohlcv_df: pd.DataFrame) -> dict[str, QmResult]`
  where `QmResult` carries `score, sharpe_pctile, rs_pctile, mdd_pctile,
  atr_closeness, sma200_closeness`.
- Consumes: Task 11's confirmed line numbers for the existing QM Score logic.

- [ ] **Step 1: Read the exact existing QM Score block**

Run: `sed -n '2560,2670p' backend/insights_routes.py`

Copy its real `_ATR_PTS`, `_SMA_PTS`, `_pct_rank`, `_closeness`, and the final
weighted-blend loop verbatim into the new module — do not restate them from
memory, use what Step 1 prints.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_qm_score.py
import pandas as pd

from qm_score import compute_qm_scores


def test_compute_qm_scores_returns_one_result_per_ticker():
    # Two tickers with distinct Sharpe/RS/ATR/dist-SMA200 profiles.
    df = pd.DataFrame(
        {
            "ticker": ["A.NS"] * 130 + ["B.NS"] * 130,
            "date": list(pd.date_range("2025-01-01", periods=130)) * 2,
            "close": [100.0 + i * 0.5 for i in range(130)]
            + [100.0 - i * 0.2 for i in range(130)],
        }
    )
    results = compute_qm_scores(df)
    assert set(results.keys()) == {"A.NS", "B.NS"}
    assert results["A.NS"].score is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_qm_score.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Extract the implementation**

Move the exact logic read in Step 1 into `backend/qm_score.py`, wrapped as:

```python
from dataclasses import dataclass

import pandas as pd


@dataclass
class QmResult:
    score: float | None
    sharpe_pctile: float | None
    rs_pctile: float | None
    mdd_pctile: float | None
    atr_closeness: float | None
    sma200_closeness: float | None


def compute_qm_scores(ohlcv_df: pd.DataFrame) -> dict[str, QmResult]:
    # Body = the exact _ATR_PTS/_SMA_PTS/_pct_rank/_closeness/blend logic
    # read back in Step 1 — copy verbatim, adapted to iterate ohlcv_df's
    # tickers and return QmResult per ticker instead of mutating
    # WatchlistStockRow directly.
    ...
```

- [ ] **Step 5: Update `insights_routes.py` to call the extracted function**

Replace the inline QM Score block (Step 1's line range) with:

```python
from qm_score import compute_qm_scores

_qm_results = compute_qm_scores(ohlcv_df)
# then per row: row.score = _qm_results[ticker].score, etc.
```

- [ ] **Step 6: Run the full existing insights test suite to confirm no regression**

Run: `python -m pytest tests/backend/test_insights_scoping.py backend/tests/test_qm_score.py -v`
Expected: PASS, no change in existing QM Score behavior (this is a pure
extraction — same inputs must produce the same `score` values as before)

- [ ] **Step 7: Update `entry_quality_snapshot.py` to use real QM values**

In `backend/jobs/entry_quality_snapshot.py`, replace the `qm_score: None, ...`
placeholders with:

```python
from qm_score import compute_qm_scores

# after ohlcv_df is loaded, before the per-ticker loop:
_qm_results = compute_qm_scores(ohlcv_df)
qualifying = allowed | {
    t for t, r in _qm_results.items() if r.score is not None and r.score >= 58
}
```

and inside the per-ticker row dict:

```python
                "qm_score": _qm_results[ticker].score,
                "qm_sharpe_pctile": _qm_results[ticker].sharpe_pctile,
                "qm_rs_pctile": _qm_results[ticker].rs_pctile,
                "qm_mdd_pctile": _qm_results[ticker].mdd_pctile,
                "qm_atr_closeness": _qm_results[ticker].atr_closeness,
                "qm_sma200_closeness": _qm_results[ticker].sma200_closeness,
```

- [ ] **Step 8: Run all touched tests**

Run: `python -m pytest backend/tests/test_qm_score.py backend/tests/test_entry_quality_snapshot.py tests/backend/test_insights_scoping.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/qm_score.py backend/insights_routes.py backend/jobs/entry_quality_snapshot.py backend/tests/test_qm_score.py
git commit -m "refactor(insights): extract compute_qm_scores for route+job reuse"
```

---

### Task 16: Scheduled-job PG registration (`scheduled_jobs` row)

**Files:**
- Create: `scripts/seed_entry_quality_snapshot.py`

**Interfaces:**
- Consumes: `entry_quality_snapshot` job name (Task 14's `@register_job`).

- [ ] **Step 1: Read the exact template**

Run: `cat scripts/seed_closed_trades_rollup.py`

- [ ] **Step 2: Write the seed script**

Mirror it exactly, substituting the job identity:

```python
# scripts/seed_entry_quality_snapshot.py
"""Seed the scheduled_jobs row for entry_quality_snapshot.
Idempotent — safe to re-run (ON CONFLICT (name) DO UPDATE).
"""

import uuid

_NS = uuid.UUID("f3d4b5c6-0000-0000-0000-000000000000")  # confirm this
# matches the actual namespace UUID used in seed_closed_trades_rollup.py —
# do not invent a new one, reuse the same constant for consistency.

_JOB = {
    "name": "Entry Quality Snapshot (QM Score + ESS)",
    "job_type": "entry_quality_snapshot",
    "cron_days": "mon,tue,wed,thu,fri",
    "cron_time": "16:00",
    "enabled": True,
}


def seed() -> None:
    jid = str(uuid.uuid5(_NS, _JOB["name"]))
    # Same disposable_pg_session + INSERT ... ON CONFLICT (name) DO UPDATE
    # pattern as scripts/seed_closed_trades_rollup.py — copy its exact SQL
    # shape rather than restating it here, since the column list must match
    # the real scheduled_jobs schema byte-for-byte.


if __name__ == "__main__":
    seed()
```

- [ ] **Step 3: Run the seed script**

Run: `docker compose exec -e PYTHONPATH=.:backend backend python scripts/seed_entry_quality_snapshot.py`
Expected: no error

- [ ] **Step 4: Verify the row exists**

Run: `docker compose exec postgres psql -U postgres -d ai_agent_ui -c "SELECT name, job_type, cron_time, enabled FROM scheduled_jobs WHERE job_type = 'entry_quality_snapshot';"`
Expected: one row, `cron_time = 16:00`, `enabled = t`

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_entry_quality_snapshot.py
git commit -m "chore(insights): schedule entry_quality_snapshot job at 16:00 IST"
```

---

### Task 17: Frontend types

**Files:**
- Modify: `frontend/lib/types.ts:376-397` (`WatchlistStockRow` interface)

**Interfaces:**
- Produces: TS fields mirroring Task 10's Pydantic additions.

- [ ] **Step 1: Read the current interface**

Run: `sed -n '370,400p' frontend/lib/types.ts`

- [ ] **Step 2: Add the new fields**

```typescript
  ess_score: number | null;
  ess_gate_passed: boolean | null;
  ess_gate_reason: string | null;
```

Add a new interface and locate/extend the watchlist response wrapper type:

```typescript
export interface WatchlistMarketContext {
  nifty_return_pct: number | null;
  nifty_roc5_pct: number | null;
  nifty_below_sma200: boolean | null;
  nifty_roc5_extreme: boolean;
}
```

Then find the response type used by the page (grep
`WatchlistStocksResponse\|market_context` in `frontend/lib/types.ts` and
`frontend/hooks/`) and add `market_context: WatchlistMarketContext | null;` to
it.

- [ ] **Step 3: Verify the frontend typechecks**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors (existing errors, if any, are pre-existing and out of
scope)

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/types.ts
git commit -m "feat(insights): add ESS + market context TS types"
```

---

### Task 18: Frontend — ESS column, tooltip, filter, banners

**Files:**
- Modify: `frontend/app/(authenticated)/analytics/analysis/page.tsx`

**Interfaces:**
- Consumes: `WatchlistStockRow.ess_score/ess_gate_passed/ess_gate_reason`,
  `WatchlistMarketContext` (Task 17).

- [ ] **Step 1: Read the exact existing patterns before editing**

Run:
```bash
sed -n '2140,2150p' frontend/app/\(authenticated\)/analytics/analysis/page.tsx   # SCORE_BUCKETS
sed -n '2385,2460p' frontend/app/\(authenticated\)/analytics/analysis/page.tsx   # ScoreMultiSelect
sed -n '2456,2520p' frontend/app/\(authenticated\)/analytics/analysis/page.tsx   # ColumnTooltip
sed -n '3295,3350p' frontend/app/\(authenticated\)/analytics/analysis/page.tsx   # Score tooltip text + column headers + colSpan
sed -n '2544,2548p' frontend/app/\(authenticated\)/analytics/analysis/page.tsx   # SortKey type
```

- [ ] **Step 2: Extend `SortKey`**

```typescript
type SortKey =
  | "ticker" | "close" | "rsi_2" | "current_rsi_2" | "sma_200" | "sma_50"
  | "sharpe_ratio" | "blended_rs" | "rs_3m" | "rs_6m" | "mdd_6m" | "atr_pct"
  | "dist_sma200" | "score" | "ess_score";
```

- [ ] **Step 3: Add `ESS_BUCKETS` + `EssMultiSelect` (fifth copy of the
  established multi-select pattern)**

```typescript
const ESS_BUCKETS = [
  { value: "lt50", label: "< 50", caption: "Weak — Reject" },
  { value: "50to60", label: "50–60", caption: "Avoid" },
  { value: "60to70", label: "60–70", caption: "Tradable" },
  { value: "70to80", label: "70–80", caption: "Preferred" },
  { value: "80to90", label: "80–90", caption: "High priority" },
  { value: "gt90", label: "> 90", caption: "Elite" },
] as const;
type EssBucket = (typeof ESS_BUCKETS)[number]["value"];
```

Copy `ScoreMultiSelect`'s full component body (from Step 1's read-back)
verbatim, renaming `ScoreMultiSelect` → `EssMultiSelect` and `SCORE_BUCKETS` →
`ESS_BUCKETS` — this mirrors the codebase's existing precedent of near-
identical multi-select components (`DistSma200MultiSelect`,
`BlendedRsMultiSelect`, `Mdd6mMultiSelect`, `ScoreMultiSelect` are already four
copies of the same shape), so a fifth copy is consistent with established
style, not a DRY violation to fix here.

- [ ] **Step 4: Add the ESS column header with formula tooltip**

In the column-header array (from Step 1's read-back, same shape as the
existing `Score` entry), add:

```typescript
  {
    key: "ess_score" as SortKey,
    label: "ESS",
    tooltip:
      "Entry Strength Score (ESS) — 0–100, higher = healthier pullback, " +
      "today. Independent of Quality Score (QM Score) by design.\n\n" +
      "Weighted blend of 6 factors:\n" +
      "  Selling Absorption × Rel. Volume   30%\n" +
      "    Absorption = 60% Close-Location-Value + 40% Lower-Wick Ratio\n" +
      "    (both from today's OHLC), scored jointly against volume vs\n" +
      "    its 20-day average — high volume only scores well if the\n" +
      "    close also shows absorption.\n" +
      "  SMA50 Proximity                    20%  (ideal -2% to -3% " +
      "below SMA50)\n" +
      "  Trend Stability (SMA50 slope)      15%  (10-day slope; " +
      "flattening SMA50 scores low)\n" +
      "  Selling Deceleration               15%  (is the daily decline " +
      "shrinking day over day)\n" +
      "  ROC5 (5-day rate of change)        12%  (ideal ~-4%; -18% " +
      "reads as a falling knife)\n" +
      "  ATR Expansion                       8%  (today's ATR vs 10 " +
      "days ago; expansion = danger)\n\n" +
      "Missing factors are excluded and remaining weights " +
      "re-normalised.\n\n" +
      "Hard gates (flag, don't hide, the row):\n" +
      "  Price < SMA200 → rejected\n" +
      "  > 10% below SMA50 → rejected\n" +
      "A rejected row still shows its ESS number for review.",
  },
```

- [ ] **Step 5: Render the ESS cell with a gate badge**

In the row-rendering `<td>` list, add a cell following the existing `Score`
cell's pattern:

```tsx
<td className="px-3 py-2 text-right">
  {row.ess_score !== null ? row.ess_score.toFixed(1) : "—"}
  {row.ess_gate_passed === false && (
    <span
      className="ml-1 inline-block rounded bg-amber-100 px-1 text-xs text-amber-800"
      title={`Gated: ${row.ess_gate_reason ?? "unknown"}`}
    >
      ⚠
    </span>
  )}
</td>
```

- [ ] **Step 6: Add the page-level market-context banners**

Near the top of the page's return JSX (above the filter row, rendered once,
not per-row):

```tsx
{marketContext?.nifty_below_sma200 && (
  <p className="mb-2 rounded bg-amber-50 px-3 py-2 text-sm text-amber-900">
    Market regime unfavorable for new longs — Nifty is below its SMA200.
  </p>
)}
{marketContext?.nifty_roc5_extreme && (
  <p className="mb-2 rounded bg-rose-50 px-3 py-2 text-sm text-rose-900">
    Broad market falling fast — Nifty is down{" "}
    {marketContext.nifty_roc5_pct?.toFixed(1)}% over 5 sessions.
  </p>
)}
```

(`marketContext` comes from the existing SWR hook's response —
`data?.market_context`, matching how `data?.stocks` is already consumed.)

- [ ] **Step 7: Bump `colSpan`**

Change the empty-state row's `colSpan={14}` to `colSpan={15}` (one new ESS
column).

- [ ] **Step 8: Manual verification in the browser**

Run: `./run.sh restart frontend` (new route/columns need a fresh Next.js
build; confirm with the user first per the standing "ask before restart" rule
— **do not restart automatically, surface this step and wait**)

Then navigate to Analysis → Watchlist Stocks, confirm:
- ESS column renders with values or "—"
- Hovering the ESS header shows the full formula tooltip
- A gated row (if any ticker currently qualifies) shows the amber warning
  badge with the correct reason on hover
- The ESS filter dropdown works and matches other bucket filters' behavior
- If Nifty is currently below SMA200 or its 5-day ROC is below -6%, the
  corresponding banner renders; otherwise confirm no banner renders (test both
  branches by temporarily hardcoding `marketContext` values in dev tools if
  neither condition is naturally true today)

- [ ] **Step 9: Commit**

```bash
git add frontend/app/\(authenticated\)/analytics/analysis/page.tsx
git commit -m "feat(insights): add ESS column, tooltip, filter, and market banners to Watchlist Stocks"
```

---

## Self-review notes (writing-plans skill)

- **Spec coverage:** §5 hard gates → Task 3 + Task 11; §6 all six factors →
  Tasks 1-9; §7 frontend/tooltip → Tasks 17-18; §8 Iceberg table → Task 12;
  §9 write path/validation → Tasks 14-16. QM Score persistence (added mid-
  conversation, not originally in the spec's factor list but confirmed by the
  user) is covered by Task 15's extraction — **the spec document itself should
  be updated to mention QM Score sub-factor persistence explicitly if it
  wasn't already** (it was captured in §8's schema table, so no spec gap).
- **Known deliberate loose end:** Task 12 Step 2 and Task 14 Step 1 both
  contain explicit "read the real thing before writing" instructions rather
  than invented code, for the two places the research pass didn't reach full
  certainty (the `SortOrder`/`SortField` exact construction, and whether
  `_algo_job_success` is algo-scoped). This is intentional — asserting fake
  certainty there would violate "no placeholders" in a worse way than an
  explicit verification step does.
- **Type consistency:** `EssResult`, `NiftyMarketContext`, `QmResult` field
  names are used identically across Tasks 8/9/10/11/14/15 — verified by re-
  reading the interface contract block against each task's code.
