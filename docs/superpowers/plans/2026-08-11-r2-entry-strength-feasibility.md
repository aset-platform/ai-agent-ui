# R2 Entry-Strength Feasibility Analysis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a re-runnable CLI that measures, on the real labeled dataset, whether the 4 entry-strength dimensions separate winners from losers — producing an honest go/no-go report that gates whether R2's composite gate is worth building.

**Architecture:** A new research module `backend/algo/research/entry_strength_feasibility/` reads the self-contained PG table `algo.entry_labeled_outcomes` (no joins), computes per-feature rank-based separation (AUC / median-split win-rate / Mann-Whitney p) grouped by the 4 dimensions, plus one pre-registered composite headline, and renders a markdown report with hard tiny-n caveats. Pure stat functions are unit-tested; the DB read and CLI are thin.

**Tech Stack:** Python 3.12, pandas, `sklearn.metrics.roc_auc_score`, `scipy.stats.mannwhitneyu` (both already in the stack via the XGBoost/FinBERT pipeline), SQLAlchemy async (`disposable_pg_session`).

**Spec:** `docs/superpowers/specs/2026-08-11-r2-entry-strength-feasibility-design.md`

## Global Constraints

- Line length ≤ 79 chars (black/isort/flake8). PEP 604 (`X | None`). No bare `print()` — module `_logger = logging.getLogger(__name__)`. No bare `except:`.
- Analysis is **read-only** and **real-trades-only** — no simulation, no thresholds derived, no gate code. Pure analysis → report.
- Cohort predicate (verbatim): `mode==target AND filled AND outcome_settled AND label_win is not None AND not dry_run`.
- Every report MUST carry the caveat banner: exploratory, in-sample, NOT out-of-sample, multiple comparisons uncorrected, n small.
- Output dir: `~/.ai-agent-ui/research_runs/<date>-entry-strength-feasibility/` (use `backend.paths` home helper; do NOT hardcode `~`).
- Tests run: `docker compose exec -T backend python -m pytest <path> -q`.

---

### Task 1: Separation statistics (`stats.py`)

**Files:**
- Create: `backend/algo/research/entry_strength_feasibility/__init__.py` (empty)
- Create: `backend/algo/research/entry_strength_feasibility/stats.py`
- Test: `backend/algo/research/entry_strength_feasibility/tests/__init__.py` (empty), `.../tests/test_stats.py`

**Interfaces:**
- Produces:
  - `separation_auc(feature: list[float | None], label_win: list[bool]) -> float` — rank AUC (P(winner value > loser value)); 0.5 if <2 usable pairs or single class; NaN/None feature rows dropped pairwise.
  - `median_split_win_rate(feature: list[float | None], label_win: list[bool]) -> tuple[float | None, float | None]` — (top-half win-rate, bottom-half win-rate) by median split (`> median` = top, `<= median` = bottom); `(None, None)` if a half is empty.
  - `mannwhitney_p(feature: list[float | None], label_win: list[bool]) -> float | None` — two-sided MWU p; None if a class empty or all values identical.

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/research/entry_strength_feasibility/tests/test_stats.py
from backend.algo.research.entry_strength_feasibility.stats import (
    mannwhitney_p,
    median_split_win_rate,
    separation_auc,
)

_F = [1.0, 2.0, 3.0, 4.0]
_PERFECT = [False, False, True, True]   # winners have higher values
_REVERSED = [True, True, False, False]
_NOSEP = [True, False, True, False]


def test_auc_perfect_reversed_and_none():
    assert separation_auc(_F, _PERFECT) == 1.0
    assert separation_auc(_F, _REVERSED) == 0.0
    assert separation_auc(_F, _NOSEP) == 0.5
    # single class -> undefined -> 0.5
    assert separation_auc(_F, [True, True, True, True]) == 0.5


def test_auc_drops_nan_pairs():
    f = [1.0, None, 3.0, 4.0]
    # None row dropped; remaining [1,3,4] vs [F,T,T] still perfectly separates
    assert separation_auc(f, _PERFECT) == 1.0


def test_median_split_win_rate():
    top, bottom = median_split_win_rate(_F, _PERFECT)
    assert top == 1.0 and bottom == 0.0


def test_mannwhitney_p_range_and_empty_class():
    p = mannwhitney_p(_F, _PERFECT)
    assert p is not None and 0.0 <= p <= 1.0
    assert mannwhitney_p(_F, [True, True, True, True]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_stats.py -q`
Expected: FAIL — `ModuleNotFoundError` / cannot import `stats`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/algo/research/entry_strength_feasibility/stats.py
"""Rank-based winner/loser separation statistics (real-trades feasibility)."""
from __future__ import annotations

import math
import statistics

from scipy.stats import mannwhitneyu
from sklearn.metrics import roc_auc_score


def _clean(
    feature: list[float | None], label_win: list[bool]
) -> tuple[list[float], list[int]]:
    xs: list[float] = []
    ys: list[int] = []
    for f, lbl in zip(feature, label_win):
        if f is None or (isinstance(f, float) and math.isnan(f)):
            continue
        xs.append(float(f))
        ys.append(1 if lbl else 0)
    return xs, ys


def separation_auc(
    feature: list[float | None], label_win: list[bool]
) -> float:
    xs, ys = _clean(feature, label_win)
    if len(xs) < 2 or len(set(ys)) < 2:
        return 0.5
    return float(roc_auc_score(ys, xs))


def median_split_win_rate(
    feature: list[float | None], label_win: list[bool]
) -> tuple[float | None, float | None]:
    xs, ys = _clean(feature, label_win)
    if len(xs) < 2:
        return (None, None)
    med = statistics.median(xs)
    top = [y for x, y in zip(xs, ys) if x > med]
    bottom = [y for x, y in zip(xs, ys) if x <= med]
    if not top or not bottom:
        return (None, None)
    return (sum(top) / len(top), sum(bottom) / len(bottom))


def mannwhitney_p(
    feature: list[float | None], label_win: list[bool]
) -> float | None:
    xs, ys = _clean(feature, label_win)
    winners = [x for x, y in zip(xs, ys) if y == 1]
    losers = [x for x, y in zip(xs, ys) if y == 0]
    if not winners or not losers:
        return None
    try:
        return float(
            mannwhitneyu(
                winners, losers, alternative="two-sided"
            ).pvalue
        )
    except ValueError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_stats.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/entry_strength_feasibility/__init__.py \
  backend/algo/research/entry_strength_feasibility/stats.py \
  backend/algo/research/entry_strength_feasibility/tests/__init__.py \
  backend/algo/research/entry_strength_feasibility/tests/test_stats.py
git commit -m "feat(algo): entry-strength feasibility separation stats (R2)"
```

---

### Task 2: Dimension + composite registry (`dimensions.py`)

**Files:**
- Create: `backend/algo/research/entry_strength_feasibility/dimensions.py`
- Test: `.../tests/test_dimensions.py`

**Interfaces:**
- Produces:
  - `DIMENSIONS: dict[str, list[str]]` — dimension name → feature columns.
  - `COMPOSITE: list[tuple[str, float]]` — pre-registered (feature, sign).
  - `KNOWN_COLUMNS: frozenset[str]` — every column the analysis may reference (incl. derived `breadth_oversold_pct`). Guards against typos.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dimensions.py
from backend.algo.research.entry_strength_feasibility.dimensions import (
    COMPOSITE,
    DIMENSIONS,
    KNOWN_COLUMNS,
)


def test_every_referenced_feature_is_known():
    referenced = {f for feats in DIMENSIONS.values() for f in feats}
    referenced |= {f for f, _ in COMPOSITE}
    missing = referenced - KNOWN_COLUMNS
    assert missing == set(), f"unknown feature columns: {missing}"


def test_composite_covers_four_dimensions_with_signs():
    assert len(COMPOSITE) == 4
    assert all(sign in (1.0, -1.0) for _, sign in COMPOSITE)
    assert len(DIMENSIONS) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_dimensions.py -q`
Expected: FAIL — cannot import `dimensions`.

- [ ] **Step 3: Write minimal implementation**

```python
# dimensions.py
"""Pre-registered feature→dimension map + composite (fixed a-priori)."""
from __future__ import annotations

# Every raw column of algo.entry_labeled_outcomes the analysis may read,
# plus the derived breadth ratio. Keep in sync with the table (guarded by
# test_dimensions + the cohort loader).
KNOWN_COLUMNS: frozenset[str] = frozenset({
    "rsi2_at_entry", "dist_sma50_pct", "dist_sma200_pct",
    "ret_1d_prior", "ret_3d_prior", "gap_pct",
    "ess_absorption_volume_score", "ess_selling_deceleration_score",
    "ess_trend_stability_score", "qm_score", "ess_score",
    "qm_mdd_pctile", "qm_rs_pctile", "qm_sharpe_pctile",
    "breadth_oversold_pct",
})

DIMENSIONS: dict[str, list[str]] = {
    "pullback_absorption": [
        "rsi2_at_entry", "dist_sma50_pct", "dist_sma200_pct",
        "ret_1d_prior", "ret_3d_prior",
        "ess_absorption_volume_score", "ess_selling_deceleration_score",
        "ess_trend_stability_score",
    ],
    "strength_resilience": [
        "qm_score", "ess_score", "qm_mdd_pctile", "qm_rs_pctile",
        "qm_sharpe_pctile",
    ],
    "breadth_regime": ["breadth_oversold_pct"],
    "falling_knife": ["ret_3d_prior", "gap_pct"],
}

# Pre-registered composite — hypothesized directions from the R1 thesis +
# the July RSI2 post-mortem. Fixed BEFORE looking at results (a wrong
# direction shows as component/composite AUC < 0.5). See spec §4B.
COMPOSITE: list[tuple[str, float]] = [
    ("ess_absorption_volume_score", 1.0),   # more absorption = better
    ("qm_mdd_pctile", -1.0),                 # less-severe drawdown = better
    ("breadth_oversold_pct", -1.0),          # fewer oversold = idiosyncratic
    ("ret_3d_prior", 1.0),                   # less-negative 3d = not a knife
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_dimensions.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/entry_strength_feasibility/dimensions.py \
  backend/algo/research/entry_strength_feasibility/tests/test_dimensions.py
git commit -m "feat(algo): entry-strength dimension + composite registry (R2)"
```

---

### Task 3: Cohort loader + frame (`cohort.py`)

**Files:**
- Create: `backend/algo/research/entry_strength_feasibility/cohort.py`
- Test: `.../tests/test_cohort.py`

**Interfaces:**
- Consumes: `KNOWN_COLUMNS` (Task 2) is NOT needed here.
- Produces:
  - `filter_cohort(rows: list[dict], mode: str) -> list[dict]` — pure predicate filter (`filled AND outcome_settled AND label_win is not None AND not dry_run AND (mode=='all' or row mode matches)`).
  - `to_frame(rows: list[dict]) -> pd.DataFrame` — builds a DataFrame, coerces numeric feature columns to float, derives `breadth_oversold_pct = breadth_oversold / breadth_total` (0 when `breadth_total` in (0, None/NaN)), casts `label_win` to bool.
  - `load_labeled_rows(mode: str) -> list[dict]` — async-wrapped PG SELECT via `disposable_pg_session`; returns raw dict rows. (Thin; smoke-tested in Task 6, not unit-tested.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cohort.py
import math

from backend.algo.research.entry_strength_feasibility.cohort import (
    filter_cohort,
    to_frame,
)

_ROWS = [
    {"mode": "live", "filled": True, "outcome_settled": True,
     "label_win": True, "dry_run": False, "breadth_oversold": 5,
     "breadth_total": 100, "rsi2_at_entry": 3.0},
    {"mode": "live", "filled": True, "outcome_settled": False,   # unsettled
     "label_win": None, "dry_run": False, "breadth_oversold": 1,
     "breadth_total": 100, "rsi2_at_entry": 4.0},
    {"mode": "live", "filled": False, "outcome_settled": True,   # rejected
     "label_win": False, "dry_run": False, "breadth_oversold": 2,
     "breadth_total": 100, "rsi2_at_entry": 4.0},
    {"mode": "live", "filled": True, "outcome_settled": True,    # dry_run
     "label_win": True, "dry_run": True, "breadth_oversold": 2,
     "breadth_total": 100, "rsi2_at_entry": 4.0},
    {"mode": "paper", "filled": True, "outcome_settled": True,   # wrong mode
     "label_win": False, "dry_run": False, "breadth_oversold": 2,
     "breadth_total": 100, "rsi2_at_entry": 4.0},
]


def test_filter_cohort_live_keeps_only_settled_filled_labeled():
    kept = filter_cohort(_ROWS, "live")
    assert len(kept) == 1
    assert kept[0]["rsi2_at_entry"] == 3.0


def test_to_frame_derives_breadth_pct():
    df = to_frame(filter_cohort(_ROWS, "live"))
    assert math.isclose(df["breadth_oversold_pct"].iloc[0], 0.05)
    assert df["label_win"].iloc[0] is True or bool(df["label_win"].iloc[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_cohort.py -q`
Expected: FAIL — cannot import `cohort`.

- [ ] **Step 3: Write minimal implementation**

```python
# cohort.py
"""Load + filter the labeled cohort from algo.entry_labeled_outcomes."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd
from sqlalchemy import text

_logger = logging.getLogger(__name__)

_NUMERIC = (
    "rsi2_at_entry", "dist_sma50_pct", "dist_sma200_pct", "ret_1d_prior",
    "ret_3d_prior", "gap_pct", "ess_absorption_volume_score",
    "ess_selling_deceleration_score", "ess_trend_stability_score",
    "qm_score", "ess_score", "qm_mdd_pctile", "qm_rs_pctile",
    "qm_sharpe_pctile", "return_pct", "mfe_pct", "mae_pct",
)


def filter_cohort(rows: list[dict], mode: str) -> list[dict]:
    out = []
    for r in rows:
        if not r.get("filled"):
            continue
        if not r.get("outcome_settled"):
            continue
        if r.get("label_win") is None:
            continue
        if r.get("dry_run"):
            continue
        if mode != "all" and r.get("mode") != mode:
            continue
        out.append(r)
    return out


def to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in _NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    bt = pd.to_numeric(df.get("breadth_total"), errors="coerce")
    bo = pd.to_numeric(df.get("breadth_oversold"), errors="coerce")
    df["breadth_oversold_pct"] = (bo / bt).where(bt > 0, other=0.0)
    df["label_win"] = df["label_win"].astype(bool)
    return df


def load_labeled_rows(mode: str) -> list[dict]:
    """Sync entrypoint — async PG read via disposable_pg_session."""
    return asyncio.run(_load(mode))


async def _load(mode: str) -> list[dict]:
    from backend.db.engine import disposable_pg_session

    where_mode = "" if mode == "all" else "AND mode = :mode"
    sql = text(
        "SELECT * FROM algo.entry_labeled_outcomes "
        "WHERE filled AND outcome_settled AND label_win IS NOT NULL "
        f"AND NOT dry_run {where_mode}"
    )
    params: dict[str, Any] = {} if mode == "all" else {"mode": mode}
    async with disposable_pg_session() as s:
        res = await s.execute(sql, params)
        return [dict(m) for m in res.mappings().all()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_cohort.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/entry_strength_feasibility/cohort.py \
  backend/algo/research/entry_strength_feasibility/tests/test_cohort.py
git commit -m "feat(algo): entry-strength cohort loader + frame (R2)"
```

---

### Task 4: Analysis orchestration (`analysis.py`)

**Files:**
- Create: `backend/algo/research/entry_strength_feasibility/analysis.py`
- Test: `.../tests/test_analysis.py`

**Interfaces:**
- Consumes: `stats.*` (Task 1), `DIMENSIONS`/`COMPOSITE` (Task 2), `to_frame` (Task 3).
- Produces:
  - `FeatureStat` dataclass: `dimension, feature, n, auc, separation (|auc-0.5|), win_rate_top, win_rate_bottom, mwu_p, direction ("higher"|"lower")`.
  - `CompositeStat` dataclass: `n, auc, win_rate_top, win_rate_bottom`.
  - `FeasibilityResult` dataclass: `mode, n_total, n_win, n_loss, first_date, last_date, min_n_ok, feature_stats (sorted by separation desc), composite`.
  - `composite_score(df: pd.DataFrame) -> pd.Series` — sign-aware z-score sum over `COMPOSITE` (std==0 or NaN → that component contributes 0).
  - `analyze(df: pd.DataFrame, min_n: int) -> FeasibilityResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis.py
import pandas as pd

from backend.algo.research.entry_strength_feasibility.analysis import (
    analyze,
    composite_score,
)


def _df():
    # 4 rows, ess_absorption perfectly separates (winners higher)
    return pd.DataFrame({
        "label_win": [False, False, True, True],
        "ess_absorption_volume_score": [1.0, 2.0, 3.0, 4.0],
        "qm_mdd_pctile": [0.9, 0.8, 0.2, 0.1],
        "breadth_oversold_pct": [0.1, 0.1, 0.1, 0.1],
        "ret_3d_prior": [-1.0, -2.0, 0.5, 0.4],
        # remaining feature cols present but flat
        "rsi2_at_entry": [3.0, 3.0, 3.0, 3.0],
        "dist_sma50_pct": [0.0] * 4, "dist_sma200_pct": [0.0] * 4,
        "ret_1d_prior": [0.0] * 4, "gap_pct": [0.0] * 4,
        "ess_selling_deceleration_score": [0.0] * 4,
        "ess_trend_stability_score": [0.0] * 4, "qm_score": [0.0] * 4,
        "ess_score": [0.0] * 4, "qm_rs_pctile": [0.0] * 4,
        "qm_sharpe_pctile": [0.0] * 4,
    })


def test_composite_separates_when_components_aligned():
    comp = composite_score(_df())
    # winners (idx 2,3) should score higher than losers (0,1)
    assert comp.iloc[2] > comp.iloc[0]
    assert comp.iloc[3] > comp.iloc[1]


def test_analyze_ranks_absorption_top_and_flags_min_n():
    res = analyze(_df(), min_n=30)
    assert res.n_total == 4 and res.n_win == 2 and res.n_loss == 2
    assert res.min_n_ok is False           # 4 < 30
    top = res.feature_stats[0]
    assert top.feature == "ess_absorption_volume_score"
    assert top.auc == 1.0 and top.direction == "higher"
    assert res.composite.auc >= 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_analysis.py -q`
Expected: FAIL — cannot import `analysis`.

- [ ] **Step 3: Write minimal implementation**

```python
# analysis.py
"""Orchestrate per-feature + composite winner/loser separation."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backend.algo.research.entry_strength_feasibility.dimensions import (
    COMPOSITE,
    DIMENSIONS,
)
from backend.algo.research.entry_strength_feasibility.stats import (
    mannwhitney_p,
    median_split_win_rate,
    separation_auc,
)


@dataclass(frozen=True)
class FeatureStat:
    dimension: str
    feature: str
    n: int
    auc: float
    separation: float
    win_rate_top: float | None
    win_rate_bottom: float | None
    mwu_p: float | None
    direction: str


@dataclass(frozen=True)
class CompositeStat:
    n: int
    auc: float
    win_rate_top: float | None
    win_rate_bottom: float | None


@dataclass(frozen=True)
class FeasibilityResult:
    mode: str
    n_total: int
    n_win: int
    n_loss: int
    first_date: str | None
    last_date: str | None
    min_n_ok: bool
    feature_stats: tuple[FeatureStat, ...]
    composite: CompositeStat


def composite_score(df: pd.DataFrame) -> pd.Series:
    total = pd.Series(0.0, index=df.index)
    for feat, sign in COMPOSITE:
        col = pd.to_numeric(df[feat], errors="coerce")
        std = col.std(ddof=0)
        if std and std > 0:
            z = (col - col.mean()) / std
        else:
            z = col * 0.0
        total = total + sign * z.fillna(0.0)
    return total


def _feature_stat(df: pd.DataFrame, dim: str, feat: str) -> FeatureStat:
    vals = df[feat].tolist()
    labels = df["label_win"].tolist()
    n = int(df[feat].notna().sum())
    auc = separation_auc(vals, labels)
    top, bottom = median_split_win_rate(vals, labels)
    return FeatureStat(
        dimension=dim, feature=feat, n=n, auc=auc,
        separation=abs(auc - 0.5), win_rate_top=top,
        win_rate_bottom=bottom, mwu_p=mannwhitney_p(vals, labels),
        direction="higher" if auc >= 0.5 else "lower",
    )


def analyze(df: pd.DataFrame, min_n: int) -> FeasibilityResult:
    mode = str(df["mode"].iloc[0]) if "mode" in df and len(df) else "?"
    n = len(df)
    n_win = int(df["label_win"].sum()) if n else 0
    dates = (
        pd.to_datetime(df["trade_date"]).dt.date
        if "trade_date" in df.columns and n else None
    )
    stats: list[FeatureStat] = []
    for dim, feats in DIMENSIONS.items():
        for feat in feats:
            if feat in df.columns:
                stats.append(_feature_stat(df, dim, feat))
    stats.sort(key=lambda s: s.separation, reverse=True)

    comp = composite_score(df) if n else pd.Series(dtype=float)
    c_top, c_bottom = (
        median_split_win_rate(comp.tolist(), df["label_win"].tolist())
        if n else (None, None)
    )
    composite = CompositeStat(
        n=n,
        auc=separation_auc(comp.tolist(), df["label_win"].tolist())
        if n else 0.5,
        win_rate_top=c_top, win_rate_bottom=c_bottom,
    )
    return FeasibilityResult(
        mode=mode, n_total=n, n_win=n_win, n_loss=n - n_win,
        first_date=str(dates.min()) if dates is not None else None,
        last_date=str(dates.max()) if dates is not None else None,
        min_n_ok=n >= min_n,
        feature_stats=tuple(stats), composite=composite,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_analysis.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/entry_strength_feasibility/analysis.py \
  backend/algo/research/entry_strength_feasibility/tests/test_analysis.py
git commit -m "feat(algo): entry-strength feasibility analysis orchestration (R2)"
```

---

### Task 5: Report rendering (`report.py`)

**Files:**
- Create: `backend/algo/research/entry_strength_feasibility/report.py`
- Test: `.../tests/test_report.py`

**Interfaces:**
- Consumes: `FeasibilityResult`, `FeatureStat`, `CompositeStat` (Task 4).
- Produces:
  - `render_report(result: FeasibilityResult) -> str` — markdown. MUST include: the caveat banner, dataset summary (n / win / loss / span / mode), a per-dimension feature ranking table, the composite headline line, and a go/no-go read.
  - `write_outputs(result: FeasibilityResult, out_dir: pathlib.Path) -> pathlib.Path` — writes `report.md` + `feature_ranking.csv`; returns the report path. `mkdir(parents=True, exist_ok=True)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from backend.algo.research.entry_strength_feasibility.analysis import (
    CompositeStat,
    FeasibilityResult,
    FeatureStat,
)
from backend.algo.research.entry_strength_feasibility.report import (
    render_report,
)


def _result():
    fs = FeatureStat(
        dimension="strength_resilience", feature="qm_mdd_pctile", n=44,
        auc=0.71, separation=0.21, win_rate_top=0.75, win_rate_bottom=0.45,
        mwu_p=0.03, direction="lower",
    )
    return FeasibilityResult(
        mode="live", n_total=44, n_win=27, n_loss=17,
        first_date="2026-05-29", last_date="2026-08-11", min_n_ok=False,
        feature_stats=(fs,),
        composite=CompositeStat(n=44, auc=0.63, win_rate_top=0.7,
                                win_rate_bottom=0.5),
    )


def test_render_report_has_caveat_summary_and_feature():
    md = render_report(_result())
    assert "out-of-sample" in md.lower()      # caveat banner present
    assert "n=44" in md or "44" in md
    assert "qm_mdd_pctile" in md               # ranking row
    assert "0.63" in md                         # composite headline AUC
    assert "min_n" in md.lower() or "underpowered" in md.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_report.py -q`
Expected: FAIL — cannot import `report`.

- [ ] **Step 3: Write minimal implementation**

```python
# report.py
"""Render the feasibility report (markdown + CSV)."""
from __future__ import annotations

import csv
import pathlib

from backend.algo.research.entry_strength_feasibility.analysis import (
    FeasibilityResult,
)

_CAVEAT = (
    "> ⚠️ **Exploratory, IN-SAMPLE — NOT out-of-sample.** Multiple "
    "comparisons uncorrected; small n. Directional only — do NOT "
    "calibrate thresholds from this report."
)


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def render_report(result: FeasibilityResult) -> str:
    lines: list[str] = []
    lines.append("# Entry-Strength Feasibility Report")
    lines.append("")
    lines.append(_CAVEAT)
    lines.append("")
    warn = "" if result.min_n_ok else " — ⚠️ UNDERPOWERED (min_n not met)"
    lines.append(
        f"**Cohort:** mode={result.mode} n={result.n_total} "
        f"(win={result.n_win} / loss={result.n_loss}) "
        f"span={result.first_date}..{result.last_date}{warn}"
    )
    lines.append("")
    lines.append(
        f"**Composite headline (pre-registered):** "
        f"AUC={_fmt(result.composite.auc)} "
        f"win-rate top/bottom="
        f"{_fmt(result.composite.win_rate_top)}/"
        f"{_fmt(result.composite.win_rate_bottom)}"
    )
    lines.append("")
    lines.append("## Per-feature separation (ranked)")
    lines.append("")
    lines.append(
        "| dimension | feature | n | AUC | dir | wr top | wr bot | MWU p |"
    )
    lines.append("|---|---|--:|--:|---|--:|--:|--:|")
    for s in result.feature_stats:
        lines.append(
            f"| {s.dimension} | {s.feature} | {s.n} | {_fmt(s.auc)} | "
            f"{s.direction} | {_fmt(s.win_rate_top)} | "
            f"{_fmt(s.win_rate_bottom)} | {_fmt(s.mwu_p)} |"
        )
    lines.append("")
    lines.append("## Go / no-go read")
    lines.append("")
    best = result.feature_stats[0] if result.feature_stats else None
    if best and best.separation >= 0.15:
        lines.append(
            f"Strongest separator: **{best.feature}** "
            f"(AUC {_fmt(best.auc)}). Worth carrying into a "
            "properly-powered (OOS) study as data grows."
        )
    else:
        lines.append(
            "No feature separates winners from losers meaningfully at "
            "this n — consistent with 'indistinguishable at entry'. "
            "Accumulate more labeled trades before revisiting."
        )
    return "\n".join(lines) + "\n"


def write_outputs(
    result: FeasibilityResult, out_dir: pathlib.Path
) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"
    report_path.write_text(render_report(result))
    with (out_dir / "feature_ranking.csv").open(
        "w", newline=""
    ) as fh:
        w = csv.writer(fh)
        w.writerow(
            ["dimension", "feature", "n", "auc", "separation",
             "win_rate_top", "win_rate_bottom", "mwu_p", "direction"]
        )
        for s in result.feature_stats:
            w.writerow([
                s.dimension, s.feature, s.n, s.auc, s.separation,
                s.win_rate_top, s.win_rate_bottom, s.mwu_p, s.direction,
            ])
    return report_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_report.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/entry_strength_feasibility/report.py \
  backend/algo/research/entry_strength_feasibility/tests/test_report.py
git commit -m "feat(algo): entry-strength feasibility report rendering (R2)"
```

---

### Task 6: CLI + README + real smoke run

**Files:**
- Create: `backend/algo/research/entry_strength_feasibility/__main__.py`
- Create: `backend/algo/research/entry_strength_feasibility/README.md`
- Test: `.../tests/test_cli_wiring.py`

**Interfaces:**
- Consumes: `load_labeled_rows`, `filter_cohort`, `to_frame` (Task 3), `analyze` (Task 4), `write_outputs` (Task 5).
- Produces: `main(argv: list[str] | None = None) -> int` — argparse `--mode {live,paper,all}` (default live), `--min-n` (default 30), `--out-dir` (default under the data home). Loads → filters → frames → analyzes → writes → logs a one-line summary.

- [ ] **Step 1: Write the failing test** (wiring only — DB stubbed)

```python
# tests/test_cli_wiring.py
from unittest.mock import patch

from backend.algo.research.entry_strength_feasibility import __main__ as cli

_RAW = [
    {"mode": "live", "filled": True, "outcome_settled": True,
     "label_win": w, "dry_run": False, "trade_date": "2026-08-01",
     "breadth_oversold": 5, "breadth_total": 100,
     "ess_absorption_volume_score": v, "qm_mdd_pctile": 1 - v / 10.0,
     "breadth_oversold_pct": 0.05, "ret_3d_prior": v,
     "rsi2_at_entry": 3.0, "dist_sma50_pct": 0.0, "dist_sma200_pct": 0.0,
     "ret_1d_prior": 0.0, "gap_pct": 0.0,
     "ess_selling_deceleration_score": 0.0,
     "ess_trend_stability_score": 0.0, "qm_score": 0.0, "ess_score": 0.0,
     "qm_rs_pctile": 0.0, "qm_sharpe_pctile": 0.0}
    for w, v in [(False, 1.0), (False, 2.0), (True, 3.0), (True, 4.0)]
]


def test_cli_writes_report(tmp_path):
    with patch.object(cli, "load_labeled_rows", return_value=_RAW):
        rc = cli.main(["--mode", "live", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "report.md").exists()
    assert "Entry-Strength Feasibility" in (
        tmp_path / "report.md"
    ).read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/tests/test_cli_wiring.py -q`
Expected: FAIL — cannot import `__main__`.

- [ ] **Step 3: Write minimal implementation**

```python
# __main__.py
"""On-demand entry-strength feasibility report (R2).

    docker compose exec -e PYTHONPATH=.:backend backend python -m \
        backend.algo.research.entry_strength_feasibility \
        [--mode live] [--min-n 30] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import logging
import pathlib

from backend.algo.research.entry_strength_feasibility.analysis import (
    analyze,
)
from backend.algo.research.entry_strength_feasibility.cohort import (
    filter_cohort,
    load_labeled_rows,
    to_frame,
)
from backend.algo.research.entry_strength_feasibility.report import (
    write_outputs,
)

_logger = logging.getLogger(__name__)


def _default_out_dir() -> pathlib.Path:
    from backend.paths import APP_HOME

    return (
        pathlib.Path(APP_HOME)
        / "research_runs"
        / "entry-strength-feasibility"
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["live", "paper", "all"],
                   default="live")
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--out-dir", default=None)
    args = p.parse_args(argv)

    rows = filter_cohort(load_labeled_rows(args.mode), args.mode)
    df = to_frame(rows)
    if df.empty:
        _logger.warning("no labeled cohort for mode=%s — nothing to do.",
                        args.mode)
        return 0
    result = analyze(df, args.min_n)
    out_dir = (
        pathlib.Path(args.out_dir) if args.out_dir else _default_out_dir()
    )
    path = write_outputs(result, out_dir)
    top = result.feature_stats[0] if result.feature_stats else None
    _logger.info(
        "feasibility: mode=%s n=%d (w=%d/l=%d) composite_auc=%.3f "
        "top=%s min_n_ok=%s -> %s",
        result.mode, result.n_total, result.n_win, result.n_loss,
        result.composite.auc, top.feature if top else "-",
        result.min_n_ok, path,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

Also create `README.md` documenting the module (purpose, cohort, the pre-registered composite + directions, how to run, output location, and the "directional/underpowered" caveat), mirroring `intraday_15m_mis_bakeoff/README.md`.

- [ ] **Step 4: Run test to verify it passes + full module suite**

Run: `docker compose exec -T backend python -m pytest backend/algo/research/entry_strength_feasibility/ -q`
Expected: PASS (all tasks' tests).

- [ ] **Step 5: Real smoke run (read-only) + verify report**

Run:
```bash
docker compose exec -e PYTHONPATH=.:backend backend python -m \
    backend.algo.research.entry_strength_feasibility --mode live
```
Expected: logs `feasibility: mode=live n=44 (w=27/l=17) composite_auc=... top=... min_n_ok=False`. Open the written `report.md` and sanity-check the caveat banner + ranking table. (This is the actual R2 deliverable output — read it.)

- [ ] **Step 6: Commit**

```bash
git add backend/algo/research/entry_strength_feasibility/__main__.py \
  backend/algo/research/entry_strength_feasibility/README.md \
  backend/algo/research/entry_strength_feasibility/tests/test_cli_wiring.py
git commit -m "feat(algo): entry-strength feasibility CLI + report run (R2)"
```

---

## Self-Review

**Spec coverage:**
- §3 cohort/features → Task 2 (registry) + Task 3 (loader/filter/derive). ✓
- §4A univariate AUC/win-rate/MWU → Task 1 (stats) + Task 4 (orchestration). ✓
- §4B pre-registered composite → Task 2 (`COMPOSITE`) + Task 4 (`composite_score`). ✓
- §5 report + caveat banner + CSV + re-runnable + data-home output → Task 5 + Task 6. ✓
- §8 tests (AUC correctness, median-split, cohort filter, composite, min-n guard) → Tasks 1/3/4 tests + Task 5/6. ✓
- §6/§7 switch + activation criterion → **out of scope for this build** (spec §2/§9); not planned here, by design. ✓

**Placeholder scan:** No TBD/TODO; every code step has real content. README content is described with an explicit contents list (acceptable — it's prose docs, not code). ✓

**Type consistency:** `FeatureStat`/`CompositeStat`/`FeasibilityResult` defined in Task 4 and consumed verbatim in Tasks 5/6. `separation_auc`/`median_split_win_rate`/`mannwhitney_p` signatures consistent across Tasks 1/4. `filter_cohort`/`to_frame`/`load_labeled_rows` consistent across Tasks 3/6. `breadth_oversold_pct` derived in Task 3, referenced in Task 2 `KNOWN_COLUMNS` + Task 2 `COMPOSITE`. ✓

**Verify before building (confirmed):** `disposable_pg_session` is importable from `backend.db.engine` (verified at `backend/db/engine.py:62`). The data-home path is the `APP_HOME: Path` constant in `backend/paths.py` (verified) — not a function; Task 6 imports `from backend.paths import APP_HOME`. `mannwhitneyu`/`roc_auc_score` ship with the existing scipy/sklearn stack — implementer should confirm `import scipy` + `sklearn` resolve in the backend container before Task 1 Step 3.
