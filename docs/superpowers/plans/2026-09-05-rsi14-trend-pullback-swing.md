# RSI(14) Trend-Pullback Swing Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new backtest-research strategy template — `rsi14_trend_pullback_swing_v1` — for 3-9 month trend-following swing trades, entering on a shallow RSI(14) pullback within a confirmed uptrend and exiting on trend failure.

**Architecture:** Two new daily-cadence features (`rsi_14_delta_1bar`, `bars_below_sma50`) are added to the existing `compute_daily_features` engine, registered in the shared feature catalog (backend + frontend mirror + warmup table), then wired into a new declarative strategy-template JSON evaluated by the existing AST grammar/evaluator — no new runtime code, no new persistence path.

**Tech Stack:** Python 3.12 / Pydantic 2 (`backend/algo/strategy/ast.py`), pytest, TypeScript (frontend catalog mirror, no build step required for this change).

## Global Constraints

- Line length 79 chars (black/isort/flake8) on all Python.
- No bare `print()` — this change touches no logging paths, N/A.
- Every new %-like feature must set `scale` explicitly; both new features here are raw units (RSI-point delta, integer bar count) so `scale` is omitted (`None`), matching the catalog's documented convention for non-percentage features.
- Feature source values must match the existing catalog's storage-backend convention: features computed in `compute_daily_features` and persisted via the `daily_features_daily_compute` job use `source="intraday_feature_store"` (the physical Iceberg table name, despite computing daily-cadence values) — matches the closest existing analogues `rsi_14`, `golden_cross_bars_ago`, `dist_from_prev_day_high_pct`, all computed by the same function.
- Spec doc: `docs/superpowers/specs/2026-09-05-rsi14-trend-pullback-swing-design.md`.
- Stay on branch `feature/algo-intraday-entry-window` (per explicit user instruction — do not create a new branch for this work).

---

### Task 1: `rsi_14_delta_1bar` feature

**Files:**
- Modify: `backend/algo/features/daily_engine.py:58-67` (docstring), `:116-125` (RSI block)
- Test: Create `backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py`

**Interfaces:**
- Consumes: `compute_daily_features(bars: list[BarData]) -> TickerFeaturePanel` (existing, unchanged signature), `BarData` from `backend.algo.backtest.types`.
- Produces: `compute_daily_features` output panel now includes key `"rsi_14_delta_1bar"` (`Decimal`) on any bar where both `rsi_14[i]` and `rsi_14[i-1]` are non-`None`.

- [ ] **Step 1: Write the failing tests**

Create `backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py`:

```python
"""Tests for the two daily-engine features the
rsi14_trend_pullback_swing_v1 template references:
rsi_14_delta_1bar and bars_below_sma50.

See docs/superpowers/specs/2026-09-05-rsi14-trend-pullback-swing-design.md.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.algo.features.daily_engine import compute_daily_features


def _bars(
    closes: list[float],
    start: date = date(2024, 1, 1),
) -> list[BarData]:
    """Bars built from an explicit close-price list. No weekend
    skipping — daily_engine treats every bar as its own trading
    day regardless of calendar gaps (mirrors the existing
    test_daily_engine_v3_features.py::_bars helper)."""
    bars = []
    for i, price in enumerate(closes):
        c = Decimal(str(price))
        bars.append(BarData(
            ticker="TEST.NS",
            date=start + timedelta(days=i),
            open=c,
            high=c + Decimal("0.5"),
            low=c - Decimal("0.5"),
            close=c,
            volume=10000,
            bar_open_ts_ns=i * 86400 * 10**9,
        ))
    return bars


def _zigzag_closes(n: int, base: float = 100.0) -> list[float]:
    """Alternating +1.5/-1.0 moves — keeps RSI(14) away from the
    0/100 saturation extremes so both rising and falling deltas
    appear across the series."""
    closes = [base]
    for i in range(1, n):
        closes.append(closes[-1] + (1.5 if i % 2 else -1.0))
    return closes


def test_rsi_14_delta_1bar_matches_consecutive_rsi_difference():
    closes = _zigzag_closes(40)
    panel = compute_daily_features(_bars(closes))
    ts = sorted(panel.keys())
    # rsi_14 first appears at bar index 14 (0-indexed; wilder_rsi
    # is None for the first `window` bars). The delta needs one
    # more prior bar, so it first appears at index 15.
    for i in range(15, len(ts)):
        feats = panel[ts[i]]
        prev_feats = panel[ts[i - 1]]
        assert "rsi_14_delta_1bar" in feats
        expected = feats["rsi_14"] - prev_feats["rsi_14"]
        assert feats["rsi_14_delta_1bar"] == expected


def test_rsi_14_delta_1bar_absent_before_warmup():
    closes = _zigzag_closes(10)  # fewer than 15 bars, rsi_14 never warm
    panel = compute_daily_features(_bars(closes))
    for feats in panel.values():
        assert "rsi_14_delta_1bar" not in feats
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py -v`
Expected: 2 tests, both FAIL — `assert "rsi_14_delta_1bar" in feats` raises `AssertionError` (key not present) in the first test; the second test currently passes vacuously (nothing to assert against yet) but re-run it after Step 4 to confirm it still passes.

- [ ] **Step 3: Implement `rsi_14_delta_1bar`**

In `backend/algo/features/daily_engine.py`, update the docstring's momentum bullet (line 62):

```python
        - Momentum: ``rsi_5``, ``rsi_14``, ``roc_5``,
          ``rsi_14_delta_1bar``
```

Then, in the per-bar loop, immediately after the existing RSI block (currently lines 116-125):

```python
        # RSI family.
        rsi_v = rsi_14[i]
        if rsi_v is not None:
            feats["rsi_14"] = rsi_v
        rsi5_v = rsi_5[i]
        if rsi5_v is not None:
            feats["rsi_5"] = rsi5_v
        rsi2_v = rsi_2[i]
        if rsi2_v is not None:
            feats["rsi_2"] = rsi2_v

        # rsi_14_delta_1bar = rsi_14[i] - rsi_14[i-1]. Absent
        # until both this bar and the prior bar have a warm
        # rsi_14 (first appears at i == 14, per wilder_rsi).
        if i > 0 and rsi_v is not None and rsi_14[i - 1] is not None:
            feats["rsi_14_delta_1bar"] = rsi_v - rsi_14[i - 1]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/features/daily_engine.py backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py
git commit -m "$(cat <<'EOF'
feat(algo): add rsi_14_delta_1bar daily feature

1-bar RSI(14) delta for the RSI(14) trend-pullback swing
strategy's reversal condition (rsi_14[t] > rsi_14[t-1]).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 2: `bars_below_sma50` feature

**Files:**
- Modify: `backend/algo/features/daily_engine.py` (docstring `:58-67`; init var near `:96`; SMA block near `:102-106`)
- Test: Modify `backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py` (created in Task 1)

**Interfaces:**
- Consumes: same `compute_daily_features` signature as Task 1; reuses the already-computed `s50 = sma_by_w.get(50)` list in scope.
- Produces: `compute_daily_features` output panel now includes key `"bars_below_sma50"` (`Decimal`, non-negative integer value) on any bar where `sma_50` is warm (i.e. `s50[i] is not None`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py`:

```python
def _bars_flat_then_dip(
    n_flat: int = 60,
    dip_days: int = 2,
    tail: int = 5,
    flat_price: float = 100.0,
    dip_price: float = 90.0,
) -> list[BarData]:
    """n_flat bars at flat_price, then dip_days bars at dip_price,
    then tail bars back at flat_price. With n_flat=60 the SMA50
    warms up well before the dip (first non-None sma_50 is at
    0-indexed bar 49, all still flat_price)."""
    closes = (
        [flat_price] * n_flat
        + [dip_price] * dip_days
        + [flat_price] * tail
    )
    return _bars(closes)


def test_bars_below_sma50_zero_while_close_at_or_above_sma():
    panel = compute_daily_features(_bars_flat_then_dip())
    ts = sorted(panel.keys())
    # Bars 49..59 (0-indexed): sma_50 just warmed, close == 100 ==
    # sma_50 (flat series so far) — not below, streak stays 0.
    for i in range(49, 60):
        assert panel[ts[i]]["bars_below_sma50"] == 0


def test_bars_below_sma50_increments_across_the_dip():
    panel = compute_daily_features(_bars_flat_then_dip())
    ts = sorted(panel.keys())
    # Bar 60: close=90, sma_50=(49*100+90)/50=99.8 -> below, streak=1.
    assert panel[ts[60]]["bars_below_sma50"] == 1
    # Bar 61: close=90, sma_50=(48*100+2*90)/50=99.6 -> below, streak=2.
    assert panel[ts[61]]["bars_below_sma50"] == 2


def test_bars_below_sma50_resets_on_recovery_above_sma():
    panel = compute_daily_features(_bars_flat_then_dip())
    ts = sorted(panel.keys())
    # Bar 62: close=100 back >= sma_50 (99.6) -> streak resets to 0.
    assert panel[ts[62]]["bars_below_sma50"] == 0
    assert panel[ts[63]]["bars_below_sma50"] == 0


def test_bars_below_sma50_absent_before_sma50_warmup():
    panel = compute_daily_features(
        _bars_flat_then_dip(n_flat=10, dip_days=0, tail=0)
    )
    for feats in panel.values():
        assert "bars_below_sma50" not in feats
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py -v -k bars_below_sma50`
Expected: 4 new tests, all FAIL with `KeyError: 'bars_below_sma50'`.

- [ ] **Step 3: Implement `bars_below_sma50`**

In `backend/algo/features/daily_engine.py`, update the docstring's trend bullet (line 60):

```python
        - Trend (SMA): ``sma_20``, ``sma_50``, ``sma_100``,
          ``sma_200``
        - Trend (cross): ``golden_cross_bars_ago``,
          ``bars_below_sma50``
```

Add a running counter next to the existing `last_cross_up_idx` init (currently line 96):

```python
    s50 = sma_by_w.get(50)
    s200 = sma_by_w.get(200)
    last_cross_up_idx: int | None = None
    bars_below_50_streak = 0
```

Then, in the per-bar loop, immediately after the existing SMA family block (currently lines 102-106, before the `distance_from_sma5` block):

```python
        # SMA family.
        for w in sma_windows_t:
            v = sma_by_w[w][i]
            if v is not None:
                feats[f"sma_{w}"] = v

        # bars_below_sma50 — consecutive daily closes below SMA50,
        # resets to 0 the bar close >= SMA50. Absent until SMA50
        # is warm (mirrors golden_cross_bars_ago's counter shape).
        if s50 is not None:
            s50_v = s50[i]
            if s50_v is not None:
                if bar.close < s50_v:
                    bars_below_50_streak += 1
                else:
                    bars_below_50_streak = 0
                feats["bars_below_sma50"] = Decimal(bars_below_50_streak)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py -v`
Expected: all 6 tests in the file PASS (2 from Task 1 + 4 from this task).

- [ ] **Step 5: Run the full daily_engine test suite to check for regressions**

Run: `python -m pytest backend/algo/features/tests/test_daily_engine.py backend/algo/features/tests/test_daily_engine_v3_features.py -v`
Expected: all PASS — confirms the new counter/delta logic didn't disturb any existing feature's emission (both existing "no intraday leakage" and "all spec features present" tests are subset checks, unaffected by new keys).

- [ ] **Step 6: Commit**

```bash
git add backend/algo/features/daily_engine.py backend/algo/features/tests/test_daily_engine_rsi14_swing_features.py
git commit -m "$(cat <<'EOF'
feat(algo): add bars_below_sma50 daily feature

Consecutive-bars-below-SMA50 counter for the RSI(14) trend-
pullback swing strategy's soft-trend-weakness exit condition.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 3: Catalog registration + warmup overrides

**Files:**
- Modify: `backend/algo/strategy/features.py:440-445` (insert `bars_below_sma50`), `:453-458` (insert `rsi_14_delta_1bar`)
- Modify: `backend/algo/strategy/feature_warmup.py:37-100` (`_OVERRIDES` dict)
- Test: Modify `backend/algo/strategy/tests/test_feature_warmup.py`

**Interfaces:**
- Consumes: `Feature` model and `FEATURES: list[Feature]` from `backend.algo.strategy.features` (existing); `_OVERRIDES: dict[str, int]` and `warmup_for_feature(feature: str) -> int` from `backend.algo.strategy.feature_warmup` (existing).
- Produces: `FEATURE_KEYS` (auto-derived `frozenset` at the bottom of `features.py`) now includes `"rsi_14_delta_1bar"` and `"bars_below_sma50"` — this is what `ast.py`'s `FeatureRef` validator checks, so the template in Task 5 can reference them. `warmup_for_feature("rsi_14_delta_1bar") == 15`, `warmup_for_feature("bars_below_sma50") == 50`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/algo/strategy/tests/test_feature_warmup.py` (append near the existing window-suffix test):

```python
def test_warmup_for_rsi14_swing_strategy_overrides():
    assert warmup_for_feature("rsi_14_delta_1bar") == 15
    assert warmup_for_feature("bars_below_sma50") == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/algo/strategy/tests/test_feature_warmup.py::test_warmup_for_rsi14_swing_strategy_overrides -v`
Expected: FAIL — `warmup_for_feature("rsi_14_delta_1bar")` currently falls through the `rsi_` prefix branch, fails `int("14_delta_1bar")`, and returns `DEFAULT_WARMUP_DAYS` (200), not 15. `bars_below_sma50` isn't in `_OVERRIDES` and matches no window prefix, so it also returns 200, not 50.

- [ ] **Step 3: Register both features in the catalog**

In `backend/algo/strategy/features.py`, insert after the existing `golden_cross_bars_ago` entry (currently lines 440-445), before the `# Intraday — momentum` comment:

```python
    Feature(
        key="golden_cross_bars_ago",
        label="Golden cross (bars ago, intraday)",
        type="int",
        source="intraday_feature_store",
    ),
    Feature(
        key="bars_below_sma50",
        label="Bars below SMA50 (consecutive)",
        type="int",
        source="intraday_feature_store",
    ),
    # Intraday — momentum
```

Then insert after the existing `rsi_5` entry (currently lines 453-458), before `roc_5`:

```python
    Feature(
        key="rsi_5",
        label="RSI(5)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="rsi_14_delta_1bar",
        label="RSI(14) 1-bar delta",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="roc_5",
        label="ROC(5)",
        type="float",
        source="intraday_feature_store",
    ),
```

- [ ] **Step 4: Add warmup overrides**

In `backend/algo/strategy/feature_warmup.py`, add to `_OVERRIDES` (near the `golden_cross_bars_ago` / `adx_14` entries, currently lines 54/70):

```python
    "golden_cross_bars_ago": 200,
    "bars_below_sma50": 50,
    "bb_width": 20,
```

and near the RSI-adjacent entries (currently line 70, `"adx_14": 14,`):

```python
    "adx_14": 14,
    "rsi_14_delta_1bar": 15,
```

(These land in the existing `_OVERRIDES` dict; exact surrounding line doesn't matter, dict order is not semantically meaningful — just don't place them inside the `_WINDOW_PREFIXES`-matching zone in a way that suggests they're covered by the regex, since they aren't: `bars_below_sma50` doesn't start with `sma_`/`ema_`/`rsi_`/`roc_`/`atr_`, and `rsi_14_delta_1bar` starts with `rsi_` but its suffix `14_delta_1bar` isn't a bare integer so the regex fallback would silently mis-resolve to `DEFAULT_WARMUP_DAYS` without this explicit override.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest backend/algo/strategy/tests/test_feature_warmup.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/strategy/features.py backend/algo/strategy/feature_warmup.py backend/algo/strategy/tests/test_feature_warmup.py
git commit -m "$(cat <<'EOF'
feat(algo): register rsi_14_delta_1bar + bars_below_sma50 in strategy catalog

Adds both new daily-engine features to FEATURES (so the AST
validator accepts them) and to feature_warmup's _OVERRIDES
(15 and 50 prior bars respectively) — the rsi_ prefix regex
fallback would otherwise silently mis-resolve rsi_14_delta_1bar
to the 200-day default.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 4: Frontend catalog mirror

**Files:**
- Modify: `frontend/components/algo-trading/strategyFeatureCatalog.ts:119-122`
- Test: `backend/algo/tests/test_feature_registry_sync.py` (existing, no changes needed — it's the drift gate)

**Interfaces:**
- Consumes: nothing new — this is a static-data mirror keyed by the same `key` strings as Task 3's `FEATURES` entries.
- Produces: `STRATEGY_FEATURES` array now includes both keys, closing the gap `test_feature_registry_sync.py` checks.

- [ ] **Step 1: Confirm the drift test currently fails**

Run: `python -m pytest backend/algo/tests/test_feature_registry_sync.py -v`
Expected: FAIL — `backend extra: {'rsi_14_delta_1bar', 'bars_below_sma50'}` (Task 3 added them to the backend catalog; the frontend mirror doesn't have them yet).

- [ ] **Step 2: Update the frontend mirror**

In `frontend/components/algo-trading/strategyFeatureCatalog.ts`, replace lines 119-122:

```typescript
  { key: "golden_cross_bars_ago", label: "Golden cross (bars ago, intraday)", type: "int", source: "intraday_feature_store" },
  // Intraday – momentum
  { key: "rsi_14", label: "RSI(14)", type: "float", source: "intraday_feature_store" },
  { key: "rsi_5", label: "RSI(5)", type: "float", source: "intraday_feature_store" },
```

with:

```typescript
  { key: "golden_cross_bars_ago", label: "Golden cross (bars ago, intraday)", type: "int", source: "intraday_feature_store" },
  { key: "bars_below_sma50", label: "Bars below SMA50 (consecutive)", type: "int", source: "intraday_feature_store" },
  // Intraday – momentum
  { key: "rsi_14", label: "RSI(14)", type: "float", source: "intraday_feature_store" },
  { key: "rsi_5", label: "RSI(5)", type: "float", source: "intraday_feature_store" },
  { key: "rsi_14_delta_1bar", label: "RSI(14) 1-bar delta", type: "float", source: "intraday_feature_store" },
```

- [ ] **Step 3: Run the drift test to verify it passes**

Run: `python -m pytest backend/algo/tests/test_feature_registry_sync.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/algo-trading/strategyFeatureCatalog.ts
git commit -m "$(cat <<'EOF'
feat(algo): mirror rsi_14_delta_1bar + bars_below_sma50 to frontend catalog

Keeps strategyFeatureCatalog.ts in sync with the backend
FEATURES registry per test_feature_registry_sync.py's drift gate.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 5: Strategy template + template test suite

**Files:**
- Create: `backend/algo/strategy/templates/rsi14_trend_pullback_swing_v1.json`
- Test: Create `backend/algo/strategy/tests/test_template_rsi14_trend_pullback_swing_v1.py`

**Interfaces:**
- Consumes: `parse_strategy(payload: dict) -> Strategy` and `Strategy` from `backend.algo.strategy.ast` (existing); `Evaluator` and `EvalContext` from `backend.algo.backtest.evaluator` (existing) — `Evaluator().eval_node(node: dict, ctx: EvalContext)` returns `bool` for condition nodes and `dict(node)` verbatim for action nodes (`buy`/`sell`/`exit`/`hold`/`set_target_weight`).
- Produces: a loadable, AST-valid template file at the path above; no other task depends on this one.

- [ ] **Step 1: Write the failing tests**

Create `backend/algo/strategy/tests/test_template_rsi14_trend_pullback_swing_v1.py`:

```python
"""Sanity tests for rsi14_trend_pullback_swing_v1.json."""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.types import BarData
from backend.algo.features.daily_engine import compute_daily_features
from backend.algo.strategy.ast import parse_strategy

_TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "templates"
    / "rsi14_trend_pullback_swing_v1.json"
)


@pytest.fixture
def template_dict() -> dict:
    return json.loads(_TEMPLATE_PATH.read_text())


def test_template_parses_cleanly(template_dict):
    s = parse_strategy(template_dict)
    assert s.product == "CNC"
    assert s.schedule.interval == "1d"
    assert s.universe.scope == "discovery"
    assert s.universe.filter.market == "india"
    assert s.universe.filter.ticker_type == ["stock"]
    assert s.rebalance.max_positions == 5


def test_template_entry_thresholds_match_spec(template_dict):
    """Entry: distance_from_sma200>0, sma_50>sma_200,
    sma200_slope>0, rsi_14<50, rsi_14_delta_1bar>0,
    dist_from_prev_day_high_pct>0."""
    entry = template_dict["root"]["cond"]["operands"]
    thresholds = {op["left"]["feature"]: op for op in entry}

    assert thresholds["distance_from_sma200"]["op"] == ">"
    assert thresholds["distance_from_sma200"]["right"]["literal"] == 0.0

    assert thresholds["sma_50"]["op"] == ">"
    assert thresholds["sma_50"]["right"]["feature"] == "sma_200"

    assert thresholds["sma200_slope"]["op"] == ">"
    assert thresholds["sma200_slope"]["right"]["literal"] == 0.0

    assert thresholds["rsi_14"]["op"] == "<"
    assert thresholds["rsi_14"]["right"]["literal"] == 50

    assert thresholds["rsi_14_delta_1bar"]["op"] == ">"
    assert thresholds["rsi_14_delta_1bar"]["right"]["literal"] == 0.0

    assert thresholds["dist_from_prev_day_high_pct"]["op"] == ">"
    assert thresholds["dist_from_prev_day_high_pct"]["right"]["literal"] == 0.0


def test_template_exit_branch_structure(template_dict):
    """Exit: distance_from_sma200<0 OR
    (bars_below_sma50>=2 AND rsi_14<45)."""
    exit_branch = template_dict["root"]["else"]
    assert exit_branch["type"] == "if"
    cond = exit_branch["cond"]
    assert cond["type"] == "or"

    trend_fail, soft_weak = cond["operands"]
    assert trend_fail["left"]["feature"] == "distance_from_sma200"
    assert trend_fail["op"] == "<"
    assert trend_fail["right"]["literal"] == 0.0

    assert soft_weak["type"] == "and"
    bars_op, rsi_op = soft_weak["operands"]
    assert bars_op["left"]["feature"] == "bars_below_sma50"
    assert bars_op["op"] == ">="
    assert bars_op["right"]["literal"] == 2
    assert rsi_op["left"]["feature"] == "rsi_14"
    assert rsi_op["op"] == "<"
    assert rsi_op["right"]["literal"] == 45

    assert exit_branch["then"]["type"] == "exit"
    assert exit_branch["then"]["scope"] == "this_symbol"
    assert exit_branch["else"]["type"] == "hold"


def test_template_risk_caps(template_dict):
    s = parse_strategy(template_dict)
    assert s.risk.per_trade.stop_loss_pct == 8.0
    assert s.risk.portfolio.max_exposure_pct == 100.0
    assert s.risk.portfolio.max_concentration_pct == 25.0
    assert s.risk.daily.max_loss_pct == 5.0
    assert s.risk.daily.max_open_positions == 5


def test_template_uses_only_expected_features(template_dict):
    expected = {
        "distance_from_sma200",
        "sma_50",
        "sma_200",
        "sma200_slope",
        "rsi_14",
        "rsi_14_delta_1bar",
        "dist_from_prev_day_high_pct",
        "bars_below_sma50",
    }
    used: set[str] = set()

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "feature" and isinstance(v, str):
                    used.add(v)
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(template_dict["root"])
    extra = used - expected
    assert not extra, f"AST references unexpected features: {extra}"
    missing = expected - used
    assert not missing, f"AST missing expected features: {missing}"


def _ctx(**features: float) -> EvalContext:
    return EvalContext(
        ticker="TEST.NS",
        bar_date=date(2026, 9, 5),
        features={k: Decimal(str(v)) for k, v in features.items()},
        open_qty=0,
    )


def test_evaluator_enters_when_all_entry_conditions_true(template_dict):
    ctx = _ctx(
        distance_from_sma200=0.05,
        sma_50=110,
        sma_200=100,
        sma200_slope=0.01,
        rsi_14=45,
        rsi_14_delta_1bar=2,
        dist_from_prev_day_high_pct=0.5,
        bars_below_sma50=0,
    )
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "set_target_weight", "weight": 0.2}


def test_evaluator_holds_when_pullback_too_deep(template_dict):
    """rsi_14 >= 50 fails the pullback condition, and no exit
    condition is true either (position still in a healthy
    uptrend) -> hold."""
    ctx = _ctx(
        distance_from_sma200=0.05,
        sma_50=110,
        sma_200=100,
        sma200_slope=0.01,
        rsi_14=55,
        rsi_14_delta_1bar=2,
        dist_from_prev_day_high_pct=0.5,
        bars_below_sma50=0,
    )
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "hold"}


def test_evaluator_exits_on_major_trend_failure(template_dict):
    ctx = _ctx(
        distance_from_sma200=-0.02,
        sma_50=95,
        sma_200=100,
        sma200_slope=-0.01,
        rsi_14=60,
        rsi_14_delta_1bar=-1,
        dist_from_prev_day_high_pct=-0.5,
        bars_below_sma50=0,
    )
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "exit", "scope": "this_symbol"}


def test_evaluator_exits_on_soft_trend_weakness(template_dict):
    ctx = _ctx(
        distance_from_sma200=0.02,  # still above SMA200 -> not major failure
        sma_50=101,
        sma_200=100,
        sma200_slope=0.005,
        rsi_14=40,  # < 45
        rsi_14_delta_1bar=-1,
        dist_from_prev_day_high_pct=-0.2,
        bars_below_sma50=3,  # >= 2
    )
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "exit", "scope": "this_symbol"}


def test_end_to_end_wires_real_computed_features_without_missing_feature_error(
    template_dict,
):
    """Wires an actual compute_daily_features panel into the
    evaluator for a well-warmed synthetic uptrend series, merged
    with distance_from_sma200/sma200_slope computed the same way
    backend/algo/factors/trend.py computes them (a separate
    factor pipeline, NOT part of compute_daily_features — a real
    runtime merges both sources into one EvalContext.features
    dict before evaluation; this test replicates that merge
    rather than assuming one function emits every feature).
    Guards the class of bug where a template references a
    feature key no runtime ever actually populates (KeyError:
    Feature not in context)."""
    bars = []
    price = 100.0
    for i in range(260):
        close_p = price * 1.003
        bars.append(BarData(
            ticker="TEST.NS",
            date=date(2024, 1, 1) + timedelta(days=i),
            open=Decimal(str(round(price, 4))),
            high=Decimal(str(round(max(price, close_p) * 1.005, 4))),
            low=Decimal(str(round(min(price, close_p) * 0.995, 4))),
            close=Decimal(str(round(close_p, 4))),
            volume=10000,
            bar_open_ts_ns=i * 86400 * 10**9,
        ))
        price = close_p

    panel = compute_daily_features(bars)
    ts = sorted(panel.keys())
    last_ts = ts[-1]
    feats = dict(panel[last_ts])

    # distance_from_sma200 / sma200_slope come from the separate
    # factor pipeline (backend/algo/factors/trend.py: dist =
    # (close-sma200)/sma200, slope = (sma200[t]-sma200[t-21])/
    # sma200[t-21]), not from compute_daily_features. Derive them
    # here from the panel's own sma_200 series (sma_200 IS an
    # existing compute_daily_features output) to mirror the real
    # merge without touching daily_engine.py.
    sma200_last = panel[last_ts]["sma_200"]
    sma200_21_ago = panel[ts[-1 - 21]]["sma_200"]
    close_last = bars[-1].close
    feats["distance_from_sma200"] = (
        (close_last - sma200_last) / sma200_last
    )
    feats["sma200_slope"] = (
        (sma200_last - sma200_21_ago) / sma200_21_ago
    )

    ctx = EvalContext(
        ticker="TEST.NS",
        bar_date=date(2024, 1, 1) + timedelta(days=259),
        features=feats,
        open_qty=0,
    )
    # No KeyError -> every feature the template references is
    # actually present once both real feature sources are
    # merged. The specific action doesn't matter here (a
    # monotonic uptrend never dips RSI(14) below 50, so this
    # lands on "hold", not an entry) — what matters is that
    # evaluation completes cleanly.
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result["type"] in {"set_target_weight", "exit", "hold"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/algo/strategy/tests/test_template_rsi14_trend_pullback_swing_v1.py -v`
Expected: FAIL on `test_template_parses_cleanly` (and every subsequent test) with `FileNotFoundError` / fixture error — the template file doesn't exist yet.

- [ ] **Step 3: Create the template file**

Create `backend/algo/strategy/templates/rsi14_trend_pullback_swing_v1.json`:

```json
{
  "id": "00000000-0000-0000-0000-000000000052",
  "name": "RSI(14) Trend Pullback Swing v1 — Long-only 3-9mo swing",
  "universe": {
    "type": "scope",
    "scope": "discovery",
    "filter": {
      "ticker_type": ["stock"],
      "market": "india"
    }
  },
  "schedule": {
    "type": "bar_close",
    "interval": "1d",
    "time": "15:25 IST"
  },
  "rebalance": {
    "type": "daily",
    "max_positions": 5
  },
  "product": "CNC",
  "root": {
    "type": "if",
    "cond": {
      "type": "and",
      "operands": [
        {
          "type": "compare",
          "left": {"feature": "distance_from_sma200"},
          "op": ">",
          "right": {"literal": 0.0}
        },
        {
          "type": "compare",
          "left": {"feature": "sma_50"},
          "op": ">",
          "right": {"feature": "sma_200"}
        },
        {
          "type": "compare",
          "left": {"feature": "sma200_slope"},
          "op": ">",
          "right": {"literal": 0.0}
        },
        {
          "type": "compare",
          "left": {"feature": "rsi_14"},
          "op": "<",
          "right": {"literal": 50}
        },
        {
          "type": "compare",
          "left": {"feature": "rsi_14_delta_1bar"},
          "op": ">",
          "right": {"literal": 0.0}
        },
        {
          "type": "compare",
          "left": {"feature": "dist_from_prev_day_high_pct"},
          "op": ">",
          "right": {"literal": 0.0}
        }
      ]
    },
    "then": {"type": "set_target_weight", "weight": 0.20},
    "else": {
      "type": "if",
      "cond": {
        "type": "or",
        "operands": [
          {
            "type": "compare",
            "left": {"feature": "distance_from_sma200"},
            "op": "<",
            "right": {"literal": 0.0}
          },
          {
            "type": "and",
            "operands": [
              {
                "type": "compare",
                "left": {"feature": "bars_below_sma50"},
                "op": ">=",
                "right": {"literal": 2}
              },
              {
                "type": "compare",
                "left": {"feature": "rsi_14"},
                "op": "<",
                "right": {"literal": 45}
              }
            ]
          }
        ]
      },
      "then": {"type": "exit", "scope": "this_symbol"},
      "else": {"type": "hold"}
    }
  },
  "risk": {
    "per_trade": {"stop_loss_pct": 8.0, "max_qty": 10000},
    "portfolio": {
      "max_exposure_pct": 100.0,
      "max_concentration_pct": 25.0
    },
    "daily": {"max_loss_pct": 5.0, "max_open_positions": 5}
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/algo/strategy/tests/test_template_rsi14_trend_pullback_swing_v1.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Run the full algo strategy + features test suites to check for regressions**

Run: `python -m pytest backend/algo/strategy/ backend/algo/features/ backend/algo/tests/test_feature_registry_sync.py -v`
Expected: all PASS — no other template, loader, or registry test broken by the new file/catalog entries.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/strategy/templates/rsi14_trend_pullback_swing_v1.json backend/algo/strategy/tests/test_template_rsi14_trend_pullback_swing_v1.py
git commit -m "$(cat <<'EOF'
feat(algo): add rsi14_trend_pullback_swing_v1 strategy template

3-9mo trend-following swing: enters on a shallow RSI(14)
pullback (<50, turning up) within a confirmed SMA50>SMA200
uptrend with rising SMA200 slope, confirmed by a close above
the prior day's high. Exits on SMA200 trend failure or 2+
consecutive closes below SMA50 combined with RSI14<45.

Backtest-research candidate, paper-ready risk fields (8% stop,
100% max exposure, 25% max concentration, 5 max positions).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Post-plan: not in scope

- No backtest job run against real market data (Iceberg OHLCV) — that's a manual next step for the user once this lands (`PYTHONPATH=.:backend python -m backend.pipeline.runner ...` / the Algo Trading UI's backtest picker), not part of this implementation plan.
- No paper/live promotion — per the design spec, this stays a backtest-research template until a walk-forward run with DSR ≥ 0.95 justifies promotion.
- No backend restart required — this change touches no Pydantic route fields, no new routers, no scheduled jobs; it's pure catalog/template data plus a pure-function feature addition picked up by the next `daily_features_daily_compute` scheduled run.
