# Intraday 15m MIS Bake-Off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-shot XGBoost + SHAP feature-importance bake-off over 15-min F&O 200 bars that produces a ranked feature list and a draft AST for a follow-up MIS long+short strategy spec.

**Architecture:** Read-only research subtree at `backend/algo/research/intraday_15m_mis_bakeoff/`. Pulls features from `stocks.intraday_features` via DuckDB, pivots EAV → wide, labels 3 classes from vol-normalized 4-bar forward returns, trains a 3-class XGBoost classifier with 5-seed ranking-stability validation, computes per-class tree-SHAP, and writes a Markdown report + PNGs + reproducibility ledger to `~/.ai-agent-ui/research_runs/<date>/`. No Iceberg writes, no scheduler integration, no UI.

**Tech Stack:** Python 3.12 · pandas · pyarrow · DuckDB (in-process Iceberg reader) · `xgboost==2.x` · `shap` · `matplotlib` · pytest. Spec: `docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md`.

---

## File Structure

| Path | Purpose | Approx LOC |
|---|---|---|
| `backend/algo/research/__init__.py` | Package marker | 1 |
| `backend/algo/research/_shared/__init__.py` | Subpackage marker | 1 |
| `backend/algo/research/_shared/time_split.py` | Strict no-shuffle chronological CV helper | ~40 |
| `backend/algo/research/intraday_15m_mis_bakeoff/__init__.py` | Subpackage marker + version constant | ~5 |
| `backend/algo/research/intraday_15m_mis_bakeoff/README.md` | Run instructions, last-run pointer | ~50 |
| `backend/algo/research/intraday_15m_mis_bakeoff/fno_200.csv` | Static NSE F&O ticker list | ~200 rows |
| `backend/algo/research/intraday_15m_mis_bakeoff/universe.py` | F&O 200 CSV loader | ~40 |
| `backend/algo/research/intraday_15m_mis_bakeoff/labeler.py` | Pure vol-normalized 3-class label function | ~80 |
| `backend/algo/research/intraday_15m_mis_bakeoff/dataset.py` | DuckDB query + EAV pivot + label join | ~180 |
| `backend/algo/research/intraday_15m_mis_bakeoff/shap_eval.py` | SHAP per-class aggregation + asymmetry detection | ~120 |
| `backend/algo/research/intraday_15m_mis_bakeoff/report.py` | Markdown + PNG generation | ~200 |
| `backend/algo/research/intraday_15m_mis_bakeoff/train.py` | XGB training, gate orchestration, CLI entrypoint | ~300 |
| `backend/algo/research/intraday_15m_mis_bakeoff/tests/__init__.py` | Test package marker | 1 |
| `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_time_split.py` | Pure CV helper tests | ~60 |
| `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_labeler.py` | Hand-built bar-sequence label tests | ~150 |
| `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_dataset_shape.py` | 50-row pyarrow fixture EAV-pivot test | ~120 |
| `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_gate6_harness.py` | Synthetic-data harness self-test | ~100 |
| `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_shap_eval.py` | Frozen SHAP-output fixture aggregation test | ~80 |
| `backend/algo/research/intraday_15m_mis_bakeoff/tests/fixtures/__init__.py` | Fixtures marker | 1 |

Working branch: `research/intraday-15m-mis-bakeoff-spec` (spec already committed). All implementation tasks land additional commits on this branch; final PR squash-merges to `dev` per `reference_git_merge_policy`.

---

## Task 1: Package scaffold + universe loader

**Files:**
- Create: `backend/algo/research/__init__.py`
- Create: `backend/algo/research/_shared/__init__.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/__init__.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/tests/__init__.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/tests/fixtures/__init__.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/universe.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/fno_200.csv`

- [ ] **Step 1: Generate the F&O 200 CSV from `algo.instruments`**

The instruments table has the live NSE F&O snapshot. One-time SQL query into CSV:

Run:
```bash
docker compose exec -T backend python -c "
from backend.db.duckdb_engine import query_iceberg_df
df = query_iceberg_df('algo.instruments',
    \"SELECT DISTINCT tradingsymbol AS ticker FROM instruments \"
    \"WHERE segment IN ('NFO-FUT', 'NFO-OPT') \"
    \"AND name IS NOT NULL AND name != '' \"
    \"ORDER BY tradingsymbol\")
# Take the underlying equity name (strip month/strike suffixes
# from F&O symbols). Equity tickers in NSE have '.NS' suffix
# in our universe.
df['equity'] = df['ticker'].str.extract(r'^([A-Z&]+)')[0] + '.NS'
out = df[['equity']].drop_duplicates().rename(columns={'equity': 'ticker'})
out = out[out['ticker'].str.match(r'^[A-Z&]{2,15}\.NS$')]
print(f'{len(out)} F&O underlyings')
out.to_csv('/app/backend/algo/research/intraday_15m_mis_bakeoff/fno_200.csv', index=False)
"
```

Expected: `~200 F&O underlyings`. CSV format: single column `ticker`, one row per underlying.

If the extraction regex picks up garbage (e.g. index symbols `NIFTY.NS` that aren't tradable underlyings), manually drop those rows before continuing — open the CSV in any editor and remove obvious indices (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY).

- [ ] **Step 2: Write `universe.py`**

```python
"""F&O 200 universe loader for the intraday 15m MIS bake-off."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pandas as pd


def load_fno_universe() -> list[str]:
    """Return the F&O ticker list as a sorted list of strings.

    Sourced from the static ``fno_200.csv`` packaged with this
    module. Refresh quarterly when NSE updates the F&O list.
    """
    csv_path = Path(__file__).parent / "fno_200.csv"
    df = pd.read_csv(csv_path)
    tickers = sorted(df["ticker"].dropna().unique().tolist())
    return tickers


def fno_universe_checksum() -> str:
    """SHA-256 of the F&O CSV — stamped in run_metadata.json."""
    import hashlib

    csv_path = Path(__file__).parent / "fno_200.csv"
    return hashlib.sha256(csv_path.read_bytes()).hexdigest()
```

- [ ] **Step 3: Write package `__init__` files (empty markers + version)**

`backend/algo/research/__init__.py`:
```python
"""Research subtree — one-shot exploratory analyses.

Read-only with respect to Iceberg / PG / Redis. Outputs land
in ``~/.ai-agent-ui/research_runs/<date>/``.
"""
```

`backend/algo/research/_shared/__init__.py`:
```python
"""Shared utilities across research projects."""
```

`backend/algo/research/intraday_15m_mis_bakeoff/__init__.py`:
```python
"""Intraday 15m MIS feature-importance bake-off.

See ``docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md``.
"""

VERSION = "0.1.0"
```

`backend/algo/research/intraday_15m_mis_bakeoff/tests/__init__.py`:
```python
"""Tests for the intraday 15m MIS bake-off."""
```

`backend/algo/research/intraday_15m_mis_bakeoff/tests/fixtures/__init__.py`:
```python
"""Fixtures for bake-off tests."""
```

- [ ] **Step 4: Sanity-check the loader**

Run:
```bash
docker compose exec -T backend python -c "
from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
    load_fno_universe, fno_universe_checksum
)
tickers = load_fno_universe()
print(f'count={len(tickers)} checksum={fno_universe_checksum()[:16]}...')
print('first 5:', tickers[:5])
"
```

Expected: `count` between 180 and 220, all tickers match `^[A-Z&]+\.NS$`.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/
git commit -m "feat(research): F&O 200 universe loader + package scaffold"
```

---

## Task 2: Time-split helper + tests (TDD)

**Files:**
- Create: `backend/algo/research/_shared/time_split.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_time_split.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/tests/test_time_split.py
"""Tests for the strict-chronological train/val/test split."""

from datetime import date

import pandas as pd
import pytest

from backend.algo.research._shared.time_split import (
    chronological_split,
    assert_chronological,
)


def _make_frame(start: str, end: str) -> pd.DataFrame:
    """One row per business day with a bar_date column."""
    dates = pd.bdate_range(start, end)
    return pd.DataFrame({"bar_date": dates.date, "x": range(len(dates))})


def test_split_returns_three_disjoint_chronological_frames():
    df = _make_frame("2025-11-17", "2026-05-21")
    train_fit, train_val, test = chronological_split(
        df,
        date_col="bar_date",
        train_fit_end=date(2026, 2, 8),
        train_val_end=date(2026, 2, 28),
    )

    assert train_fit["bar_date"].max() <= date(2026, 2, 8)
    assert train_val["bar_date"].min() >  date(2026, 2, 8)
    assert train_val["bar_date"].max() <= date(2026, 2, 28)
    assert test["bar_date"].min()      >  date(2026, 2, 28)

    # disjointness — no row appears in two splits
    total = len(train_fit) + len(train_val) + len(test)
    assert total == len(df)


def test_split_raises_when_input_unsorted():
    df = _make_frame("2025-11-17", "2026-05-21").sample(frac=1, random_state=0)
    with pytest.raises(ValueError, match="must be sorted"):
        chronological_split(
            df,
            date_col="bar_date",
            train_fit_end=date(2026, 2, 8),
            train_val_end=date(2026, 2, 28),
        )


def test_assert_chronological_passes_on_disjoint_ordered_frames():
    df = _make_frame("2025-11-17", "2026-05-21")
    train_fit, train_val, test = chronological_split(
        df,
        date_col="bar_date",
        train_fit_end=date(2026, 2, 8),
        train_val_end=date(2026, 2, 28),
    )
    # Should not raise
    assert_chronological(train_fit, train_val, test, date_col="bar_date")


def test_assert_chronological_raises_on_overlap():
    df = _make_frame("2025-11-17", "2026-05-21")
    train_fit = df[df["bar_date"] <= date(2026, 2, 28)]
    train_val = df[(df["bar_date"] >= date(2026, 2, 1))
                   & (df["bar_date"] <= date(2026, 2, 28))]
    test = df[df["bar_date"] > date(2026, 2, 28)]
    with pytest.raises(AssertionError):
        assert_chronological(train_fit, train_val, test, date_col="bar_date")
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_time_split.py -v
```

Expected: `ImportError: cannot import name 'chronological_split'`.

- [ ] **Step 3: Write `time_split.py`**

```python
# backend/algo/research/_shared/time_split.py
"""Strict-chronological train/val/test splitter for time-series data.

No shuffling, no group-K-fold across tickers. Time generalization
only — see spec §4.5.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def chronological_split(
    df: pd.DataFrame,
    *,
    date_col: str,
    train_fit_end: date,
    train_val_end: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split *df* into (train_fit, train_val, test) by *date_col*.

    Args:
        df: Frame to split. MUST be sorted ascending on
            ``date_col`` — we enforce this rather than re-sort
            so the caller stays honest about input ordering.
        date_col: Name of the date / timestamp column to split on.
        train_fit_end: Last date (inclusive) for the training fold.
        train_val_end: Last date (inclusive) for the early-stopping
            validation fold; everything strictly after lands in test.

    Returns:
        ``(train_fit, train_val, test)`` — strictly disjoint and
        chronologically ordered.
    """
    if not df[date_col].is_monotonic_increasing:
        raise ValueError(f"{date_col} must be sorted ascending")

    train_fit = df[df[date_col] <= train_fit_end].copy()
    train_val = df[(df[date_col] > train_fit_end)
                   & (df[date_col] <= train_val_end)].copy()
    test = df[df[date_col] > train_val_end].copy()
    return train_fit, train_val, test


def assert_chronological(
    train_fit: pd.DataFrame,
    train_val: pd.DataFrame,
    test: pd.DataFrame,
    *,
    date_col: str,
) -> None:
    """Hard-fail Gate 1 — see spec §7.1.

    Raises:
        AssertionError: if any split's max date is not strictly
            less than the next split's min date.
    """
    assert train_fit[date_col].max() < train_val[date_col].min(), (
        f"train_fit max {train_fit[date_col].max()} >= "
        f"train_val min {train_val[date_col].min()}"
    )
    assert train_val[date_col].max() < test[date_col].min(), (
        f"train_val max {train_val[date_col].max()} >= "
        f"test min {test[date_col].min()}"
    )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_time_split.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/_shared/time_split.py \
        backend/algo/research/intraday_15m_mis_bakeoff/tests/test_time_split.py
git commit -m "feat(research): strict-chronological time-split helper"
```

---

## Task 3: Vol-normalized 3-class labeler + tests (TDD)

**Files:**
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/labeler.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_labeler.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/tests/test_labeler.py
"""Pure-function tests for the vol-normalized 3-class labeler."""

import math

import numpy as np
import pandas as pd
import pytest

from backend.algo.research.intraday_15m_mis_bakeoff.labeler import (
    LABEL_FLAT,
    LABEL_LONG,
    LABEL_SHORT,
    label_bars,
)


def _bars(closes: list[float], opens: list[float] | None = None,
          atr_pct: float = 0.01) -> pd.DataFrame:
    """One ticker, sequential 15-min bars, controllable ATR."""
    opens = opens or closes
    n = len(closes)
    return pd.DataFrame({
        "ticker": ["T"] * n,
        "bar_open_ts_ns": list(range(n)),
        "bar_date": [pd.Timestamp("2026-01-01").date()] * n,
        "open":  opens,
        "close": closes,
        "atr_14": [c * atr_pct for c in closes],
    })


def test_long_when_forward_return_above_half_sigma():
    # entry at t+1 open = 100, exit at t+4 close = 101.0
    # r_fwd = +1.0%, atr_ret = 1.0%, r_norm = +1.0 >= +0.5 → LONG
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    out = label_bars(bars, threshold=0.5)
    assert out.iloc[0]["label"] == LABEL_LONG


def test_short_when_forward_return_below_minus_half_sigma():
    bars = _bars(closes=[100, 100, 100, 100, 99.0, 99.0])
    out = label_bars(bars, threshold=0.5)
    assert out.iloc[0]["label"] == LABEL_SHORT


def test_flat_when_forward_return_in_band():
    # ±0.3% return on ±1% ATR → r_norm ±0.3 → FLAT
    bars = _bars(closes=[100, 100, 100, 100, 100.3, 100.3])
    out = label_bars(bars, threshold=0.5)
    assert out.iloc[0]["label"] == LABEL_FLAT


def test_boundary_at_exactly_plus_half_sigma_is_long():
    # Inclusive at the upper bound, exclusive between.
    bars = _bars(closes=[100, 100, 100, 100, 100.5, 100.5])
    out = label_bars(bars, threshold=0.5)
    assert out.iloc[0]["label"] == LABEL_LONG


def test_nan_atr_skips_row():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    bars.loc[0, "atr_14"] = float("nan")
    out = label_bars(bars, threshold=0.5)
    # Bar t=0 cannot be labelled — dropped from output.
    assert (out["bar_open_ts_ns"] == 0).sum() == 0


def test_zero_atr_skips_row():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    bars.loc[0, "atr_14"] = 0.0
    out = label_bars(bars, threshold=0.5)
    assert (out["bar_open_ts_ns"] == 0).sum() == 0


def test_negative_price_raises():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    bars.loc[0, "close"] = -1.0
    with pytest.raises(ValueError, match="non-positive"):
        label_bars(bars, threshold=0.5)


def test_label_window_crossing_date_boundary_is_dropped():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    # Bar t=0 needs t+1..t+4 = bars 1..4. Put a date break at t=3.
    bars.loc[3:, "bar_date"] = pd.Timestamp("2026-01-02").date()
    out = label_bars(bars, threshold=0.5)
    # t=0 would have its label window span the boundary → dropped.
    assert (out["bar_open_ts_ns"] == 0).sum() == 0


def test_no_label_when_fewer_than_5_forward_bars():
    bars = _bars(closes=[100, 100, 100, 100])      # only 4 bars
    out = label_bars(bars, threshold=0.5)
    assert len(out) == 0


def test_multi_ticker_independence():
    a = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    b = _bars(closes=[200, 200, 200, 200, 198.0, 198.0])
    b["ticker"] = "B"
    out = label_bars(pd.concat([a, b], ignore_index=True), threshold=0.5)
    assert out[out["ticker"] == "T"].iloc[0]["label"] == LABEL_LONG
    assert out[out["ticker"] == "B"].iloc[0]["label"] == LABEL_SHORT
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_labeler.py -v
```

Expected: `ImportError: cannot import name 'label_bars'`.

- [ ] **Step 3: Write `labeler.py`**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/labeler.py
"""Vol-normalized 3-class label function for the bake-off.

Pure. No I/O. Spec §4.3.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

LABEL_SHORT = 0
LABEL_FLAT = 1
LABEL_LONG = 2


def label_bars(
    bars: pd.DataFrame,
    *,
    threshold: float = 0.5,
) -> pd.DataFrame:
    """Attach an integer ``label`` column to *bars* under the spec rule.

    Per-row label:

      r_fwd  = (close[t+4] - open[t+1]) / open[t+1]
      r_norm = r_fwd / (atr_14[t] / close[t])
      label  = LONG  if r_norm >= +threshold
               SHORT if r_norm <= -threshold
               else FLAT

    Bars where the label window crosses a ``bar_date`` boundary,
    or where ``atr_14[t]`` is NaN / zero, are dropped — they
    cannot be labelled without forward-looking information.

    Args:
        bars: Must contain ``ticker``, ``bar_open_ts_ns``, ``bar_date``,
            ``open``, ``close``, ``atr_14``. Sorted ascending per ticker.
        threshold: σ-multiple threshold for LONG / SHORT cut-off.

    Returns:
        Frame with the same columns as *bars* plus ``label``,
        ``entry_px``, ``exit_px``, ``r_norm``. Unlabellable rows
        are dropped.
    """
    required = {"ticker", "bar_open_ts_ns", "bar_date",
                "open", "close", "atr_14"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    if (bars["close"] <= 0).any() or (bars["open"] <= 0).any():
        raise ValueError("non-positive price encountered")

    out_frames = []
    for ticker, grp in bars.groupby("ticker", sort=False):
        grp = grp.sort_values("bar_open_ts_ns").reset_index(drop=True)
        n = len(grp)
        if n < 5:
            continue
        # Build forward-looking arrays for t in [0, n-5).
        entry_px = grp["open"].shift(-1)
        exit_px  = grp["close"].shift(-4)
        same_day = grp["bar_date"].shift(-4) == grp["bar_date"]
        r_fwd = (exit_px - entry_px) / entry_px
        atr_ret = grp["atr_14"] / grp["close"]
        r_norm = r_fwd / atr_ret

        keep = (
            grp.index < n - 4
        ) & same_day & atr_ret.notna() & (atr_ret > 0) & r_fwd.notna()

        sub = grp[keep].copy()
        sub["entry_px"] = entry_px[keep].values
        sub["exit_px"] = exit_px[keep].values
        sub["r_norm"] = r_norm[keep].values
        sub["label"] = np.where(
            sub["r_norm"] >= threshold, LABEL_LONG,
            np.where(sub["r_norm"] <= -threshold, LABEL_SHORT,
                     LABEL_FLAT),
        )
        out_frames.append(sub)

    if not out_frames:
        return bars.iloc[0:0].assign(
            entry_px=pd.Series(dtype=float),
            exit_px=pd.Series(dtype=float),
            r_norm=pd.Series(dtype=float),
            label=pd.Series(dtype=int),
        )
    return pd.concat(out_frames, ignore_index=True)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_labeler.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/labeler.py \
        backend/algo/research/intraday_15m_mis_bakeoff/tests/test_labeler.py
git commit -m "feat(research): vol-normalized 3-class labeler"
```

---

## Task 4: Dataset loader (EAV pivot + bars join) + fixture test

**Files:**
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/dataset.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_dataset_shape.py`

- [ ] **Step 1: Write the failing test using an in-memory pyarrow fixture**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/tests/test_dataset_shape.py
"""Dataset shape tests using an in-memory pyarrow fixture.

No Iceberg dependency — we monkeypatch ``load_features_eav``
and ``load_bars`` to return synthetic frames, exercising the
pivot + join + filter logic only.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backend.algo.research.intraday_15m_mis_bakeoff import dataset


def _eav_fixture() -> pd.DataFrame:
    """Two tickers × 8 bars × 3 features in EAV form."""
    rows = []
    base_ts = 1_700_000_000_000_000_000   # 2023-11-14 ns-ish; arbitrary
    for ticker in ("A", "B"):
        for i in range(8):
            ts = base_ts + i * 900 * 1_000_000_000
            for fname, fval in [
                ("rsi_14", 50.0 + i),
                ("relative_volume", 1.0 + i * 0.1),
                ("regime_label", "SIDEWAYS"),
            ]:
                rows.append({
                    "ticker": ticker,
                    "bar_open_ts_ns": ts,
                    "bar_date": date(2026, 1, 5),
                    "interval_sec": 900,
                    "feature_name": fname,
                    "feature_value": fval,
                    "feature_set_version": "v1",
                })
    return pd.DataFrame(rows)


def _bars_fixture() -> pd.DataFrame:
    rows = []
    base_ts = 1_700_000_000_000_000_000
    for ticker in ("A", "B"):
        for i in range(8):
            ts = base_ts + i * 900 * 1_000_000_000
            rows.append({
                "ticker": ticker,
                "bar_open_ts_ns": ts,
                "bar_date": date(2026, 1, 5),
                "interval_sec": 900,
                "open": 100 + i,
                "high": 101 + i,
                "low":  99 + i,
                "close": 100 + i + 0.5,
                "volume": 1000,
                "atr_14": 1.0,
            })
    return pd.DataFrame(rows)


def test_pivot_produces_wide_frame_with_expected_columns(monkeypatch):
    monkeypatch.setattr(dataset, "_load_features_eav",
                        lambda **kwargs: _eav_fixture())
    monkeypatch.setattr(dataset, "_load_bars",
                        lambda **kwargs: _bars_fixture())

    df = dataset.load_research_frame(
        tickers=["A", "B"],
        date_min=date(2026, 1, 1),
        date_max=date(2026, 1, 31),
    )
    # Wide frame: feature columns + bar columns + ticker/ts.
    assert {"rsi_14", "relative_volume", "regime_label",
            "open", "close", "atr_14",
            "ticker", "bar_open_ts_ns", "bar_date"} <= set(df.columns)


def test_pivot_has_no_duplicate_keys(monkeypatch):
    monkeypatch.setattr(dataset, "_load_features_eav",
                        lambda **kwargs: _eav_fixture())
    monkeypatch.setattr(dataset, "_load_bars",
                        lambda **kwargs: _bars_fixture())
    df = dataset.load_research_frame(
        tickers=["A", "B"],
        date_min=date(2026, 1, 1),
        date_max=date(2026, 1, 31),
    )
    assert df.duplicated(["ticker", "bar_open_ts_ns"]).sum() == 0


def test_join_aligns_on_ticker_and_ts(monkeypatch):
    monkeypatch.setattr(dataset, "_load_features_eav",
                        lambda **kwargs: _eav_fixture())
    monkeypatch.setattr(dataset, "_load_bars",
                        lambda **kwargs: _bars_fixture())
    df = dataset.load_research_frame(
        tickers=["A", "B"],
        date_min=date(2026, 1, 1),
        date_max=date(2026, 1, 31),
    )
    # Each (ticker, bar_open_ts_ns) row has both a feature value
    # and a price value — no NaN in close after the join.
    assert df["close"].notna().all()
    assert df["rsi_14"].notna().all()


def test_session_hours_filter_drops_pre_open(monkeypatch):
    # Inject a bar at 08:30 IST (before 09:15 open).
    eav = _eav_fixture()
    bars = _bars_fixture()
    pre_open_ts = 1_699_999_000_000_000_000   # well before
    extra_eav = pd.DataFrame([{
        "ticker": "A", "bar_open_ts_ns": pre_open_ts,
        "bar_date": date(2026, 1, 5), "interval_sec": 900,
        "feature_name": "rsi_14", "feature_value": 30.0,
        "feature_set_version": "v1",
    }])
    extra_bars = pd.DataFrame([{
        "ticker": "A", "bar_open_ts_ns": pre_open_ts,
        "bar_date": date(2026, 1, 5), "interval_sec": 900,
        "open": 100, "high": 101, "low": 99, "close": 100,
        "volume": 1000, "atr_14": 1.0,
    }])
    monkeypatch.setattr(dataset, "_load_features_eav",
                        lambda **kwargs: pd.concat([eav, extra_eav]))
    monkeypatch.setattr(dataset, "_load_bars",
                        lambda **kwargs: pd.concat([bars, extra_bars]))
    df = dataset.load_research_frame(
        tickers=["A", "B"],
        date_min=date(2026, 1, 1),
        date_max=date(2026, 1, 31),
        enforce_session_hours=True,
    )
    # Pre-open bar should be filtered out.
    assert (df["bar_open_ts_ns"] == pre_open_ts).sum() == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_dataset_shape.py -v
```

Expected: `ImportError` / `AttributeError`.

- [ ] **Step 3: Write `dataset.py`**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/dataset.py
"""Iceberg → pandas research frame for the intraday 15m bake-off.

Pulls from ``stocks.intraday_features`` (EAV) and ``stocks.intraday_bars``,
pivots to wide, applies the spec §4.2 filter chain, joins
``stocks.regime_history`` as a daily overlay. Returns a single
pandas frame ready for the labeler.

Spec §4.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone, timedelta
from typing import Iterable

import pandas as pd

_logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


# Carved out as a private helper so tests can monkeypatch
# without touching Iceberg.
def _load_features_eav(
    *,
    tickers: list[str],
    date_min: date,
    date_max: date,
    interval_sec: int = 900,
) -> pd.DataFrame:
    """EAV rows from ``stocks.intraday_features``."""
    from backend.db.duckdb_engine import query_iceberg_df

    placeholders = ",".join([f"'{t}'" for t in tickers])
    sql = (
        "SELECT ticker, bar_open_ts_ns, bar_date, interval_sec, "
        "feature_name, feature_value, feature_set_version "
        "FROM intraday_features "
        f"WHERE ticker IN ({placeholders}) "
        f"  AND interval_sec = {interval_sec} "
        f"  AND bar_date BETWEEN DATE '{date_min}' AND DATE '{date_max}' "
        # Guard against future schema evolution where multiple
        # feature_set_versions coexist — pin to the latest.
        "  AND feature_set_version = "
        "    (SELECT MAX(feature_set_version) FROM intraday_features)"
    )
    return query_iceberg_df("stocks.intraday_features", sql)


def _load_bars(
    *,
    tickers: list[str],
    date_min: date,
    date_max: date,
    interval_sec: int = 900,
) -> pd.DataFrame:
    """OHLCV + ATR_14 from ``stocks.intraday_bars``.

    ATR_14 lives in ``stocks.intraday_features`` (EAV) — we
    pivot it across with the rest of the features rather than
    pulling separately here.
    """
    from backend.db.duckdb_engine import query_iceberg_df

    placeholders = ",".join([f"'{t}'" for t in tickers])
    sql = (
        "SELECT ticker, bar_open_ts_ns, bar_date, interval_sec, "
        "open, high, low, close, volume "
        "FROM intraday_bars "
        f"WHERE ticker IN ({placeholders}) "
        f"  AND interval_sec = {interval_sec} "
        f"  AND bar_date BETWEEN DATE '{date_min}' AND DATE '{date_max}' "
    )
    return query_iceberg_df("stocks.intraday_bars", sql)


def _load_regime_overlay(date_min: date, date_max: date) -> pd.DataFrame:
    from backend.db.duckdb_engine import query_iceberg_df

    sql = (
        "SELECT bar_date, regime_label "
        "FROM regime_history "
        f"WHERE bar_date BETWEEN DATE '{date_min}' AND DATE '{date_max}'"
    )
    return query_iceberg_df("stocks.regime_history", sql)


def _is_in_session(ts_ns: int) -> bool:
    """09:15 IST <= bar_open_ts < 15:00 IST."""
    ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(_IST)
    return time(9, 15) <= ts.time() < time(15, 0)


def _drop_warmup(df: pd.DataFrame, n_bars: int = 8) -> pd.DataFrame:
    """Drop the first *n_bars* of each (ticker, bar_date).

    Spec §4.2 #5 — VWAP/ORB stability.
    """
    df = df.sort_values(["ticker", "bar_open_ts_ns"])
    rank = df.groupby(["ticker", "bar_date"]).cumcount()
    return df[rank >= n_bars].copy()


def load_research_frame(
    *,
    tickers: list[str],
    date_min: date,
    date_max: date,
    enforce_session_hours: bool = True,
    drop_warmup_bars: int = 8,
) -> pd.DataFrame:
    """Build the wide research frame for the bake-off.

    Returns a pandas frame with one row per ``(ticker, bar_open_ts_ns)``,
    feature columns from the EAV pivot, OHLCV + ATR_14 from
    ``stocks.intraday_bars`` / ``stocks.intraday_features``, and
    ``regime_label`` joined from ``stocks.regime_history``.
    """
    eav = _load_features_eav(
        tickers=tickers, date_min=date_min, date_max=date_max,
    )
    bars = _load_bars(
        tickers=tickers, date_min=date_min, date_max=date_max,
    )

    if eav.empty or bars.empty:
        return pd.DataFrame()

    # Pivot EAV → wide on (ticker, bar_open_ts_ns).
    wide = eav.pivot_table(
        index=["ticker", "bar_open_ts_ns", "bar_date", "interval_sec"],
        columns="feature_name",
        values="feature_value",
        aggfunc="first",
    ).reset_index()
    wide.columns.name = None

    # Join price columns.
    df = wide.merge(
        bars[["ticker", "bar_open_ts_ns",
              "open", "high", "low", "close", "volume"]],
        on=["ticker", "bar_open_ts_ns"],
        how="inner",
    )

    # Filters §4.2.
    if enforce_session_hours:
        df = df[df["bar_open_ts_ns"].apply(_is_in_session)].copy()
    df = _drop_warmup(df, n_bars=drop_warmup_bars)

    # Daily overlay — regime_history may be empty in tests; tolerate.
    try:
        regime = _load_regime_overlay(date_min, date_max)
        if not regime.empty:
            df = df.merge(regime, on="bar_date", how="left",
                          suffixes=("", "_regime"))
    except Exception:
        _logger.warning("regime overlay load failed — proceeding without",
                        exc_info=True)

    return df.reset_index(drop=True)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_dataset_shape.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/dataset.py \
        backend/algo/research/intraday_15m_mis_bakeoff/tests/test_dataset_shape.py
git commit -m "feat(research): EAV→wide dataset loader with monkeypatched fixture tests"
```

---

## Task 5: SHAP aggregation + asymmetry detection (TDD)

**Files:**
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/shap_eval.py`
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_shap_eval.py`

- [ ] **Step 1: Write the failing test using a frozen SHAP-output fixture**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/tests/test_shap_eval.py
"""Tests for the per-class SHAP aggregator.

SHAP itself is library code we trust. We test our aggregation
logic against a frozen SHAP-shaped fixture: list of 3 arrays
of shape ``(n_rows, n_features)``.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.algo.research.intraday_15m_mis_bakeoff.shap_eval import (
    aggregate_per_feature,
    bucket_features,
    BUCKET_LONG_SIDE,
    BUCKET_SHORT_SIDE,
    BUCKET_SYMMETRIC,
)


def _frozen_shap_values():
    """Three classes × 100 rows × 3 features.

    Feature 0: strongly LONG-positive in class LONG.
    Feature 1: strongly SHORT-positive in class SHORT.
    Feature 2: equally important in both classes (symmetric).
    """
    rng = np.random.default_rng(0)
    n_rows = 100
    sv_short = rng.normal(0, 0.01, (n_rows, 3))
    sv_flat  = rng.normal(0, 0.01, (n_rows, 3))
    sv_long  = rng.normal(0, 0.01, (n_rows, 3))

    sv_long[:, 0] += 0.5     # feature 0 → LONG
    sv_short[:, 1] += 0.5    # feature 1 → SHORT
    sv_long[:, 2] += 0.3
    sv_short[:, 2] += 0.3

    return [sv_short, sv_flat, sv_long]


def test_aggregate_returns_expected_columns():
    sv = _frozen_shap_values()
    out = aggregate_per_feature(sv, feature_names=["f0", "f1", "f2"])
    assert set(out.columns) == {
        "feature", "mean_abs_long", "mean_abs_short",
        "directional_long", "directional_short", "asymmetry",
    }
    assert len(out) == 3


def test_aggregate_directional_signs_match_fixture():
    sv = _frozen_shap_values()
    out = aggregate_per_feature(sv, feature_names=["f0", "f1", "f2"])
    out = out.set_index("feature")
    assert out.loc["f0", "directional_long"]  > 0.3
    assert out.loc["f1", "directional_short"] > 0.3


def test_bucket_classifies_long_short_symmetric():
    sv = _frozen_shap_values()
    out = aggregate_per_feature(sv, feature_names=["f0", "f1", "f2"])
    bucketed = bucket_features(out)
    by_feat = bucketed.set_index("feature")["bucket"].to_dict()
    assert by_feat["f0"] == BUCKET_LONG_SIDE
    assert by_feat["f1"] == BUCKET_SHORT_SIDE
    assert by_feat["f2"] == BUCKET_SYMMETRIC


def test_stable_features_intersection_across_seeds():
    """Gate 5 helper — top-K intersection across seeds."""
    from backend.algo.research.intraday_15m_mis_bakeoff.shap_eval import (
        compute_stable_features,
    )
    rankings_per_seed = [
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7"},
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f9"},   # 7/8 overlap
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f10"},
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7"},
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f8"},
    ]
    result = compute_stable_features(rankings_per_seed, min_overlap=6)
    assert result["stable"] == {"f0", "f1", "f2", "f3", "f4", "f5", "f6"}
    # f7 appears in 2 of 5 → not mostly_stable (needs ≥4).
    assert "f7" not in result["mostly_stable"]
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_shap_eval.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write `shap_eval.py`**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/shap_eval.py
"""SHAP per-class aggregation + asymmetry bucketing.

Spec §6.2, §6.3, §7.2.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

BUCKET_LONG_SIDE = "long_side"
BUCKET_SHORT_SIDE = "short_side"
BUCKET_SYMMETRIC = "symmetric"
BUCKET_INTERACTION_ONLY = "interaction_only"


def aggregate_per_feature(
    shap_values: list[np.ndarray],
    feature_names: list[str],
) -> pd.DataFrame:
    """Compute the 4 per-feature aggregations from the spec table.

    Args:
        shap_values: As returned by ``TreeExplainer.shap_values`` —
            list of 3 arrays in class order
            ``[SHORT, FLAT, LONG]``, each shape ``(n_rows, n_features)``.
        feature_names: Column names matching the feature axis.

    Returns:
        Long frame: one row per feature, columns
        ``feature, mean_abs_long, mean_abs_short, directional_long,
        directional_short, asymmetry``.
    """
    sv_short, _sv_flat, sv_long = shap_values
    if sv_short.shape[1] != len(feature_names):
        raise ValueError(
            f"feature_names length {len(feature_names)} != "
            f"SHAP feature axis {sv_short.shape[1]}"
        )
    rows = []
    for i, fname in enumerate(feature_names):
        rows.append({
            "feature": fname,
            "mean_abs_long":     float(np.abs(sv_long[:, i]).mean()),
            "mean_abs_short":    float(np.abs(sv_short[:, i]).mean()),
            "directional_long":  float(sv_long[:, i].mean()),
            "directional_short": float(sv_short[:, i].mean()),
        })
    out = pd.DataFrame(rows)
    out["asymmetry"] = out["mean_abs_long"] - out["mean_abs_short"]
    return out


def bucket_features(agg: pd.DataFrame) -> pd.DataFrame:
    """Tag each feature as long_side / short_side / symmetric / interaction_only.

    Bucketing per spec §6.3:
      - long_side  if asymmetry > +0.5 × σ_asym
      - short_side if asymmetry < -0.5 × σ_asym
      - symmetric  otherwise
      - interaction_only if both mean_abs_long and mean_abs_short are
        in the bottom decile AND |directional| both tiny — set aside
        as a niche bucket for v2 work.
    """
    sigma = agg["asymmetry"].std(ddof=0) or 1e-12
    out = agg.copy()
    cond_long  = out["asymmetry"] >  0.5 * sigma
    cond_short = out["asymmetry"] < -0.5 * sigma

    bucket = np.where(
        cond_long, BUCKET_LONG_SIDE,
        np.where(cond_short, BUCKET_SHORT_SIDE, BUCKET_SYMMETRIC),
    )
    out["bucket"] = bucket
    return out


def compute_stable_features(
    rankings_per_seed: list[set[str]],
    *,
    min_overlap: int = 6,
    mostly_overlap: int = 4,
) -> dict[str, set[str]]:
    """Gate 5 — intersection + mostly-overlap sets across seeds.

    Args:
        rankings_per_seed: Per-seed top-K feature sets (same K).
        min_overlap: Feature must appear in ALL seeds (intersection)
            for ``stable``. Default matches the spec's ``≥ 6/8`` —
            for K=8 the intersection size cut-off lives in the
            CALLER's gate logic; here we return both the strict
            intersection and the mostly-overlap set for the report.
        mostly_overlap: Min seeds (out of N) a feature must appear
            in to land in ``mostly_stable``.
    """
    if not rankings_per_seed:
        return {"stable": set(), "mostly_stable": set()}

    universe: set[str] = set().union(*rankings_per_seed)
    counts = {f: sum(f in r for r in rankings_per_seed) for f in universe}
    stable = {f for f, c in counts.items() if c == len(rankings_per_seed)}
    mostly_stable = {f for f, c in counts.items() if c >= mostly_overlap}
    return {"stable": stable, "mostly_stable": mostly_stable}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_shap_eval.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/shap_eval.py \
        backend/algo/research/intraday_15m_mis_bakeoff/tests/test_shap_eval.py
git commit -m "feat(research): SHAP per-class aggregator + asymmetry bucketing + stability set"
```

---

## Task 6: Gate 6 harness self-test (synthetic-data sanity)

**Files:**
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/tests/test_gate6_harness.py`

This test is part of the production gate suite — it verifies the **whole training pipeline** (not just labeler / shap_eval) can detect a signal we know is there. It uses real XGBoost and real SHAP on synthetic data.

- [ ] **Step 1: Write the test**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/tests/test_gate6_harness.py
"""Gate 6 — harness self-test on synthetic, deterministic data.

If one feature linearly drives the 3-class label, the harness
must rank it #1 by mean_abs SHAP across both LONG and SHORT
classes, with at least 3× the next feature's importance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import shap
import xgboost as xgb

from backend.algo.research.intraday_15m_mis_bakeoff.shap_eval import (
    aggregate_per_feature,
)


def _synthetic_dataset(n_rows: int = 5_000, seed: int = 42):
    """Feature 0 drives the label; features 1-9 are pure noise."""
    rng = np.random.default_rng(seed)
    n_features = 10
    X = rng.normal(0, 1, size=(n_rows, n_features))
    # Label is a clean step function of feature 0.
    y = np.where(X[:, 0] >  0.5, 2,                     # LONG
         np.where(X[:, 0] < -0.5, 0,                    # SHORT
                  1))                                    # FLAT
    feature_names = [f"f{i}" for i in range(n_features)]
    return X, y, feature_names


def test_harness_ranks_injected_feature_first():
    X, y, names = _synthetic_dataset()

    model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=200,
        max_depth=4,
        learning_rate=0.1,
        tree_method="hist",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)

    sv = shap.TreeExplainer(model).shap_values(X)
    # shap may return a list (multiclass) or a single array with
    # an extra class axis. Normalize to list-of-3 (SHORT, FLAT, LONG).
    if isinstance(sv, list):
        sv_list = sv
    else:
        sv_list = [sv[..., k] for k in range(3)]

    agg = aggregate_per_feature(sv_list, feature_names=names)
    # Combined importance: SHORT + LONG.
    agg["combined"] = agg["mean_abs_long"] + agg["mean_abs_short"]
    agg = agg.sort_values("combined", ascending=False).reset_index(drop=True)

    top = agg.iloc[0]
    runner_up = agg.iloc[1]
    assert top["feature"] == "f0", (
        f"expected f0 #1, got {top['feature']}; full ranking:\n{agg}"
    )
    assert top["combined"] >= 3 * runner_up["combined"], (
        f"top feature only {top['combined'] / runner_up['combined']:.2f}× "
        f"runner-up — harness is not picking up the signal cleanly"
    )
```

- [ ] **Step 2: Run the test**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/tests/test_gate6_harness.py -v
```

Expected: 1 passed, runtime < 30 s.

If it fails because `f0` ranks #2 or `combined` ratio is below 3, the harness is not picking up an obvious signal — investigate before continuing the plan. Common culprits: SHAP return-shape mismatch (list vs array), wrong class order, n_estimators too low.

- [ ] **Step 3: Commit**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/tests/test_gate6_harness.py
git commit -m "test(research): Gate 6 harness self-test on synthetic data"
```

---

## Task 7: Train.py — pre-training gates + smoke mode

**Files:**
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/train.py`

Build train.py incrementally: this task lands a **smoke-only** version that exercises Gates 1, 2, 3 on synthetic data and trains one model with one seed. Tasks 8 and 9 add multi-seed stability, SHAP, and real-data modes.

- [ ] **Step 1: Write skeleton with smoke-mode CLI**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/train.py
"""XGBoost 3-class training + gate orchestration for the bake-off.

Modes:
  --smoke   synthetic 5K-row data; no Iceberg
  --dry-run real Iceberg, 3 tickers, 2 weeks (added in Task 9)
  (full)    F&O 200, full window, 5 seeds (added in Task 9)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.utils.class_weight import compute_class_weight

from backend.algo.research._shared.time_split import (
    assert_chronological,
    chronological_split,
)
from backend.algo.research.intraday_15m_mis_bakeoff.labeler import (
    LABEL_FLAT, LABEL_LONG, LABEL_SHORT, label_bars,
)

_logger = logging.getLogger("bakeoff.train")

XGB_PARAMS: dict[str, Any] = {
    "objective": "multi:softprob",
    "num_class": 3,
    "n_estimators": 400,
    "max_depth": 5,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "min_child_weight": 10,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "tree_method": "hist",
    "eval_metric": ["mlogloss", "merror"],
    "early_stopping_rounds": 30,
    "n_jobs": -1,
}


@dataclass
class GateResults:
    chronology: str = "skipped"
    label_distribution: str = "skipped"
    leak_audit: str = "skipped"
    random_baseline: str = "skipped"
    ranking_stability: str = "skipped"
    harness_self_test: str = "skipped"
    per_regime: dict[str, dict[str, float]] = field(default_factory=dict)


def gate1_chronology(train_fit, train_val, test) -> str:
    """Hard fail on overlap."""
    assert_chronological(train_fit, train_val, test, date_col="bar_date")
    return "pass"


def gate2_label_distribution(y_train: np.ndarray) -> tuple[str, dict]:
    """Each class must be in [15%, 60%]."""
    counts = pd.Series(y_train).value_counts(normalize=True).to_dict()
    pct = {int(k): float(v) for k, v in counts.items()}
    ok = all(0.15 <= pct.get(k, 0.0) <= 0.60
             for k in (LABEL_SHORT, LABEL_FLAT, LABEL_LONG))
    return ("pass" if ok else f"fail: {pct}"), pct


def gate3_leak_audit(
    X: pd.DataFrame, y: np.ndarray, threshold: float = 0.5
) -> str:
    """Pearson |corr| < 0.5 for every feature."""
    corrs = X.corrwith(pd.Series(y, index=X.index)).abs()
    offenders = corrs[corrs >= threshold].to_dict()
    if offenders:
        raise ValueError(
            f"Gate 3 LEAK AUDIT FAILED — features correlated with label: "
            f"{offenders}"
        )
    return "pass"


def _synthetic_smoke_frame(n_rows: int = 5_000, seed: int = 42):
    """5K-row synthetic data — same as Gate 6, plus a date column."""
    rng = np.random.default_rng(seed)
    n_features = 10
    X = rng.normal(0, 1, size=(n_rows, n_features))
    y = np.where(X[:, 0] >  0.5, LABEL_LONG,
         np.where(X[:, 0] < -0.5, LABEL_SHORT, LABEL_FLAT))
    feature_names = [f"f{i}" for i in range(n_features)]
    df = pd.DataFrame(X, columns=feature_names)
    # Fake bar_date column for the chronological split.
    df["bar_date"] = pd.date_range("2026-01-01", periods=n_rows,
                                   freq="15min").date
    df["label"] = y
    return df, feature_names


def _train_one(X_fit, y_fit, X_val, y_val, *, seed: int) -> xgb.XGBClassifier:
    params = XGB_PARAMS | {"random_state": seed}
    w = compute_class_weight(
        "balanced",
        classes=np.array([LABEL_SHORT, LABEL_FLAT, LABEL_LONG]),
        y=y_fit,
    )
    sample_weight = w[y_fit]
    model = xgb.XGBClassifier(**params)
    model.fit(
        X_fit, y_fit,
        sample_weight=sample_weight,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    return model


def run_smoke() -> dict[str, Any]:
    """End-to-end on synthetic data — exercises gates 1, 2, 3 + training."""
    df, feature_names = _synthetic_smoke_frame()

    # Single chronological split over the synthetic timeline.
    train_fit, train_val, test = chronological_split(
        df.sort_values("bar_date"),
        date_col="bar_date",
        train_fit_end=df["bar_date"].iloc[3000],
        train_val_end=df["bar_date"].iloc[4000],
    )
    gates = GateResults()
    gates.chronology = gate1_chronology(train_fit, train_val, test)

    X_fit = train_fit[feature_names]
    y_fit = train_fit["label"].to_numpy()
    X_val = train_val[feature_names]
    y_val = train_val["label"].to_numpy()
    X_test = test[feature_names]
    y_test = test["label"].to_numpy()

    gates.label_distribution, _ = gate2_label_distribution(y_fit)
    gates.leak_audit = gate3_leak_audit(X_fit, y_fit)

    model = _train_one(X_fit, y_fit, X_val, y_val, seed=42)
    test_mlogloss = float(
        model.evals_result_["validation_0"]["mlogloss"][-1]
    )

    return {
        "mode": "smoke",
        "rows": {"fit": len(X_fit), "val": len(X_val), "test": len(X_test)},
        "gates": gates.__dict__,
        "best_iteration": int(model.best_iteration or 0),
        "test_mlogloss_estimate": test_mlogloss,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true",
                        help="Run on synthetic 5K rows; no Iceberg.")
    args = parser.parse_args(argv)

    if not args.smoke:
        parser.error(
            "Only --smoke is wired in this commit; "
            "--dry-run and full mode arrive in Task 9."
        )

    out = run_smoke()
    print(json.dumps(out, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run smoke mode and confirm gates pass**

```bash
docker compose exec -T backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train --smoke
```

Expected JSON: `gates.chronology == "pass"`, `gates.label_distribution == "pass"`, `gates.leak_audit == "pass"`, non-empty `best_iteration`.

If Gate 2 fails (label imbalance on synthetic data), the synthetic generator is biased — adjust the cut-offs in `_synthetic_smoke_frame` until the three classes are roughly balanced.

- [ ] **Step 3: Commit**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/train.py
git commit -m "feat(research): train.py smoke mode + gates 1/2/3"
```

---

## Task 8: Train.py — Gates 4, 5, 7 (random baseline, ranking stability, per-regime)

**Files:**
- Modify: `backend/algo/research/intraday_15m_mis_bakeoff/train.py`

- [ ] **Step 1: Add Gate 4 (random baseline)**

Add this function to `train.py` (after `gate3_leak_audit`):

```python
def gate4_random_baseline(
    y_train: np.ndarray, y_test: np.ndarray, test_mlogloss: float
) -> tuple[str, float]:
    """Test mlogloss must beat a stratified random classifier by 0.05.

    The random baseline emits the train class distribution as
    its prediction for every test row.
    """
    pi = (np.bincount(y_train, minlength=3) / len(y_train))
    # mlogloss of a constant predictor = -mean(log(pi[y_test])).
    eps = 1e-15
    pi_clipped = np.clip(pi, eps, 1.0)
    baseline = float(-np.log(pi_clipped[y_test]).mean())
    delta = baseline - test_mlogloss
    ok = delta >= 0.05
    return (
        ("pass" if ok else f"fail: model {test_mlogloss:.4f} vs "
                          f"baseline {baseline:.4f} (Δ={delta:.4f})"),
        baseline,
    )
```

- [ ] **Step 2: Add Gate 5 (ranking stability across seeds)**

```python
def gate5_ranking_stability(
    X_fit, y_fit, X_val, y_val, X_test,
    *,
    feature_names: list[str],
    seeds: list[int],
    top_k: int = 8,
    min_overlap: int = 6,
) -> tuple[str, dict]:
    """Train *seeds* boosters; intersect top-K by SHAP magnitude.

    Returns the gate result string + the stability dict from
    ``compute_stable_features``.
    """
    import shap
    from backend.algo.research.intraday_15m_mis_bakeoff.shap_eval import (
        compute_stable_features,
    )

    rankings = []
    for seed in seeds:
        model = _train_one(X_fit, y_fit, X_val, y_val, seed=seed)
        sv = shap.TreeExplainer(model).shap_values(X_test)
        # Normalize to list-of-3 (SHORT, FLAT, LONG).
        sv_list = sv if isinstance(sv, list) else [sv[..., k] for k in range(3)]
        importance = np.abs(sv_list[0]).mean(0) + np.abs(sv_list[2]).mean(0)
        top_idx = importance.argsort()[-top_k:]
        rankings.append({feature_names[i] for i in top_idx})

    stab = compute_stable_features(
        rankings, min_overlap=min_overlap, mostly_overlap=4
    )
    overlap = len(stab["stable"])
    ok = overlap >= min_overlap
    return (
        ("pass" if ok else
         f"fail: only {overlap} features stable across all seeds"),
        {"stable": list(stab["stable"]),
         "mostly_stable": list(stab["mostly_stable"]),
         "rankings_per_seed": [list(r) for r in rankings]},
    )
```

- [ ] **Step 3: Add Gate 7 (per-regime power)**

```python
def gate7_per_regime(
    df_test: pd.DataFrame, y_test: np.ndarray, y_pred_proba: np.ndarray,
    *, min_rows: int = 500,
) -> dict[str, dict]:
    """Per-regime mlogloss + count.

    Soft gate: stamps caveats, does not fail.
    """
    if "regime_label" not in df_test.columns:
        return {}
    result: dict[str, dict] = {}
    eps = 1e-15
    proba = np.clip(y_pred_proba, eps, 1.0)
    for regime in ("BULL", "SIDEWAYS", "BEAR"):
        mask = df_test["regime_label"].to_numpy() == regime
        n = int(mask.sum())
        if n == 0:
            result[regime] = {"rows": 0, "mlogloss": None,
                              "underpowered": True}
            continue
        ll = float(-np.log(proba[mask, y_test[mask]]).mean())
        result[regime] = {
            "rows": n,
            "mlogloss": ll,
            "underpowered": n < min_rows,
        }
    return result
```

- [ ] **Step 4: Run smoke mode again to confirm nothing broke**

```bash
docker compose exec -T backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train --smoke
```

Expected: same output as Task 7 step 2 (Gates 4, 5, 7 are not yet wired into `run_smoke`; the smoke run still trains a single seed). This step just confirms the new code compiles + imports cleanly.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/train.py
git commit -m "feat(research): gates 4 (random baseline) + 5 (5-seed stability) + 7 (per-regime)"
```

---

## Task 9: Train.py — dry-run + full modes + run orchestration

**Files:**
- Modify: `backend/algo/research/intraday_15m_mis_bakeoff/train.py`

This task wires the Iceberg dataset loader + labeler + all gates into a real end-to-end run.

- [ ] **Step 1: Add `run_real()` orchestrator and CLI flags**

Add to `train.py`:

```python
from datetime import date


def _augment_with_label(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Apply labeler.label_bars and return the labelled frame.

    Drops unlabellable rows. The label column is integer 0/1/2.
    """
    labelled = label_bars(df, threshold=threshold)
    if labelled.empty:
        raise RuntimeError("labeler produced 0 rows — check input data")
    return labelled


def run_real(
    *,
    tickers: list[str],
    date_min: date,
    date_max: date,
    train_fit_end: date,
    train_val_end: date,
    threshold: float,
    seeds: list[int],
    output_dir: Path,
) -> dict[str, Any]:
    """Real-data mode: load Iceberg → label → split → all gates → report.

    Used by both --dry-run (3 tickers, 2 weeks) and full mode.
    The caller decides the ticker list + date window.
    """
    from backend.algo.research.intraday_15m_mis_bakeoff.dataset import (
        load_research_frame,
    )

    _logger.info("Loading research frame for %d tickers %s..%s",
                 len(tickers), date_min, date_max)
    raw = load_research_frame(
        tickers=tickers, date_min=date_min, date_max=date_max,
    )
    _logger.info("Raw frame: %d rows × %d cols",
                 len(raw), raw.shape[1] if not raw.empty else 0)
    labelled = _augment_with_label(raw, threshold=threshold)
    _logger.info("Labelled frame: %d rows after dropping unlabellable",
                 len(labelled))

    # Pre-training data assertions §7.3.
    assert labelled["entry_px"].notna().all(),  "t+1 open missing"
    assert labelled["exit_px"].notna().all(),   "t+4 close missing"
    assert (labelled["atr_14"] > 0).all(),       "ATR_14 zero"
    assert labelled.duplicated(
        ["ticker", "bar_open_ts_ns"]).sum() == 0,  "pivot duplicates"

    labelled = labelled.sort_values("bar_date").reset_index(drop=True)
    train_fit, train_val, test = chronological_split(
        labelled,
        date_col="bar_date",
        train_fit_end=train_fit_end,
        train_val_end=train_val_end,
    )

    # Feature columns are everything except identifiers + label + the
    # forward-looking aux columns the labeler added.
    excluded = {"ticker", "bar_open_ts_ns", "bar_date", "interval_sec",
                "label", "entry_px", "exit_px", "r_norm",
                "open", "high", "low", "close", "volume"}
    # One-hot regime + time bucket if present.
    work = labelled.copy()
    for cat_col in ("regime_label", "time_of_day_bucket"):
        if cat_col in work.columns:
            dummies = pd.get_dummies(work[cat_col],
                                     prefix=cat_col, dummy_na=False)
            work = pd.concat([work.drop(columns=[cat_col]), dummies], axis=1)
            excluded.add(cat_col)

    feature_names = [c for c in work.columns
                     if c not in excluded and pd.api.types.is_numeric_dtype(work[c])]

    # Re-split after one-hot.
    train_fit = work.loc[train_fit.index][feature_names + ["label"]]
    train_val = work.loc[train_val.index][feature_names + ["label"]]
    test_df   = work.loc[test.index][feature_names + ["label", "bar_date"]]

    gates = GateResults()
    gates.chronology = gate1_chronology(
        labelled.loc[train_fit.index], labelled.loc[train_val.index],
        labelled.loc[test.index],
    )
    y_fit  = train_fit["label"].to_numpy()
    y_val  = train_val["label"].to_numpy()
    y_test = test_df["label"].to_numpy()
    X_fit  = train_fit[feature_names]
    X_val  = train_val[feature_names]
    X_test = test_df[feature_names]

    gates.label_distribution, label_dist = gate2_label_distribution(y_fit)
    gates.leak_audit = gate3_leak_audit(X_fit, y_fit)

    primary_model = _train_one(X_fit, y_fit, X_val, y_val, seed=seeds[0])
    proba_test = primary_model.predict_proba(X_test)
    eps = 1e-15
    proba_clipped = np.clip(proba_test, eps, 1.0)
    test_mlogloss = float(-np.log(proba_clipped[
        np.arange(len(y_test)), y_test
    ]).mean())

    gates.random_baseline, baseline = gate4_random_baseline(
        y_fit, y_test, test_mlogloss,
    )
    gates.ranking_stability, stability = gate5_ranking_stability(
        X_fit, y_fit, X_val, y_val, X_test,
        feature_names=feature_names, seeds=seeds,
    )
    # Per-regime needs the labelled frame's regime column.
    test_with_regime = labelled.loc[test.index][
        ["bar_date"] + ([c for c in ["regime_label"]
                         if c in labelled.columns])
    ]
    gates.per_regime = gate7_per_regime(
        test_with_regime, y_test, proba_test,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    primary_model.save_model(str(output_dir / "model.json"))

    summary = {
        "mode": "real",
        "tickers": len(tickers),
        "date_window": [str(date_min), str(date_max)],
        "rows": {"fit": len(X_fit), "val": len(X_val), "test": len(X_test)},
        "feature_count": len(feature_names),
        "gates": gates.__dict__,
        "best_iteration": int(primary_model.best_iteration or 0),
        "test_mlogloss": test_mlogloss,
        "random_baseline_mlogloss": baseline,
        "label_distribution": label_dist,
        "stability": stability,
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, default=str, indent=2)
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true",
                        help="Synthetic 5K rows; no Iceberg.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Real Iceberg, 3 tickers, 2 weeks.")
    parser.add_argument("--train-end",
                        type=lambda s: date.fromisoformat(s),
                        default=date(2026, 2, 28),
                        help="Last date (inclusive) of the train_val split.")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="σ-multiple threshold for LONG/SHORT.")
    parser.add_argument("--seeds", type=str, default="42,43,44,45,46",
                        help="Comma-separated random seeds for Gate 5.")
    parser.add_argument("--out", type=Path,
                        default=Path.home() / ".ai-agent-ui" / "research_runs"
                        / f"{datetime.now().date()}-intraday-15m-bakeoff",
                        help="Output directory for run artifacts.")
    parser.add_argument("--tickers-cap", type=int, default=None,
                        help="Optional cap on F&O universe for debugging.")
    args = parser.parse_args(argv)

    seeds = [int(s) for s in args.seeds.split(",")]

    if args.smoke:
        out = run_smoke()
        print(json.dumps(out, default=str, indent=2))
        return 0

    # Load F&O universe (or 3 names for dry-run).
    from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
        load_fno_universe,
    )
    if args.dry_run:
        tickers = ["RELIANCE.NS", "HDFCBANK.NS", "INFY.NS"]
        date_min = date(2026, 1, 1)
        date_max = date(2026, 1, 14)
        train_fit_end = date(2026, 1, 7)
        train_val_end = date(2026, 1, 10)
    else:
        tickers = load_fno_universe()
        if args.tickers_cap is not None:
            tickers = tickers[: args.tickers_cap]
        date_min = date(2025, 11, 17)
        date_max = date(2026, 5, 21)
        # Train-fit ends 20 days before train_val_end.
        train_fit_end = args.train_end - pd.Timedelta(days=20)
        train_fit_end = train_fit_end.date() \
            if hasattr(train_fit_end, "date") else train_fit_end
        train_val_end = args.train_end

    out = run_real(
        tickers=tickers,
        date_min=date_min,
        date_max=date_max,
        train_fit_end=train_fit_end,
        train_val_end=train_val_end,
        threshold=args.threshold,
        seeds=seeds,
        output_dir=args.out,
    )
    print(json.dumps(out, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run dry-run mode on 3 tickers**

```bash
docker compose exec -T backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train --dry-run
```

Expected: completes in ~1 min, prints a JSON summary, writes `model.json` + `run_summary.json` to the output dir, all hard gates pass.

If Gate 2 fails on the dry-run (likely with only 3 tickers × 2 weeks), the threshold needs adjustment. Set `--threshold 0.3` and re-run; document the value that worked.

If Gate 3 leak audit flags features, inspect the offenders — most likely a forward-looking column (`entry_px`/`exit_px`/`r_norm`) accidentally retained in `feature_names`. Update the `excluded` set in `run_real`.

- [ ] **Step 3: Commit**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/train.py
git commit -m "feat(research): real-data dry-run + full mode + orchestration"
```

---

## Task 10: Report generation (CSV, PNGs, run_metadata.json)

**Files:**
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/report.py`
- Modify: `backend/algo/research/intraday_15m_mis_bakeoff/train.py` (wire `report.write_run` into `run_real`)

- [ ] **Step 1: Write `report.py`**

```python
# backend/algo/research/intraday_15m_mis_bakeoff/report.py
"""Markdown + PNG report generation for the bake-off.

Spec §6.4, §6.5, §6.7.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
        ).decode().strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=Path(__file__).parent,
        ).decode().strip()
        return bool(out)
    except Exception:
        return False


def write_run_metadata(
    *,
    output_dir: Path,
    summary: dict[str, Any],
    hyperparams: dict[str, Any],
    threshold: float,
    fno_csv_path: Path,
) -> None:
    """Write run_metadata.json — the reproducibility ledger §7.5."""
    fno_sha = hashlib.sha256(fno_csv_path.read_bytes()).hexdigest()
    metadata = {
        "git_commit": _git_commit(),
        "dirty_tree": _git_dirty(),
        "started_at_ist": datetime.now(timezone.utc).isoformat(),
        "fno_universe_sha256": fno_sha,
        "hyperparams": hyperparams,
        "threshold_used": threshold,
        "summary": summary,
    }
    (output_dir / "run_metadata.json").write_text(
        json.dumps(metadata, default=str, indent=2)
    )


def write_feature_ranking_csv(
    aggregated: pd.DataFrame, output_dir: Path,
) -> None:
    """Persist the §4 report table as machine-readable CSV."""
    aggregated.sort_values(
        ["mean_abs_long", "mean_abs_short"], ascending=False,
    ).to_csv(output_dir / "feature_ranking.csv", index=False)


def write_class_balance(
    label_dist: dict, output_dir: Path,
) -> None:
    pd.DataFrame([
        {"class": "SHORT", "fraction": label_dist.get(0, 0.0)},
        {"class": "FLAT",  "fraction": label_dist.get(1, 0.0)},
        {"class": "LONG",  "fraction": label_dist.get(2, 0.0)},
    ]).to_csv(output_dir / "class_balance.csv", index=False)


def plot_shap_summary(
    sv: np.ndarray, X: pd.DataFrame, class_name: str,
    out_path: Path, top_n: int = 15,
) -> None:
    """Beeswarm of a single class's SHAP values."""
    plt.figure(figsize=(10, 8))
    shap.summary_plot(sv, X, max_display=top_n, show=False)
    plt.title(f"SHAP summary — class {class_name}")
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()


def plot_two_sided_ranking(
    aggregated: pd.DataFrame, out_path: Path, top_n: int = 20,
) -> None:
    """Two-sided bar chart: short importance (left) vs long (right)."""
    agg = aggregated.copy()
    agg["combined"] = agg["mean_abs_long"] + agg["mean_abs_short"]
    agg = agg.sort_values("combined", ascending=True).tail(top_n)

    fig, ax = plt.subplots(figsize=(10, max(6, 0.35 * len(agg))))
    y = np.arange(len(agg))
    ax.barh(y, -agg["mean_abs_short"], label="mean_abs_short", color="#c0392b")
    ax.barh(y,  agg["mean_abs_long"],  label="mean_abs_long",  color="#27ae60")
    ax.set_yticks(y)
    ax.set_yticklabels(agg["feature"])
    ax.axvline(0, color="black", linewidth=0.5)
    ax.set_xlabel("mean |SHAP| (short ←  → long)")
    ax.set_title("Per-feature importance: short vs long")
    ax.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()


def write_report_md(
    *,
    summary: dict[str, Any],
    aggregated: pd.DataFrame,
    bucketed: pd.DataFrame,
    stable_features: set[str],
    output_dir: Path,
) -> None:
    """Assemble report.md per spec §6.5."""
    lines: list[str] = []
    lines.append(f"# Intraday 15m MIS Bake-Off — "
                 f"{datetime.now().date().isoformat()}\n")

    gates = summary["gates"]
    soft_failed = [k for k, v in gates.items()
                   if isinstance(v, str) and v.startswith("fail")]

    lines.append("## 1. Run metadata\n")
    lines.append(f"- Tickers: {summary['tickers']}")
    lines.append(f"- Date window: {summary['date_window'][0]} → "
                 f"{summary['date_window'][1]}")
    lines.append(f"- Rows: fit={summary['rows']['fit']}, "
                 f"val={summary['rows']['val']}, test={summary['rows']['test']}")
    lines.append(f"- Feature count: {summary['feature_count']}")
    lines.append(f"- Best iteration: {summary['best_iteration']}\n")

    lines.append("## 2. Training summary\n")
    lines.append(f"- Test mlogloss: **{summary['test_mlogloss']:.4f}**")
    lines.append(f"- Random baseline mlogloss: "
                 f"{summary['random_baseline_mlogloss']:.4f}\n")

    lines.append("## 3. Caveats — READ FIRST\n")
    if soft_failed:
        lines.append(f"> ⚠️ **{len(soft_failed)} soft gate(s) failed: "
                     f"{', '.join(soft_failed)}. "
                     f"Treat ranking as exploratory only.**\n")
    per_regime = gates.get("per_regime", {})
    for r in ("BULL", "SIDEWAYS", "BEAR"):
        info = per_regime.get(r, {})
        rows = info.get("rows", 0)
        mark = " ⚠️ underpowered" if info.get("underpowered") else ""
        lines.append(f"- {r}: {rows} test rows{mark}")
    lines.append("")

    lines.append("## 4. Feature ranking\n")
    cols = ["feature", "mean_abs_long", "mean_abs_short",
            "asymmetry", "bucket", "directional_long", "directional_short"]
    table = bucketed[cols].sort_values(
        ["mean_abs_long", "mean_abs_short"], ascending=False,
    ).copy()
    table["stable"] = table["feature"].isin(stable_features).map(
        {True: "✅", False: "🟡"}
    )
    lines.append(table.to_markdown(index=False, floatfmt=".4f"))
    lines.append("")

    lines.append("## 5. SHAP plots\n")
    lines.append("![SHAP — LONG class](shap_long.png)\n")
    lines.append("![SHAP — SHORT class](shap_short.png)\n")
    lines.append("![Two-sided ranking](feature_ranking.png)\n")

    lines.append("## 6. Draft AST candidates\n")
    stable_rows = bucketed[bucketed["feature"].isin(stable_features)] \
        .sort_values("mean_abs_long", ascending=False).head(8)
    if stable_rows.empty:
        lines.append("> Gate 5 produced no stable features. "
                     "Section omitted intentionally — see §3 caveats.\n")
    else:
        for _, row in stable_rows.iterrows():
            direction = ("→ LONG" if row["directional_long"] > 0
                         else "→ SHORT")
            lines.append(f"- **{row['feature']}** ({row['bucket']}): "
                         f"{direction}, "
                         f"|long|={row['mean_abs_long']:.4f}, "
                         f"|short|={row['mean_abs_short']:.4f}")
        lines.append("\n(Draft AST JSON is left for the follow-up "
                     "strategy spec — this report informs but does not "
                     "produce a backtested strategy.)\n")

    lines.append("## 7. Next actions\n")
    lines.append("- Read §3 caveats first.")
    lines.append("- If stable features non-empty and gates pass:")
    lines.append("  → Proceed to strategy v1 spec (backtest the draft AST).")
    lines.append("- If Gate 2 self-tuned: re-run pinned at the new threshold.")
    lines.append("- If BULL is underpowered (§3): queue feature backfill spec.")
    lines.append("- If Gate 4 failed: document the negative result and stop.")

    (output_dir / "report.md").write_text("\n".join(lines))
```

- [ ] **Step 2: Wire `report.write_run` into `run_real`**

In `train.py`'s `run_real`, after the gate/training block but before `return summary`, add:

```python
    # Build per-feature SHAP aggregates for the report.
    import shap as _shap_lib
    from backend.algo.research.intraday_15m_mis_bakeoff.shap_eval import (
        aggregate_per_feature, bucket_features,
    )
    from backend.algo.research.intraday_15m_mis_bakeoff import report as rpt
    sv_primary = _shap_lib.TreeExplainer(primary_model).shap_values(X_test)
    sv_list = (sv_primary if isinstance(sv_primary, list)
               else [sv_primary[..., k] for k in range(3)])
    aggregated = aggregate_per_feature(sv_list, feature_names=feature_names)
    bucketed = bucket_features(aggregated)

    rpt.plot_shap_summary(sv_list[2], X_test, "LONG",
                          output_dir / "shap_long.png")
    rpt.plot_shap_summary(sv_list[0], X_test, "SHORT",
                          output_dir / "shap_short.png")
    rpt.plot_two_sided_ranking(aggregated, output_dir / "feature_ranking.png")
    rpt.write_feature_ranking_csv(bucketed, output_dir)
    rpt.write_class_balance(label_dist, output_dir)
    rpt.write_run_metadata(
        output_dir=output_dir,
        summary=summary,
        hyperparams=XGB_PARAMS,
        threshold=threshold,
        fno_csv_path=(
            Path(__file__).parent / "fno_200.csv"
        ),
    )
    rpt.write_report_md(
        summary=summary, aggregated=aggregated, bucketed=bucketed,
        stable_features=set(stability["stable"]),
        output_dir=output_dir,
    )
```

- [ ] **Step 3: Run dry-run end-to-end and inspect artifacts**

```bash
docker compose exec -T backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train --dry-run

ls ~/.ai-agent-ui/research_runs/*-intraday-15m-bakeoff/
```

Expected files in the output dir:
- `report.md`
- `feature_ranking.csv`
- `class_balance.csv`
- `shap_long.png`
- `shap_short.png`
- `feature_ranking.png`
- `model.json`
- `run_metadata.json`
- `run_summary.json`

Open `report.md` and confirm: §1 metadata populated, §3 caveats list per-regime counts, §4 ranking table renders, §6 stable-features bullets non-empty or explicitly stated as omitted.

- [ ] **Step 4: Commit**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/report.py \
        backend/algo/research/intraday_15m_mis_bakeoff/train.py
git commit -m "feat(research): report.md + PNGs + reproducibility ledger"
```

---

## Task 11: README + full-run smoke

**Files:**
- Create: `backend/algo/research/intraday_15m_mis_bakeoff/README.md`

- [ ] **Step 1: Write README**

```markdown
# Intraday 15m MIS Bake-Off

Spec: [`docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md`](../../../../docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md)

## Modes

| Mode | Purpose | Runtime |
|---|---|---|
| `--smoke` | Synthetic 5K rows; CI-safe | < 30 s |
| `--dry-run` | 3 tickers, 2 weeks; real Iceberg | ~1 min |
| (default) | F&O 200, full window, 5 seeds | ~25 min |

## Happy path

```bash
# 1. Iceberg health check
docker compose exec backend python -c \
  "from backend.db.duckdb_engine import query_iceberg_df; \
   print(query_iceberg_df('stocks.intraday_features', \
       'SELECT MAX(bar_date) FROM intraday_features'))"

# 2. Tests
docker compose exec backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/

# 3. Dry-run
docker compose exec backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train --dry-run

# 4. Full run
docker compose exec backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train \
    --train-end 2026-02-28 \
    --threshold 0.5 \
    --seeds 42,43,44,45,46
```

## Output

`~/.ai-agent-ui/research_runs/<date>-intraday-15m-bakeoff/`:

- `report.md` — primary deliverable
- `feature_ranking.csv`
- `shap_long.png`, `shap_short.png`, `feature_ranking.png`
- `model.json`
- `run_metadata.json` — reproducibility ledger
- `run_summary.json`, `class_balance.csv`

## Failure-mode playbook

See spec §8.3.

## Refreshing the F&O universe

The static `fno_200.csv` is a one-time pull from `algo.instruments`. Refresh quarterly when NSE updates the F&O list — see plan Task 1 step 1 for the SQL.
```

- [ ] **Step 2: Run the full test suite to confirm everything passes**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/ -v
```

Expected: all tests pass (test_time_split: 4, test_labeler: 10, test_dataset_shape: 4, test_shap_eval: 4, test_gate6_harness: 1). Total: 23 passed.

- [ ] **Step 3: Run the actual full bake-off**

```bash
docker compose exec -T backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train \
    --train-end 2026-02-28 \
    --threshold 0.5 \
    --seeds 42,43,44,45,46
```

Expected: completes in ~25 min, prints summary JSON, writes the full artifact set. Inspect `report.md` and triage outcome per §7.6 of the spec.

If gates fail per the playbook in spec §8.3, follow the documented fix (adjust `--threshold`, cap `--tickers-cap`, etc.) and re-run.

- [ ] **Step 4: Commit README + final**

```bash
git add backend/algo/research/intraday_15m_mis_bakeoff/README.md
git commit -m "docs(research): README + dry-run validated end-to-end"
```

---

## Task 12: PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin research/intraday-15m-mis-bakeoff-spec
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(research): intraday 15m MIS feature-importance bake-off" --body "$(cat <<'EOF'
## Summary
- Read-only research subtree at `backend/algo/research/intraday_15m_mis_bakeoff/`
- XGBoost 3-class + SHAP feature-importance bake-off on F&O 200 over 15-min intraday features
- 7 validation gates incl. 5-seed ranking stability
- Outputs report.md + plots + model.json + reproducibility ledger to `~/.ai-agent-ui/research_runs/`

Spec: `docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md`

## Test plan
- [x] `pytest backend/algo/research/intraday_15m_mis_bakeoff/` green (23 tests)
- [x] `--smoke` mode produces gate-pass summary
- [x] `--dry-run` mode produces full artifact set on 3 tickers × 2 weeks
- [ ] Full run completes and `report.md` triaged

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Address review, squash-merge per `reference_git_merge_policy`**

---

## Spec-coverage self-review

| Spec section | Plan task(s) | Notes |
|---|---|---|
| §3 Architecture (file layout, CLI) | Task 1, 7-9, 11 | Full subtree built; CLI gains modes incrementally |
| §4 Dataset & labels | Task 3 (labeler), Task 4 (loader) | EAV pivot, filters, label rules all implemented + tested |
| §5 Model (XGBoost) | Task 7 (`_train_one` + `XGB_PARAMS`) | All hyperparams pinned in code |
| §6 SHAP & report | Task 5 (aggregator) + Task 10 (PNG + Markdown) | Three plots + ranked CSV + report.md |
| §7 Validation gates | Tasks 6, 7, 8 | Gates 1-7 implemented; hard vs soft split honored |
| §7.4 Unit tests | Tasks 2, 3, 4, 5, 6 | All 5 test files specified line-by-line |
| §7.5 Reproducibility ledger | Task 10 (`write_run_metadata`) | git commit + dirty + data hashes + hyperparams + threshold |
| §8 Run procedure | Task 11 (README) + Task 7/9 (modes) | Smoke / dry-run / full all wired |
| §9 Non-goals | (intentionally absent from plan) | No strategy AST, no backtest, no UI |
| §10 References | Cross-referenced from spec | — |

No placeholder text remaining. Method signatures used in later tasks match earlier definitions (`label_bars`, `chronological_split`, `aggregate_per_feature`, `_train_one`, `compute_stable_features`). Type hints consistent.
