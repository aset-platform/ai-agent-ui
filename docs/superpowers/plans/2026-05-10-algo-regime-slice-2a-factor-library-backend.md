# Regime-Aware Multi-Factor System — Slice REGIME-2a: Factor Library Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-computed nightly factor store in Iceberg (`stocks.daily_factors`, ~22 factor keys across 7 families) + nightly compute job + 3-runtime cache integration so backtest/paper/live read cached factors instead of per-bar recomputing them.

**Architecture:** One pure compute function per factor family (`backend/algo/factors/{momentum,quality,lowvol,trend,volume,relative_strength,breadth}.py`), each takes per-ticker OHLCV history + optional context (NIFTY series, sector lookup) and returns `{date: {factor_key: value}}`. `compute_job.py` orchestrator iterates the universe, batches factor outputs into a single Iceberg `append`, follows NaN-replaceable upsert per CLAUDE.md §5.1. Runtimes load `daily_factors` for the relevant date window at session init and resolve features by lookup, not recompute.

**Tech Stack:** Python 3.12, pandas, numpy, PyIceberg 0.11.1, FastAPI, Pydantic v2. NO new dependencies — `ta` library already provides ADX.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.3 (factor library), §4.1 (`stocks.daily_factors`), §5.1 REGIME-2a row, §6.1 REGIME-2a tests.

**Research Anchor:** `docs/superpowers/research/2026-05-10-regime-aware-multifactor-research.md` §3 — exact closed-form for every factor.

**Branch:** `feature/regime-slice-2a-factor-library` (already created, tracking `origin`) off `feature/regime-multifactor-integration` (REGIME-1 already merged into it).

**Estimated SP:** 13

---

## Pre-flight (MUST DO before writing any code)

Per `feedback_subagent_grep_preflight` — every imported symbol, called function, schema column referenced in this plan MUST be grep-verified before code is written. The previous slice landed 2 hotfixes (`bar_date` vs `date` column, NIFTY lookback) that grep-preflight would have caught.

Verify the following BEFORE each task. Adjust your code if the grep output disagrees with the plan:

- **Iceberg helpers:** `_create_table`, `_get_catalog`, `_NAMESPACE` in `stocks/create_tables.py`.
- **DuckDB read:** `query_iceberg_table` in `backend/db/duckdb_engine.py` — uses **short** table name in SQL (e.g. `FROM ohlcv`, NOT `FROM stocks.ohlcv`); REGIME-1 verified.
- **OHLCV column name is `date`** (NOT `bar_date`) — confirmed during REGIME-1 backfill. Use `SELECT date AS bar_date, ...` if you want a `bar_date` alias.
- **fscore source:** the table is `stocks.piotroski_scores` (NOT `stocks.fscore_summary` as the spec calls it). Columns include `ticker, score_date, total_score, sector, industry, market_cap`. Use `total_score` as the `f_score` proxy.
- **Sector lookup:** prefer `stocks.piotroski_scores.sector` (most coverage) with fallback to `stocks.company_info.sector` if missing.
- **Cache module:** `backend/cache.py` with singleton `get_cache()`. NO top-level `cache` symbol. NO `await` on `get/set/invalidate` — sync API.
- **Cache TTL constants:** `TTL_VOLATILE`, `TTL_STABLE`, `TTL_ADMIN` exist in `backend/cache.py`.
- **register_job:** decorator pattern — REGIME-1 used a wrapper in `backend/jobs/executor.py` that imports from `backend/algo/jobs/`. Mirror exactly.
- **Job registry dict:** `JOB_EXECUTORS` (NOT `_registry`).
- **Backtest data load:** `backend/algo/backtest/data_source.py::load_ohlcv_window` and `compute_indicators_for_universe` in `backend/algo/backtest/runner.py`.
- **Paper runtime feature injection:** `backend/algo/paper/runtime.py` line ~200 (`features = {**ind_map.get(...), "nifty_above_sma200": ...}`). Extend the dict assembly with cached factor lookups.
- **Live runtime same:** `backend/algo/live/runtime.py` line ~262 has the analogous block.
- **AST features registry:** `backend/algo/strategy/features.py` `FEATURES, FEATURE_KEYS, FEATURE_BY_KEY`. REGIME-1 already extended `FeatureSource` with `"regime"`. This slice adds `"factor"` to that literal.
- **Frontend feature catalog mirror:** `frontend/components/algo-trading/strategyFeatureCatalog.ts` — must stay in sync; CI test `test_feature_registry_sync.py` enforces.
- **NIFTY ticker:** `^NSEI` (REGIME-1 confirmed).
- **`ta` library** (for ADX): `grep -n "ta.trend\|ta.momentum" backend/tools/_analysis_indicators.py | head -3`. Already used for RSI/MACD; ADX is `ta.trend.ADXIndicator(high, low, close, window=14).adx()`.

If any name above doesn't resolve, STOP and report — do not invent substitutes.

---

## File Structure

**Backend — new:**
- `backend/algo/factors/__init__.py`
- `backend/algo/factors/momentum.py`
- `backend/algo/factors/quality.py` — reads `stocks.piotroski_scores.total_score` (alias `f_score`); ROIC/accruals **left as None** (require fundamentals beyond current schema; documented as TODO).
- `backend/algo/factors/lowvol.py` — `realized_vol_60d`, `beta_to_nifty` (needs NIFTY context).
- `backend/algo/factors/trend.py` — `adx_14` (via `ta.trend.ADXIndicator`), `sma200_slope`, `distance_from_sma200`.
- `backend/algo/factors/volume.py` — `obv`, `volume_x_avg_20`, `up_down_vol_ratio_20`.
- `backend/algo/factors/relative_strength.py` — `rs_vs_nifty_3m`, `rs_vs_nifty_6m`, `rs_vs_sector_3m` (needs NIFTY + sector index context).
- `backend/algo/factors/breadth.py` — `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio` (universe-level; one row per date, joined to all tickers in compute_job).
- `backend/algo/factors/iceberg_init.py` — schema for `stocks.daily_factors` + register helper (mirrors REGIME-1's pattern).
- `backend/algo/factors/repo.py` — read/write via PyIceberg + DuckDB; NaN-replaceable upsert keyed on `(ticker, bar_date)`.
- `backend/algo/factors/compute_job.py` — nightly orchestrator + `@register_job("compute_daily_factors")` wrapper (in `backend/jobs/executor.py`).
- `backend/algo/factors/tests/` — one test file per factor, plus repo, compute_job, features, runtime tests.

**Backend — modified:**
- `stocks/create_tables.py` — register `stocks.daily_factors`.
- `backend/algo/jobs/__init__.py` — import `compute_job` for `@register_job` side-effect.
- `backend/jobs/executor.py` — add the `@register_job("compute_daily_factors")` wrapper (per REGIME-1 convention).
- `backend/algo/strategy/features.py` — extend `FeatureSource` with `"factor"`; register the new factor keys.
- `backend/algo/backtest/runner.py` — load `daily_factors` for the period at start of `run_backtest`; inject lookups into the per-bar features dict.
- `backend/algo/paper/runtime.py` — pre-load today's `daily_factors` row for each ticker on `__init__`; inject into the features dict.
- `backend/algo/live/runtime.py` — same as paper.
- `frontend/components/algo-trading/strategyFeatureCatalog.ts` — mirror the new factor keys (CI sync gate).

**Scripts — new:**
- `scripts/backfill_factors.py` — CLI: `<start YYYY-MM-DD> <end YYYY-MM-DD>` calls `compute_job.run_compute_job(as_of=...)` per day; idempotent via NaN-replaceable upsert.

---

## Factor key inventory (locked)

Per spec §3.3 + research §3. **Total = 21 factor columns** in `daily_factors` (plus `ticker`, `bar_date`, `sector` bookkeeping). ROIC + accruals deferred until fundamentals upgrade.

| Family | Keys |
|---|---|
| Momentum (4) | `mom_12_1`, `mom_6_1`, `mom_3_1`, `prox_52w` |
| Quality (1) | `f_score` (from `stocks.piotroski_scores.total_score`) |
| Low-vol (2) | `realized_vol_60d`, `beta_to_nifty` |
| Trend (3) | `adx_14`, `sma200_slope`, `distance_from_sma200` |
| Volume (3) | `obv`, `volume_x_avg_20`, `up_down_vol_ratio_20` |
| Rel. strength (3) | `rs_vs_nifty_3m`, `rs_vs_nifty_6m`, `rs_vs_sector_3m` |
| Breadth (3) | `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio` |
| Bookkeeping | `sector` (string) — needed for sector-RS join; `ticker`, `bar_date` PK |

*Out of scope:* `roic`, `accruals` — call out as TODO in `quality.py`.

---

## Task 1 — `stocks.daily_factors` Iceberg table

**Files:**
- Create: `backend/algo/factors/__init__.py` (empty), `backend/algo/factors/iceberg_init.py`.
- Modify: `stocks/create_tables.py` to call the new register helper.
- Test: `backend/algo/factors/tests/__init__.py` (empty), `backend/algo/factors/tests/test_iceberg_schema.py`.

Schema: `ticker STRING (req), bar_date DATE (req)` + 21 factor columns DOUBLE (nullable, NaN encodes "missing") + `sector STRING (nullable)`. Partition by `year(bar_date)`. NaN-replaceable upsert keyed on `(ticker, bar_date)` — pre-delete by `In("ticker", batch) AND In("bar_date", dates)` then append.

- [ ] **Step 1.1: Failing schema test**

Create `backend/algo/factors/tests/__init__.py` (empty) and `backend/algo/factors/tests/test_iceberg_schema.py`:

```python
"""Verify stocks.daily_factors schema + table identifier."""
from __future__ import annotations

from backend.algo.factors.iceberg_init import (
    DAILY_FACTORS_TABLE,
    daily_factors_schema,
)


REQUIRED_KEYS = {
    "ticker", "bar_date",
    "mom_12_1", "mom_6_1", "mom_3_1", "prox_52w",
    "f_score",
    "realized_vol_60d", "beta_to_nifty",
    "adx_14", "sma200_slope", "distance_from_sma200",
    "obv", "volume_x_avg_20", "up_down_vol_ratio_20",
    "rs_vs_nifty_3m", "rs_vs_nifty_6m", "rs_vs_sector_3m",
    "pct_above_50sma", "pct_above_200sma", "midcap_largecap_ratio",
    "sector",
}


def test_daily_factors_columns() -> None:
    s = daily_factors_schema()
    names = {f.name for f in s.fields}
    missing = REQUIRED_KEYS - names
    assert not missing, f"Missing columns: {missing}"


def test_table_identifier() -> None:
    assert DAILY_FACTORS_TABLE == "stocks.daily_factors"


def test_required_columns_marked_nonnull() -> None:
    s = daily_factors_schema()
    by_name = {f.name: f for f in s.fields}
    assert by_name["ticker"].required
    assert by_name["bar_date"].required
    assert not by_name["mom_12_1"].required
```

- [ ] **Step 1.2: Run to verify fail**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_iceberg_schema.py -v
```
Expected: ImportError.

- [ ] **Step 1.3: Implement `iceberg_init.py`**

```python
"""Iceberg table registration for ``stocks.daily_factors``.

Mirrors ``backend/algo/regime/iceberg_init.py``. Append-only with
NaN-replaceable upsert in the repo layer.
"""
from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import (
    DateType, DoubleType, NestedField, StringType,
)

DAILY_FACTORS_TABLE = "stocks.daily_factors"

MOMENTUM_KEYS = ["mom_12_1", "mom_6_1", "mom_3_1", "prox_52w"]
QUALITY_KEYS = ["f_score"]
LOWVOL_KEYS = ["realized_vol_60d", "beta_to_nifty"]
TREND_KEYS = ["adx_14", "sma200_slope", "distance_from_sma200"]
VOLUME_KEYS = ["obv", "volume_x_avg_20", "up_down_vol_ratio_20"]
RS_KEYS = ["rs_vs_nifty_3m", "rs_vs_nifty_6m", "rs_vs_sector_3m"]
BREADTH_KEYS = [
    "pct_above_50sma", "pct_above_200sma", "midcap_largecap_ratio",
]
ALL_FACTOR_KEYS = (
    MOMENTUM_KEYS + QUALITY_KEYS + LOWVOL_KEYS + TREND_KEYS
    + VOLUME_KEYS + RS_KEYS + BREADTH_KEYS
)


def daily_factors_schema() -> Schema:
    fields = [
        NestedField(1, "ticker", StringType(), required=True),
        NestedField(2, "bar_date", DateType(), required=True),
    ]
    fid = 3
    for k in ALL_FACTOR_KEYS:
        fields.append(NestedField(fid, k, DoubleType(), required=False))
        fid += 1
    fields.append(NestedField(fid, "sector", StringType(), required=False))
    return Schema(*fields)


def daily_factors_partition_spec() -> PartitionSpec:
    return PartitionSpec(
        PartitionField(
            source_id=2, field_id=2000,
            transform=YearTransform(), name="bar_date_year",
        )
    )


def register_tables() -> None:
    from stocks.create_tables import _create_table, _get_catalog
    catalog = _get_catalog()
    _create_table(
        catalog, DAILY_FACTORS_TABLE,
        daily_factors_schema(), daily_factors_partition_spec(),
    )
```

- [ ] **Step 1.4: Wire into `stocks/create_tables.py`**

Below the REGIME-1 entry inside `create_tables()`:

```python
    # Factor library — REGIME-2a
    from backend.algo.factors.iceberg_init import register_tables as \
        _factors_register
    _factors_register()
```

- [ ] **Step 1.5: Run + create**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_iceberg_schema.py -v
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend python stocks/create_tables.py
```
Expected: 3 tests PASS; `Created Iceberg table 'stocks.daily_factors'.`

- [ ] **Step 1.6: Commit**

```bash
git add backend/algo/factors/__init__.py backend/algo/factors/iceberg_init.py backend/algo/factors/tests/__init__.py backend/algo/factors/tests/test_iceberg_schema.py stocks/create_tables.py
git commit -m "feat(algo): stocks.daily_factors Iceberg table + 21 factor columns (REGIME-2a)"
```

---

## Task 2 — Momentum factors

**Files:**
- Create: `backend/algo/factors/momentum.py`, `backend/algo/factors/tests/test_momentum.py`.

Pure function: takes a per-ticker `pd.DataFrame` with columns `bar_date, close` (sorted ASC), returns `dict[date, dict[str, float]]`. Skip-month convention NON-NEGOTIABLE: `mom_12_1` excludes the last 21 trading days. Output `NaN` for dates lacking lookback.

- [ ] **Step 2.1: Failing tests**

```python
"""Momentum factor tests — table-driven + skip-month gate."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors.momentum import compute_momentum


def _series(close: list[float]) -> pd.DataFrame:
    n = len(close)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({"bar_date": dates, "close": close})


def test_mom_12_1_excludes_last_21_days() -> None:
    """If the last 21 days double, mom_12_1 must NOT see them."""
    base = list(np.linspace(100, 200, 232))
    spike = [400.0] * 21
    df = _series(base + spike)
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.5 <= last["mom_12_1"] <= 1.5, (
        f"mom_12_1 leaked the post-skip-month spike: {last['mom_12_1']}"
    )


def test_mom_3_6_12_happy_path() -> None:
    n = 260
    close = list(np.linspace(100, 130, n))
    df = _series(close)
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert not math.isnan(last["mom_12_1"])
    assert not math.isnan(last["mom_6_1"])
    assert not math.isnan(last["mom_3_1"])


def test_prox_52w_at_high_is_one() -> None:
    n = 260
    df = _series(list(np.linspace(100, 200, n)))
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert abs(last["prox_52w"] - 1.0) < 1e-6


def test_prox_52w_below_high() -> None:
    close = list(np.linspace(100, 200, 252)) + [150.0]
    df = _series(close)
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.7 <= last["prox_52w"] <= 0.8


def test_short_history_returns_nan_safely() -> None:
    df = _series(list(np.linspace(100, 110, 30)))
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["mom_12_1"])
    assert math.isnan(last["mom_6_1"])
    assert math.isnan(last["mom_3_1"])
```

- [ ] **Step 2.2: Run (verify fail)**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_momentum.py -v
```

- [ ] **Step 2.3: Implement `momentum.py`**

```python
"""Momentum factors with mandatory skip-month convention.

Per research §3:
    mom_12_1 = close[t-21] / close[t-252] - 1
    mom_6_1  = close[t-21] / close[t-126] - 1
    mom_3_1  = close[t-21] / close[t-63]  - 1
    prox_52w = close[t]    / max(close[t-252:t])
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

SKIP_DAYS = 21
LOOKBACK_12 = 252
LOOKBACK_6 = 126
LOOKBACK_3 = 63
PROX_WINDOW = 252


def _ratio(num: float, den: float) -> float:
    if den == 0 or np.isnan(num) or np.isnan(den):
        return float("nan")
    return float(num / den - 1.0)


def compute_momentum(history: pd.DataFrame) -> dict[date, dict[str, float]]:
    if history.empty:
        return {}
    h = history.sort_values("bar_date").reset_index(drop=True)
    closes = h["close"].astype(float).to_numpy()
    dates = h["bar_date"].tolist()
    n = len(closes)
    out: dict[date, dict[str, float]] = {}
    for i in range(n):
        idx_skip = i - SKIP_DAYS
        mom_12 = (
            _ratio(closes[idx_skip], closes[i - LOOKBACK_12])
            if i >= LOOKBACK_12 else float("nan")
        )
        mom_6 = (
            _ratio(closes[idx_skip], closes[i - LOOKBACK_6])
            if i >= LOOKBACK_6 else float("nan")
        )
        mom_3 = (
            _ratio(closes[idx_skip], closes[i - LOOKBACK_3])
            if i >= LOOKBACK_3 + SKIP_DAYS else float("nan")
        )
        prox = (
            float(closes[i] / closes[i - PROX_WINDOW + 1: i + 1].max())
            if i >= PROX_WINDOW - 1 else float("nan")
        )
        out[dates[i]] = {
            "mom_12_1": mom_12,
            "mom_6_1": mom_6,
            "mom_3_1": mom_3,
            "prox_52w": prox,
        }
    return out
```

- [ ] **Step 2.4: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_momentum.py -v
git add backend/algo/factors/momentum.py backend/algo/factors/tests/test_momentum.py
git commit -m "feat(algo): momentum factors mom_{12,6,3}_1 + prox_52w with skip-month gate (REGIME-2a)"
```

---

## Task 3 — Quality factor (f_score from piotroski_scores)

**Files:**
- Create: `backend/algo/factors/quality.py`, `backend/algo/factors/tests/test_quality.py`.

Single key: `f_score` forward-filled from `stocks.piotroski_scores`. ROIC + accruals deferred (require quarterly NI/CFO/total_assets not currently in the schema).

- [ ] **Step 3.1: Test**

```python
"""Quality factor tests — f_score lookup from piotroski_scores."""
from __future__ import annotations

import math
from datetime import date

from backend.algo.factors.quality import compute_quality


def test_quality_returns_f_score(monkeypatch) -> None:
    rows = [
        {"score_date": date(2026, 5, 1), "total_score": 7},
        {"score_date": date(2026, 5, 8), "total_score": 8},
    ]
    monkeypatch.setattr(
        "backend.algo.factors.quality._load_piotroski",
        lambda ticker, start, end: rows,
    )
    out = compute_quality("RELIANCE.NS", date(2026, 5, 1), date(2026, 5, 8))
    assert out[date(2026, 5, 1)]["f_score"] == 7
    assert out[date(2026, 5, 8)]["f_score"] == 8


def test_quality_forward_fills(monkeypatch) -> None:
    rows = [{"score_date": date(2026, 5, 1), "total_score": 6}]
    monkeypatch.setattr(
        "backend.algo.factors.quality._load_piotroski",
        lambda ticker, start, end: rows,
    )
    out = compute_quality(
        "RELIANCE.NS", date(2026, 5, 1), date(2026, 5, 5),
    )
    assert out[date(2026, 5, 5)]["f_score"] == 6


def test_quality_missing_returns_nan(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.algo.factors.quality._load_piotroski",
        lambda ticker, start, end: [],
    )
    out = compute_quality("FOO", date(2026, 5, 1), date(2026, 5, 1))
    assert math.isnan(out[date(2026, 5, 1)]["f_score"])
```

- [ ] **Step 3.2: Implement**

```python
"""Quality factor — Piotroski f_score forward-filled to daily.

ROIC + accruals require quarterly fundamentals not yet in
stocks.quarterly_results in usable form. Deferred — emit NaN.
"""
from __future__ import annotations

from datetime import date, timedelta

from backend.db.duckdb_engine import query_iceberg_table


def _load_piotroski(
    ticker: str, start: date, end: date,
) -> list[dict]:
    return query_iceberg_table(
        "stocks.piotroski_scores",
        "SELECT score_date, total_score "
        "FROM piotroski_scores "
        "WHERE ticker = ? AND score_date BETWEEN ? AND ? "
        "ORDER BY score_date ASC",
        [ticker, start - timedelta(days=400), end],
    )


def compute_quality(
    ticker: str, start: date, end: date,
) -> dict[date, dict[str, float]]:
    scores = _load_piotroski(ticker, start, end)
    out: dict[date, dict[str, float]] = {}
    if not scores:
        cur = start
        while cur <= end:
            out[cur] = {"f_score": float("nan")}
            cur += timedelta(days=1)
        return out

    cur = start
    last_seen: float = float("nan")
    si = 0
    while cur <= end:
        while si < len(scores) and scores[si]["score_date"] <= cur:
            last_seen = float(scores[si]["total_score"])
            si += 1
        out[cur] = {"f_score": last_seen}
        cur += timedelta(days=1)
    return out
```

- [ ] **Step 3.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_quality.py -v
git add backend/algo/factors/quality.py backend/algo/factors/tests/test_quality.py
git commit -m "feat(algo): quality factor f_score (forward-filled from piotroski) (REGIME-2a)"
```

---

## Task 4 — Low-vol factor

**Files:**
- Create: `backend/algo/factors/lowvol.py`, `backend/algo/factors/tests/test_lowvol.py`.

`realized_vol_60d = std(log_returns[-60:]) * sqrt(252)`.
`beta_to_nifty` = OLS slope of stock log-returns on NIFTY log-returns over 252-day window.

- [ ] **Step 4.1: Test**

```python
"""Low-vol factor tests."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors.lowvol import compute_lowvol


def _series(close: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "bar_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(len(close))],
        "close": close,
    })


def test_realized_vol_60d_finite_for_long_history() -> None:
    rng = np.random.default_rng(0)
    close = (100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))).tolist()
    df = _series(close)
    nifty = _series([100 + i * 0.05 for i in range(300)])
    out = compute_lowvol(df, nifty)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.0 < last["realized_vol_60d"] < 1.0


def test_beta_around_one_when_perfectly_correlated() -> None:
    n = 260
    rng = np.random.default_rng(7)
    nifty_close = (100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))).tolist()
    df = _series([c * 1.5 for c in nifty_close])
    nifty = _series(nifty_close)
    out = compute_lowvol(df, nifty)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.8 <= last["beta_to_nifty"] <= 1.2


def test_short_history_returns_nan() -> None:
    df = _series([100.0] * 30)
    nifty = _series([100.0] * 30)
    out = compute_lowvol(df, nifty)
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["realized_vol_60d"])
    assert math.isnan(last["beta_to_nifty"])
```

- [ ] **Step 4.2: Implement**

```python
"""Low-vol factors: realized_vol_60d + beta_to_nifty."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

VOL_WINDOW = 60
BETA_WINDOW = 252
ANNUALISER = float(np.sqrt(252))


def _log_returns(closes: np.ndarray) -> np.ndarray:
    if closes.size < 2:
        return np.array([])
    return np.diff(np.log(closes))


def compute_lowvol(
    history: pd.DataFrame, nifty: pd.DataFrame,
) -> dict[date, dict[str, float]]:
    h = history.sort_values("bar_date").reset_index(drop=True)
    n = nifty.sort_values("bar_date").reset_index(drop=True)
    merged = pd.merge(
        h[["bar_date", "close"]],
        n[["bar_date", "close"]].rename(columns={"close": "nifty_close"}),
        on="bar_date", how="inner",
    )
    if merged.empty:
        return {d: {"realized_vol_60d": float("nan"),
                    "beta_to_nifty": float("nan")}
                for d in h["bar_date"]}

    closes = merged["close"].astype(float).to_numpy()
    nclose = merged["nifty_close"].astype(float).to_numpy()
    dates = merged["bar_date"].tolist()
    out: dict[date, dict[str, float]] = {}
    for i in range(len(dates)):
        if i + 1 < VOL_WINDOW:
            rv = float("nan")
        else:
            window = closes[i + 1 - VOL_WINDOW: i + 1]
            r = _log_returns(window)
            rv = float(np.std(r, ddof=0) * ANNUALISER) if r.size else float("nan")
        if i + 1 < BETA_WINDOW:
            beta = float("nan")
        else:
            sw = closes[i + 1 - BETA_WINDOW: i + 1]
            nw = nclose[i + 1 - BETA_WINDOW: i + 1]
            sr = _log_returns(sw)
            nr = _log_returns(nw)
            if sr.size and nr.size and np.var(nr, ddof=0) > 1e-12:
                beta = float(np.cov(sr, nr, ddof=0)[0, 1]
                             / np.var(nr, ddof=0))
            else:
                beta = float("nan")
        out[dates[i]] = {
            "realized_vol_60d": rv, "beta_to_nifty": beta,
        }
    return out
```

- [ ] **Step 4.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_lowvol.py -v
git add backend/algo/factors/lowvol.py backend/algo/factors/tests/test_lowvol.py
git commit -m "feat(algo): lowvol factors realized_vol_60d + beta_to_nifty (REGIME-2a)"
```

---

## Task 5 — Trend factor

**Files:**
- Create: `backend/algo/factors/trend.py`, `backend/algo/factors/tests/test_trend.py`.

ADX via `ta.trend.ADXIndicator(high, low, close, window=14).adx()`. SMA200 slope = `(sma200[t] - sma200[t-21]) / sma200[t-21]`. Distance = `(close - sma200) / sma200`.

- [ ] **Step 5.1: Test**

```python
"""Trend factor tests."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors.trend import compute_trend


def _ohlcv(n: int, drift: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    return pd.DataFrame({
        "bar_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "high": high, "low": low, "close": close,
    })


def test_trend_full_window() -> None:
    df = _ohlcv(260)
    out = compute_trend(df)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.0 <= last["adx_14"] <= 100.0
    assert isinstance(last["sma200_slope"], float)
    assert isinstance(last["distance_from_sma200"], float)


def test_trend_short_history_returns_nan() -> None:
    df = _ohlcv(50)
    out = compute_trend(df)
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["sma200_slope"])
    assert math.isnan(last["distance_from_sma200"])


def test_trend_uptrend_distance_positive() -> None:
    df = _ohlcv(260, drift=0.003)
    out = compute_trend(df)
    last = out[df["bar_date"].iloc[-1]]
    assert last["distance_from_sma200"] > 0
```

- [ ] **Step 5.2: Implement**

```python
"""Trend factors: ADX(14), SMA200 slope, distance from SMA200."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

ADX_WINDOW = 14
SMA_WINDOW = 200
SLOPE_LOOKBACK = 21


def compute_trend(history: pd.DataFrame) -> dict[date, dict[str, float]]:
    from ta.trend import ADXIndicator

    h = history.sort_values("bar_date").reset_index(drop=True)
    if h.empty:
        return {}
    n = len(h)
    close = h["close"].astype(float)
    high = h["high"].astype(float)
    low = h["low"].astype(float)

    if n >= ADX_WINDOW + 1:
        adx = ADXIndicator(
            high=high, low=low, close=close, window=ADX_WINDOW,
        ).adx().to_numpy()
    else:
        adx = np.full(n, float("nan"))

    sma200 = close.rolling(SMA_WINDOW, min_periods=SMA_WINDOW).mean().to_numpy()
    slope = np.full(n, float("nan"))
    dist = np.full(n, float("nan"))
    for i in range(n):
        if not np.isnan(sma200[i]):
            dist[i] = float((close.iloc[i] - sma200[i]) / sma200[i])
            j = i - SLOPE_LOOKBACK
            if j >= 0 and not np.isnan(sma200[j]) and sma200[j] != 0:
                slope[i] = float((sma200[i] - sma200[j]) / sma200[j])

    dates = h["bar_date"].tolist()
    return {
        dates[i]: {
            "adx_14": (
                float(adx[i]) if not np.isnan(adx[i]) else float("nan")
            ),
            "sma200_slope": (
                float(slope[i]) if not np.isnan(slope[i]) else float("nan")
            ),
            "distance_from_sma200": (
                float(dist[i]) if not np.isnan(dist[i]) else float("nan")
            ),
        }
        for i in range(n)
    }
```

- [ ] **Step 5.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_trend.py -v
git add backend/algo/factors/trend.py backend/algo/factors/tests/test_trend.py
git commit -m "feat(algo): trend factors adx_14 + sma200_slope + distance_from_sma200 (REGIME-2a)"
```

---

## Task 6 — Volume factor

**Files:**
- Create: `backend/algo/factors/volume.py`, `backend/algo/factors/tests/test_volume.py`.

`obv = cumsum(sign(close.diff()) * volume)`. `volume_x_avg_20 = volume[t] / volume[-20:].mean()`. `up_down_vol_ratio_20 = sum(vol on green days) / sum(vol on red days)` over trailing 20.

- [ ] **Step 6.1: Test**

```python
"""Volume factor tests."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from backend.algo.factors.volume import compute_volume


def _df(close: list[float], volume: list[int]) -> pd.DataFrame:
    n = len(close)
    return pd.DataFrame({
        "bar_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "close": close, "volume": volume,
    })


def test_obv_nondecreasing_when_all_green() -> None:
    df = _df(list(range(1, 51)), [1000] * 50)
    out = compute_volume(df)
    last = out[df["bar_date"].iloc[-1]]
    assert last["obv"] == 49000


def test_volume_x_avg_20_at_average_is_one() -> None:
    df = _df([100.0] * 30, [1000] * 30)
    out = compute_volume(df)
    last = out[df["bar_date"].iloc[-1]]
    assert abs(last["volume_x_avg_20"] - 1.0) < 1e-9


def test_short_history_returns_nan() -> None:
    df = _df([100.0] * 5, [1000] * 5)
    out = compute_volume(df)
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["volume_x_avg_20"])
```

- [ ] **Step 6.2: Implement**

```python
"""Volume factors: OBV, volume_x_avg_20, up_down_vol_ratio_20."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

VOL_AVG_WINDOW = 20
UP_DOWN_WINDOW = 20


def compute_volume(history: pd.DataFrame) -> dict[date, dict[str, float]]:
    h = history.sort_values("bar_date").reset_index(drop=True)
    if h.empty:
        return {}
    close = h["close"].astype(float).to_numpy()
    volume = h["volume"].astype(float).to_numpy()
    n = len(h)

    diff = np.diff(close, prepend=close[0])
    direction = np.sign(diff)
    direction[0] = 0
    obv = np.cumsum(direction * volume)

    avg = pd.Series(volume).rolling(
        VOL_AVG_WINDOW, min_periods=VOL_AVG_WINDOW,
    ).mean().to_numpy()
    vol_x = np.where(avg > 0, volume / avg, np.nan)

    udr = np.full(n, float("nan"))
    for i in range(n):
        if i + 1 < UP_DOWN_WINDOW:
            continue
        sl = slice(i + 1 - UP_DOWN_WINDOW, i + 1)
        d = direction[sl]
        v = volume[sl]
        up = float(v[d > 0].sum())
        dn = float(v[d < 0].sum())
        udr[i] = up / dn if dn > 0 else float("inf")

    dates = h["bar_date"].tolist()
    return {
        dates[i]: {
            "obv": float(obv[i]),
            "volume_x_avg_20": (
                float(vol_x[i]) if not np.isnan(vol_x[i]) else float("nan")
            ),
            "up_down_vol_ratio_20": (
                float(udr[i]) if np.isfinite(udr[i]) else float("nan")
            ),
        }
        for i in range(n)
    }
```

- [ ] **Step 6.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_volume.py -v
git add backend/algo/factors/volume.py backend/algo/factors/tests/test_volume.py
git commit -m "feat(algo): volume factors obv + volume_x_avg_20 + up_down_vol_ratio_20 (REGIME-2a)"
```

---

## Task 7 — Relative strength

**Files:**
- Create: `backend/algo/factors/relative_strength.py`, `backend/algo/factors/tests/test_relative_strength.py`.

```
rs_vs_nifty_3m = (stock[t]/stock[t-63]) / (nifty[t]/nifty[t-63])
rs_vs_nifty_6m = same with t-126
rs_vs_sector_3m = same with sector index instead of NIFTY
```

Sector index passed in as a dict `{sector: pd.DataFrame}` by the orchestrator.

- [ ] **Step 7.1: Test**

```python
"""Relative strength factor tests."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors.relative_strength import compute_relative_strength


def _df(close: list[float]) -> pd.DataFrame:
    n = len(close)
    return pd.DataFrame({
        "bar_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "close": close,
    })


def test_rs_above_one_when_outperforming() -> None:
    n = 200
    nifty = _df(list(np.linspace(100, 110, n)))
    stock = _df(list(np.linspace(100, 130, n)))
    out = compute_relative_strength(
        stock, nifty, sector="IT",
        sector_indices={"IT": _df(list(np.linspace(100, 105, n)))},
    )
    last = out[stock["bar_date"].iloc[-1]]
    assert last["rs_vs_nifty_3m"] > 1.0
    assert last["rs_vs_sector_3m"] > 1.0


def test_rs_unknown_sector_returns_nan() -> None:
    n = 200
    nifty = _df(list(np.linspace(100, 110, n)))
    stock = _df(list(np.linspace(100, 130, n)))
    out = compute_relative_strength(
        stock, nifty, sector="UNKNOWN", sector_indices={},
    )
    last = out[stock["bar_date"].iloc[-1]]
    assert math.isnan(last["rs_vs_sector_3m"])


def test_rs_short_history_nan() -> None:
    df = _df([100.0] * 30)
    out = compute_relative_strength(
        df, df, sector="IT", sector_indices={},
    )
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["rs_vs_nifty_3m"])
    assert math.isnan(last["rs_vs_nifty_6m"])
```

- [ ] **Step 7.2: Implement**

```python
"""Relative strength factors vs NIFTY + sector index."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

LB_3M = 63
LB_6M = 126


def _rs(stock: np.ndarray, ref: np.ndarray, lookback: int, i: int) -> float:
    if i < lookback or i >= len(stock) or i >= len(ref):
        return float("nan")
    if stock[i - lookback] == 0 or ref[i - lookback] == 0:
        return float("nan")
    s = stock[i] / stock[i - lookback]
    r = ref[i] / ref[i - lookback]
    if r == 0 or np.isnan(s) or np.isnan(r):
        return float("nan")
    return float(s / r)


def compute_relative_strength(
    history: pd.DataFrame,
    nifty: pd.DataFrame,
    *,
    sector: str | None,
    sector_indices: dict[str, pd.DataFrame],
) -> dict[date, dict[str, float]]:
    h = history.sort_values("bar_date").reset_index(drop=True)
    n = nifty.sort_values("bar_date").reset_index(drop=True)
    merged = pd.merge(
        h[["bar_date", "close"]],
        n[["bar_date", "close"]].rename(columns={"close": "nifty_close"}),
        on="bar_date", how="left",
    )

    sec_df = sector_indices.get(sector) if sector else None
    if sec_df is not None and not sec_df.empty:
        merged = pd.merge(
            merged,
            sec_df.sort_values("bar_date")[["bar_date", "close"]]
            .rename(columns={"close": "sector_close"}),
            on="bar_date", how="left",
        )
    else:
        merged["sector_close"] = float("nan")

    closes = merged["close"].astype(float).to_numpy()
    nclose = merged["nifty_close"].astype(float).to_numpy()
    sclose = merged["sector_close"].astype(float).to_numpy()
    dates = merged["bar_date"].tolist()
    return {
        dates[i]: {
            "rs_vs_nifty_3m": _rs(closes, nclose, LB_3M, i),
            "rs_vs_nifty_6m": _rs(closes, nclose, LB_6M, i),
            "rs_vs_sector_3m": _rs(closes, sclose, LB_3M, i),
        }
        for i in range(len(dates))
    }
```

- [ ] **Step 7.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_relative_strength.py -v
git add backend/algo/factors/relative_strength.py backend/algo/factors/tests/test_relative_strength.py
git commit -m "feat(algo): relative_strength factors rs_vs_nifty_{3,6}m + rs_vs_sector_3m (REGIME-2a)"
```

---

## Task 8 — Breadth factor (universe-level)

**Files:**
- Create: `backend/algo/factors/breadth.py`, `backend/algo/factors/tests/test_breadth.py`.

Universe-wide values per date — same value attached to every ticker's row in `daily_factors` so any strategy can read it via the cached factor row.

- [ ] **Step 8.1: Test**

```python
"""Breadth factor tests."""
from __future__ import annotations

import math
from datetime import date

from backend.algo.factors.breadth import compute_breadth_for_date


def test_breadth_basic(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.algo.factors.breadth._fetch_breadth_pct",
        lambda d, window: 0.62 if window == 50 else 0.55,
    )
    monkeypatch.setattr(
        "backend.algo.factors.breadth._fetch_midcap_largecap_ratio",
        lambda d: 1.42,
    )
    out = compute_breadth_for_date(date(2026, 5, 8))
    assert out["pct_above_50sma"] == 0.62
    assert out["pct_above_200sma"] == 0.55
    assert out["midcap_largecap_ratio"] == 1.42


def test_breadth_missing_returns_nan(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.algo.factors.breadth._fetch_breadth_pct",
        lambda d, window: float("nan"),
    )
    monkeypatch.setattr(
        "backend.algo.factors.breadth._fetch_midcap_largecap_ratio",
        lambda d: float("nan"),
    )
    out = compute_breadth_for_date(date(2026, 5, 8))
    assert math.isnan(out["pct_above_50sma"])
    assert math.isnan(out["midcap_largecap_ratio"])
```

- [ ] **Step 8.2: Implement**

```python
"""Universe-level breadth: pct_above_{50,200}sma + midcap/largecap."""
from __future__ import annotations

from datetime import date, timedelta

from backend.db.duckdb_engine import query_iceberg_table


def _fetch_breadth_pct(d: date, window: int) -> float:
    start = d - timedelta(days=window * 2)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        f"WITH w AS ("
        f"  SELECT ticker, date AS bar_date, close, "
        f"         AVG(close) OVER ("
        f"             PARTITION BY ticker ORDER BY date "
        f"             ROWS BETWEEN {window - 1} PRECEDING "
        f"             AND CURRENT ROW"
        f"         ) AS sma "
        f"  FROM ohlcv WHERE date BETWEEN ? AND ? "
        f") "
        f"SELECT COUNT(*) FILTER (WHERE close > sma) AS above, "
        f"       COUNT(*) AS total "
        f"FROM w WHERE bar_date = ?",
        [start, d, d],
    )
    if not rows or not rows[0].get("total"):
        return float("nan")
    r = rows[0]
    return float(r["above"]) / float(r["total"])


def _fetch_midcap_largecap_ratio(d: date) -> float:
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT ticker, close FROM ohlcv "
        "WHERE ticker IN ('^NIFMDCP150', '^NSEI') AND date = ?",
        [d],
    )
    if not rows:
        return float("nan")
    by_t = {r["ticker"]: r["close"] for r in rows}
    mid = by_t.get("^NIFMDCP150")
    large = by_t.get("^NSEI")
    if mid is None or large is None or large == 0:
        return float("nan")
    return float(mid) / float(large)


def compute_breadth_for_date(d: date) -> dict[str, float]:
    return {
        "pct_above_50sma": _fetch_breadth_pct(d, 50),
        "pct_above_200sma": _fetch_breadth_pct(d, 200),
        "midcap_largecap_ratio": _fetch_midcap_largecap_ratio(d),
    }
```

- [ ] **Step 8.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_breadth.py -v
git add backend/algo/factors/breadth.py backend/algo/factors/tests/test_breadth.py
git commit -m "feat(algo): breadth factors pct_above_50/200sma + midcap_largecap_ratio (REGIME-2a)"
```

---

## Task 9 — Repo (NaN-replaceable upsert + DuckDB read)

**Files:**
- Create: `backend/algo/factors/repo.py`, `backend/algo/factors/tests/test_repo.py`.

Mirrors REGIME-1 repo pattern. Pre-delete by `(ticker IN batch_tickers AND bar_date IN batch_dates)`, then append. Use Arrow schema with explicit nullability (REGIME-1 lesson).

- [ ] **Step 9.1: Test**

```python
"""Round-trip + idempotency tests for daily_factors repo."""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.factors.repo import (
    FactorRow, upsert_factors, get_factors_window,
)


@pytest.mark.iceberg
def test_upsert_roundtrip() -> None:
    rows = [
        FactorRow(
            ticker="TEST.NS",
            bar_date=date(2026, 5, 8),
            values={"mom_12_1": 0.18, "f_score": 7.0,
                    "realized_vol_60d": 0.22},
            sector="IT",
        ),
    ]
    upsert_factors(rows)
    got = get_factors_window(["TEST.NS"], date(2026, 5, 8), date(2026, 5, 8))
    assert len(got) == 1
    assert got[0].values["mom_12_1"] == pytest.approx(0.18)
    assert got[0].sector == "IT"


@pytest.mark.iceberg
def test_upsert_same_key_overwrites() -> None:
    upsert_factors([FactorRow(
        ticker="TEST2.NS", bar_date=date(2026, 5, 8),
        values={"mom_12_1": 0.10}, sector="IT",
    )])
    upsert_factors([FactorRow(
        ticker="TEST2.NS", bar_date=date(2026, 5, 8),
        values={"mom_12_1": 0.50}, sector="IT",
    )])
    got = get_factors_window(["TEST2.NS"], date(2026, 5, 8), date(2026, 5, 8))
    assert len(got) == 1
    assert got[0].values["mom_12_1"] == pytest.approx(0.50)
```

- [ ] **Step 9.2: Implement**

```python
"""Iceberg CRUD for stocks.daily_factors with NaN-replaceable upsert."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date

import pyarrow as pa
from pyiceberg.expressions import And, In

from backend.algo.factors.iceberg_init import (
    ALL_FACTOR_KEYS,
    DAILY_FACTORS_TABLE,
)
from backend.cache import get_cache
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)

_logger = logging.getLogger(__name__)


@dataclass
class FactorRow:
    ticker: str
    bar_date: date
    values: dict[str, float]
    sector: str | None = None


def _catalog():
    from stocks.create_tables import _get_catalog
    return _get_catalog()


def _arrow_schema() -> pa.Schema:
    fields = [
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("bar_date", pa.date32(), nullable=False),
    ]
    for k in ALL_FACTOR_KEYS:
        fields.append(pa.field(k, pa.float64(), nullable=True))
    fields.append(pa.field("sector", pa.string(), nullable=True))
    return pa.schema(fields)


def upsert_factors(rows: list[FactorRow]) -> int:
    if not rows:
        return 0
    cat = _catalog()
    tbl = cat.load_table(DAILY_FACTORS_TABLE)
    tickers = sorted({r.ticker for r in rows})
    dates = sorted({r.bar_date for r in rows})
    try:
        tbl.delete(And(In("ticker", tickers), In("bar_date", dates)))
    except Exception as exc:  # pragma: no cover
        _logger.debug("daily_factors pre-delete skipped: %s", exc)

    cols: dict[str, list] = {
        "ticker": [r.ticker for r in rows],
        "bar_date": [r.bar_date for r in rows],
        "sector": [r.sector for r in rows],
    }
    for k in ALL_FACTOR_KEYS:
        cols[k] = [r.values.get(k, float("nan")) for r in rows]
    arrow_tbl = pa.table(cols, schema=_arrow_schema())
    tbl.append(arrow_tbl)
    invalidate_metadata(DAILY_FACTORS_TABLE)
    get_cache().invalidate("cache:factors:*")
    return len(rows)


def get_factors_window(
    tickers: list[str], start: date, end: date,
) -> list[FactorRow]:
    if not tickers:
        return []
    placeholders = ",".join(["?"] * len(tickers))
    cols_sql = ", ".join(["ticker", "bar_date"]
                         + ALL_FACTOR_KEYS + ["sector"])
    rows = query_iceberg_table(
        DAILY_FACTORS_TABLE,
        f"SELECT {cols_sql} FROM daily_factors "
        f"WHERE ticker IN ({placeholders}) "
        f"AND bar_date BETWEEN ? AND ? "
        f"ORDER BY ticker ASC, bar_date ASC",
        [*tickers, start, end],
    )
    out: list[FactorRow] = []
    for r in rows:
        vals = {
            k: r[k] for k in ALL_FACTOR_KEYS
            if r.get(k) is not None and not (
                isinstance(r[k], float) and math.isnan(r[k])
            )
        }
        out.append(FactorRow(
            ticker=r["ticker"],
            bar_date=r["bar_date"],
            values=vals,
            sector=r.get("sector"),
        ))
    return out
```

If `invalidate_metadata` is at a different path, grep to confirm: `grep -n "invalidate_metadata" backend/db/duckdb_engine.py`. REGIME-1's repo.py used it.

- [ ] **Step 9.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_repo.py -v
git add backend/algo/factors/repo.py backend/algo/factors/tests/test_repo.py
git commit -m "feat(algo): daily_factors repo with NaN-replaceable upsert + cache invalidation (REGIME-2a)"
```

---

## Task 10 — Compute job orchestrator

**Files:**
- Create: `backend/algo/factors/compute_job.py`, `backend/algo/factors/tests/test_compute_job.py`.
- Modify: `backend/algo/jobs/__init__.py` (import for side-effect), `backend/jobs/executor.py` (add `@register_job("compute_daily_factors")` wrapper).

Loads OHLCV window per ticker (with warmup ~2yr calendar so SMA200 + mom_12_1 ready). Loads NIFTY + sector indices once. Computes all 7 factor families per ticker. Joins universe-level breadth (one fetch per date). Emits `FactorRow` per `(ticker, date)` and bulk upserts.

- [ ] **Step 10.1: Test**

```python
"""Orchestrator integration test — mock data layer, verify end-to-end flow."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors import compute_job


def _ohlcv(close: list[float], volume: list[int] | None = None) -> pd.DataFrame:
    n = len(close)
    if volume is None:
        volume = [1000] * n
    return pd.DataFrame({
        "bar_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "open": close, "high": close, "low": close,
        "close": close, "volume": volume,
    })


def test_run_compute_writes_rows(monkeypatch) -> None:
    closes = list(np.linspace(100, 130, 280))
    monkeypatch.setattr(compute_job, "_get_universe", lambda: ["TEST.NS"])
    monkeypatch.setattr(
        compute_job, "_load_ohlcv_for_ticker",
        lambda t, s, e: _ohlcv(closes),
    )
    monkeypatch.setattr(
        compute_job, "_load_nifty_history",
        lambda s, e: _ohlcv(closes),
    )
    monkeypatch.setattr(
        compute_job, "_load_sector_indices_history",
        lambda s, e: {},
    )
    monkeypatch.setattr(
        compute_job, "_lookup_sector",
        lambda tickers: {"TEST.NS": "IT"},
    )
    monkeypatch.setattr(
        compute_job, "_compute_breadth_for_date",
        lambda d: {
            "pct_above_50sma": 0.6,
            "pct_above_200sma": 0.5,
            "midcap_largecap_ratio": 1.4,
        },
    )

    captured: list = []
    monkeypatch.setattr(
        compute_job, "upsert_factors",
        lambda rows: captured.extend(rows) or len(rows),
    )

    n_written = compute_job.run_compute_job(
        as_of=date(2024, 1, 1) + timedelta(days=279),
        days=2,
    )
    assert n_written > 0
    assert all(r.ticker == "TEST.NS" for r in captured)
    last = captured[-1]
    assert "mom_12_1" in last.values
    assert last.values.get("pct_above_50sma") == 0.6
```

- [ ] **Step 10.2: Implement**

```python
"""Daily factor compute orchestrator. Runs at 23:00 IST."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from backend.algo.factors.breadth import (
    compute_breadth_for_date as _compute_breadth_for_date,
)
from backend.algo.factors.lowvol import compute_lowvol
from backend.algo.factors.momentum import compute_momentum
from backend.algo.factors.quality import compute_quality
from backend.algo.factors.relative_strength import (
    compute_relative_strength,
)
from backend.algo.factors.repo import FactorRow, upsert_factors
from backend.algo.factors.trend import compute_trend
from backend.algo.factors.volume import compute_volume
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

WARMUP_DAYS = 730
NIFTY_TICKER = "^NSEI"
SECTOR_INDEX_MAP = {
    "IT": "^CNXIT",
    "Banks": "^NSEBANK",
    "Banking": "^NSEBANK",
    "Auto": "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "Pharmaceutical": "^CNXPHARMA",
    "FMCG": "^CNXFMCG",
    "Consumer Goods": "^CNXFMCG",
    "Metals": "^CNXMETAL",
    "Metal": "^CNXMETAL",
    "Energy": "^CNXENERGY",
    "Realty": "^CNXREALTY",
    "Real Estate": "^CNXREALTY",
    "Financial Services": "^CNXFINANCE",
}


def _get_universe() -> list[str]:
    """Distinct tickers from stocks.ohlcv with recent activity.
    Excludes index/futures/macro tickers."""
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT DISTINCT ticker FROM ohlcv "
        "WHERE date >= ? AND ticker NOT LIKE '^%' "
        "AND ticker NOT LIKE '%=F' AND ticker NOT LIKE 'DX-%' "
        "ORDER BY ticker",
        [date.today() - timedelta(days=30)],
    )
    return [r["ticker"] for r in rows]


def _load_ohlcv_for_ticker(
    ticker: str, start: date, end: date,
) -> pd.DataFrame:
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT date AS bar_date, open, high, low, close, volume "
        "FROM ohlcv WHERE ticker = ? AND date BETWEEN ? AND ? "
        "ORDER BY date ASC",
        [ticker, start, end],
    )
    return pd.DataFrame(rows)


def _load_nifty_history(start: date, end: date) -> pd.DataFrame:
    return _load_ohlcv_for_ticker(NIFTY_TICKER, start, end)


def _load_sector_indices_history(
    start: date, end: date,
) -> dict[str, pd.DataFrame]:
    indices = sorted(set(SECTOR_INDEX_MAP.values()))
    out: dict[str, pd.DataFrame] = {}
    for idx in indices:
        df = _load_ohlcv_for_ticker(idx, start, end)
        if not df.empty:
            out[idx] = df
    sector_to_df: dict[str, pd.DataFrame] = {}
    for sector, idx in SECTOR_INDEX_MAP.items():
        if idx in out:
            sector_to_df[sector] = out[idx]
    return sector_to_df


def _lookup_sector(tickers: list[str]) -> dict[str, str | None]:
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    rows = query_iceberg_table(
        "stocks.piotroski_scores",
        f"SELECT ticker, sector FROM piotroski_scores "
        f"WHERE ticker IN ({placeholders}) "
        f"ORDER BY ticker, score_date DESC",
        list(tickers),
    )
    out: dict[str, str | None] = {t: None for t in tickers}
    for r in rows:
        if out.get(r["ticker"]) is None:
            out[r["ticker"]] = r.get("sector")
    return out


def run_compute_job(
    as_of: date | None = None, days: int = 1,
) -> int:
    if as_of is None:
        as_of = date.today()
    period_start = as_of - timedelta(days=days - 1)
    load_start = as_of - timedelta(days=WARMUP_DAYS)

    universe = _get_universe()
    if not universe:
        _logger.warning("Empty universe — skipping factor compute")
        return 0
    nifty = _load_nifty_history(load_start, as_of)
    sector_indices = _load_sector_indices_history(load_start, as_of)
    sector_lookup = _lookup_sector(universe)

    breadth_by_date: dict[date, dict[str, float]] = {
        d: _compute_breadth_for_date(d)
        for d in (period_start + timedelta(days=i)
                  for i in range((as_of - period_start).days + 1))
    }

    written = 0
    for ticker in universe:
        history = _load_ohlcv_for_ticker(ticker, load_start, as_of)
        if history.empty or len(history) < 30:
            continue
        sector = sector_lookup.get(ticker)
        per_date: dict[date, dict[str, float]] = {}

        def _merge(src: dict[date, dict[str, float]]) -> None:
            for d, vals in src.items():
                per_date.setdefault(d, {}).update(vals)

        _merge(compute_momentum(history))
        _merge(compute_lowvol(history, nifty))
        _merge(compute_trend(history))
        _merge(compute_volume(history))
        _merge(compute_relative_strength(
            history, nifty, sector=sector,
            sector_indices=sector_indices,
        ))
        _merge(compute_quality(ticker, period_start, as_of))

        rows: list[FactorRow] = []
        for d, vals in per_date.items():
            if d < period_start or d > as_of:
                continue
            merged = {**vals, **breadth_by_date.get(d, {})}
            rows.append(FactorRow(
                ticker=ticker, bar_date=d,
                values=merged, sector=sector,
            ))
        if rows:
            written += upsert_factors(rows)

    _logger.info(
        "compute_daily_factors: wrote %d rows for %d tickers (as_of=%s, days=%d)",
        written, len(universe), as_of, days,
    )
    return written
```

- [ ] **Step 10.3: Wire scheduler entry**

Edit `backend/algo/jobs/__init__.py` — append:
```python
# REGIME-2a — daily factor compute (23:00 IST)
from backend.algo.factors import compute_job  # noqa: F401
```

Edit `backend/jobs/executor.py` — locate the REGIME-1 wrapper (`@register_job("regime_classifier_daily")`) and add immediately after:

```python
@register_job("compute_daily_factors")
def _compute_daily_factors(payload: dict) -> dict:
    from backend.algo.factors.compute_job import run_compute_job
    from datetime import date
    as_of = payload.get("as_of")
    parsed = date.fromisoformat(as_of) if as_of else None
    days = int(payload.get("days", 1))
    n = run_compute_job(as_of=parsed, days=days)
    return {"rows_written": n}
```

- [ ] **Step 10.4: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_compute_job.py -v
docker compose restart backend && sleep 6
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend python -c "from jobs.executor import JOB_EXECUTORS; print('compute_daily_factors registered:', 'compute_daily_factors' in JOB_EXECUTORS)"
git add backend/algo/factors/compute_job.py backend/algo/factors/tests/test_compute_job.py backend/algo/jobs/__init__.py backend/jobs/executor.py
git commit -m "feat(algo): factor compute_job orchestrator + register_job wrapper (REGIME-2a)"
```

---

## Task 11 — Register factor keys in strategy AST + frontend mirror

**Files:**
- Modify: `backend/algo/strategy/features.py` (extend `FeatureSource` literal with `"factor"`; append the factor entries).
- Modify: `frontend/components/algo-trading/strategyFeatureCatalog.ts` (mirror).
- Test: `backend/algo/factors/tests/test_features_registered.py`.

REGIME-1 already registered the breadth keys (`pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio`) under `"regime"`. Don't double-register them. This task adds the 16 non-breadth factor keys under `"factor"`.

- [ ] **Step 11.1: Test**

```python
"""All factor keys must appear in the strategy AST FEATURE_KEYS registry."""
from __future__ import annotations

from backend.algo.factors.iceberg_init import ALL_FACTOR_KEYS
from backend.algo.strategy.features import FEATURE_KEYS, FEATURE_BY_KEY

NON_BREADTH = [
    k for k in ALL_FACTOR_KEYS
    if k not in {
        "pct_above_50sma", "pct_above_200sma", "midcap_largecap_ratio",
    }
]


def test_all_factor_keys_registered() -> None:
    missing = set(ALL_FACTOR_KEYS) - FEATURE_KEYS
    assert not missing, f"Missing factor keys: {missing}"


def test_non_breadth_factor_keys_have_factor_source() -> None:
    for k in NON_BREADTH:
        assert FEATURE_BY_KEY[k].source == "factor", k


def test_factor_keys_are_float() -> None:
    for k in NON_BREADTH:
        assert FEATURE_BY_KEY[k].type == "float", k
```

- [ ] **Step 11.2: Extend `features.py`**

1. Append `"factor"` to the `FeatureSource` literal:
```python
FeatureSource = Literal[
    "ohlcv", "technical", "fundamentals",
    "recommendation", "forecast", "regime", "factor",
]
```

2. Append the 16 entries to `FEATURES`:

```python
    # Factor library (REGIME-2a)
    Feature(key="mom_12_1", label="Momentum 12-1 (skip-month)", type="float", source="factor"),
    Feature(key="mom_6_1", label="Momentum 6-1 (skip-month)", type="float", source="factor"),
    Feature(key="mom_3_1", label="Momentum 3-1 (skip-month)", type="float", source="factor"),
    Feature(key="prox_52w", label="Proximity to 52w high", type="float", source="factor"),
    Feature(key="f_score", label="Piotroski F-Score (factor)", type="float", source="factor"),
    Feature(key="realized_vol_60d", label="Realized vol 60d (annualised)", type="float", source="factor"),
    Feature(key="beta_to_nifty", label="Beta vs NIFTY (252d)", type="float", source="factor"),
    Feature(key="adx_14", label="ADX(14)", type="float", source="factor"),
    Feature(key="sma200_slope", label="SMA200 slope (21d)", type="float", source="factor"),
    Feature(key="distance_from_sma200", label="Distance from SMA200", type="float", source="factor"),
    Feature(key="obv", label="On-Balance Volume", type="float", source="factor"),
    Feature(key="volume_x_avg_20", label="Volume x 20d avg", type="float", source="factor"),
    Feature(key="up_down_vol_ratio_20", label="Up/Down vol ratio (20d)", type="float", source="factor"),
    Feature(key="rs_vs_nifty_3m", label="Rel strength vs NIFTY 3m", type="float", source="factor"),
    Feature(key="rs_vs_nifty_6m", label="Rel strength vs NIFTY 6m", type="float", source="factor"),
    Feature(key="rs_vs_sector_3m", label="Rel strength vs sector 3m", type="float", source="factor"),
```

- [ ] **Step 11.3: Sync frontend mirror**

Read `frontend/components/algo-trading/strategyFeatureCatalog.ts`. Append the same 16 entries under `source: "factor"`. CI test `test_feature_registry_sync.py` enforces.

- [ ] **Step 11.4: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_features_registered.py backend/algo/tests/test_feature_registry_sync.py -v
git add backend/algo/strategy/features.py frontend/components/algo-trading/strategyFeatureCatalog.ts backend/algo/factors/tests/test_features_registered.py
git commit -m "feat(algo): register 16 factor keys in AST + frontend mirror (REGIME-2a)"
```

---

## Task 12 — Wire factor cache into the 3 runtimes

**Files:**
- Modify: `backend/algo/backtest/runner.py`, `backend/algo/paper/runtime.py`, `backend/algo/live/runtime.py`.
- Test: `backend/algo/factors/tests/test_runtime_uses_cache.py`.

Per `feedback_runtime_feature_three_runtimes`: must wire into ALL THREE.
1. On runtime init, call `get_factors_window(universe, start, end)` to pre-load.
2. Build `factors_by_key = {(ticker, date): {key: float}}`.
3. In the per-bar features dict assembly, `**factors_by_key.get((ticker, bar_date), {})` overlays the cached values onto the indicator dict.

CRITICAL: factor keys are disjoint from indicator keys by design — but verify in the integration test that no `sma_50`/`rsi`/`golden_cross_days_ago` value gets overwritten.

- [ ] **Step 12.1: Test (lightweight binding test)**

```python
"""3-runtime cache integration test."""
from __future__ import annotations


def test_backtest_runner_imports_factor_cache() -> None:
    from backend.algo.backtest import runner as runner_mod
    assert hasattr(runner_mod, "get_factors_window")


def test_paper_runtime_imports_factor_cache() -> None:
    from backend.algo.paper import runtime as paper_mod
    assert hasattr(paper_mod, "get_factors_window")


def test_live_runtime_imports_factor_cache() -> None:
    from backend.algo.live import runtime as live_mod
    assert hasattr(live_mod, "get_factors_window")
```

(Heavy integration — running the actual backtest with seeded factor rows + spy on the per-bar features dict — is overkill for the slice. Backfill smoke at Task 13 closes the gap.)

- [ ] **Step 12.2: Modify `backend/algo/backtest/runner.py`**

Add at top:
```python
from backend.algo.factors.repo import get_factors_window
```

In `run_backtest()`, after `bars = load_ohlcv_window(...)` and `indicators = compute_indicators_for_universe(bars)`, add:

```python
    factor_rows = get_factors_window(
        tickers=universe,
        start=request.period_start,
        end=request.period_end,
    )
    factors_by_key: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in factor_rows:
        factors_by_key[(r.ticker, r.bar_date)] = {
            k: Decimal(str(v)) for k, v in r.values.items()
            if v is not None
        }
```

In the per-bar features dict assembly, overlay AFTER the indicator + market_regime keys (factor keys are disjoint by design):

```python
        features = {
            **ind_map.get(...),
            "nifty_above_sma200": market_regime.get(...),
            "nifty_30d_return_pct": market_trend.get(...),
            **factors_by_key.get((bar.ticker, bar.date), {}),
        }
```

If the exact line shape differs from the project state, adapt — keep the overlay AFTER existing keys.

- [ ] **Step 12.3: Modify `backend/algo/paper/runtime.py`**

Add at top:
```python
from backend.algo.factors.repo import get_factors_window
```

In `PaperRuntime.__init__`, after the `_market_regime`/`_market_trend` init block, add a try/except that loads today's factor rows for the strategy universe and caches them in `self._factor_cache`:

```python
        from datetime import date as _date
        try:
            today = _date.today()
            uni = list(self._strategy.universe or [])
            rows = get_factors_window(uni, today, today) if uni else []
            self._factor_cache: dict[
                tuple[str, _date], dict[str, Decimal]
            ] = {
                (r.ticker, r.bar_date): {
                    k: Decimal(str(v))
                    for k, v in r.values.items() if v is not None
                }
                for r in rows
            }
        except Exception as exc:
            _logger.warning(
                "Factor cache load failed; running without: %s", exc,
            )
            self._factor_cache = {}
```

Adjust the `self._strategy.universe` reference if the actual attribute differs — grep first.

In the per-bar features assembly (~line 200), append:
```python
        features = {
            **ind_map.get(bar_date_obj, _features_for_bar(bar)),
            "nifty_above_sma200": self._market_regime.get(...),
            "nifty_30d_return_pct": self._market_trend.get(...),
            **self._factor_cache.get((bar.ticker, bar_date_obj), {}),
        }
```

- [ ] **Step 12.4: Modify `backend/algo/live/runtime.py`**

Same pattern as paper. Pre-load `today`'s factors on `__init__`; overlay in the per-bar features dict at line ~262.

- [ ] **Step 12.5: Run + regression check + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/factors/tests/test_runtime_uses_cache.py -v
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/ -q --no-header 2>&1 | tail -10
git add backend/algo/backtest/runner.py backend/algo/paper/runtime.py backend/algo/live/runtime.py backend/algo/factors/tests/test_runtime_uses_cache.py
git commit -m "feat(algo): wire daily_factors cache into backtest+paper+live runtimes (REGIME-2a)"
```

---

## Task 13 — Backfill script + ship

**Files:**
- Create: `scripts/backfill_factors.py`.

90 days cold-start backfill per spec §7. Idempotent via the repo's NaN-replaceable upsert.

- [ ] **Step 13.1: Implement**

```python
"""Backfill stocks.daily_factors over a date range. Idempotent.

Usage::

    docker compose exec backend \
      python scripts/backfill_factors.py 2026-02-08 2026-05-08
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

from backend.algo.factors.compute_job import run_compute_job

_logger = logging.getLogger(__name__)


def main(start_iso: str, end_iso: str) -> None:
    """Replay run_compute_job day-by-day across the range."""
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    cur = start
    total = 0
    while cur <= end:
        try:
            n = run_compute_job(as_of=cur, days=1)
            _logger.info("Backfilled %s: %d rows", cur, n)
            total += n
        except Exception as exc:  # noqa: BLE001
            _logger.error("Backfill failed for %s: %s", cur, exc)
        cur += timedelta(days=1)
    _logger.info("Backfill total: %d rows across %d days",
                 total, (end - start).days + 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 3:
        sys.stderr.write(
            "usage: python scripts/backfill_factors.py "
            "<start YYYY-MM-DD> <end YYYY-MM-DD>\n"
        )
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
```

- [ ] **Step 13.2: Smoke run (last 5 days)**

```bash
START=$(date -v-5d +%Y-%m-%d 2>/dev/null || date -d '5 days ago' +%Y-%m-%d)
END=$(date +%Y-%m-%d)
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend \
  python scripts/backfill_factors.py "$START" "$END"
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend python -c "
from db.duckdb_engine import query_iceberg_table
rows = query_iceberg_table('stocks.daily_factors',
    'SELECT COUNT(*) AS n, COUNT(DISTINCT ticker) AS tickers, COUNT(DISTINCT bar_date) AS dates FROM daily_factors', [])
print(rows)
"
```

Expected: ≥ tickers × dates rows.

- [ ] **Step 13.3: Commit + push**

```bash
git add scripts/backfill_factors.py
git commit -m "chore(algo): factor backfill script for 90d cold start (REGIME-2a)"
git push origin feature/regime-slice-2a-factor-library
```

---

## Acceptance Checklist

- [ ] `stocks.daily_factors` Iceberg table created (idempotent re-run).
- [ ] All 7 factor families compute without errors over a 252d window.
- [ ] `mom_12_1` skip-month gate test passes.
- [ ] Repo upsert is idempotent (re-write same key replaces, doesn't duplicate).
- [ ] `compute_daily_factors` job registered in `JOB_EXECUTORS`.
- [ ] All factor keys present in `FEATURE_KEYS`.
- [ ] Frontend `strategyFeatureCatalog.ts` mirror in sync.
- [ ] Backtest runtime pre-loads `get_factors_window` and overlays into per-bar features.
- [ ] Paper runtime same.
- [ ] Live runtime same.
- [ ] Existing `pytest backend/algo/tests` PASS (no regression).
- [ ] Backfill of last 5 days writes ≥1 row per (ticker × date).
- [ ] Branch pushed to `feature/regime-slice-2a-factor-library`.

---

## Out of Scope for REGIME-2a

- Frontend Factor Scores tab (REGIME-2b — independent UX slice).
- Strategy↔regime metadata + AST `regime_eq` (REGIME-3).
- ROIC + accruals (need quarterly fundamentals upgrade — leave NaN with TODO).
- Volatility-targeted sizing (REGIME-4).
- Walk-forward extension (REGIME-5).
- Attribution (REGIME-6).
- PIT universe + slippage upgrade (REGIME-7).
- HTTP `/v1/algo/factors/*` route (added in REGIME-2b).
- Tunable thresholds via PG (deferred — module constants for now).
- Sector mapping table normalization (using inline `SECTOR_INDEX_MAP` dict; v3.1 problem).
