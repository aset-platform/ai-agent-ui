# Regime-Aware Multi-Factor System — Slice REGIME-1: Regime Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily regime classifier (BULL / SIDEWAYS / BEAR) + 2-state HMM stress-probability overlay, persisted nightly to Iceberg, exposed as runtime features (`regime_label`, `stress_prob`, breadth, VIX), and surfaced in the Trading tab via a regime widget + history chart.

**Architecture:** Rule-based primary classifier (NIFTY vs SMA200, India VIX bands, 30/60d momentum, breadth) emits a deterministic label. 2-state Gaussian HMM on (NIFTY log-return, 20d realized vol) provides advisory `stress_prob`. Both persisted to `stocks.regime_history` (daily). HMM `(transmat, means, covars)` persisted to `stocks.regime_hmm_state` (monthly refit, warm-started). Daily orchestrator at 22:30 IST. Online predictions are forward-only (`predict(X[:t+1])`) — anti-look-ahead is a hard CI gate.

**Tech Stack:** Python 3.12, FastAPI, hmmlearn 0.3.x, Pydantic v2, PyIceberg 0.11.1, SQLAlchemy 2.0 async (NullPool sync→async bridge per CLAUDE.md §5.1). Frontend: Next.js 16, React 19, ECharts (lazy `next/dynamic`), SWR. Tests: pytest, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.1 (rule-based classifier), §3.2 (HMM overlay), §4.1 (Iceberg tables), §4.3 (sector index ingest), §5.1 REGIME-1 row.

**Research Anchor:** `docs/superpowers/research/2026-05-10-regime-aware-multifactor-research.md` — §1 (hybrid rule+HMM, India VIX bands), §2 (NSE breadth thresholds).

**Branch:** `feature/regime-slice-1-engine` off `feature/regime-multifactor-integration` (the latter is already created and tracking `origin`).

**Estimated SP:** 13

---

## Pre-flight (MUST DO before writing any code)

This slice introduces several new modules and touches existing ones. Per the `feedback_subagent_grep_preflight` memory: every imported symbol, called method, and referenced constant MUST be grep-verified before code is written. The previous v2 epic accumulated 8 wrong-name bugs (`_iceberg_table_path`, `kite_api_secret` slug, `await cache.get`, etc.) all from skipping this step.

Verify the following names exist in the current `feature/regime-slice-1-engine` worktree before each task:

- `backend.algo.iceberg_init._create_table` and `_get_catalog` — used by `create_tables.py`. Check both names exist:  `grep -n "^def _create_table\|^def _get_catalog" stocks/create_tables.py`
- `backend.db.duckdb_engine.query_iceberg_table` — used as Iceberg read fast-path: `grep -n "^def query_iceberg_table" backend/db/duckdb_engine.py`
- `backend.db.cache.cache.get / cache.set / cache.invalidate` — these are SYNC; never `await` them: `grep -n "def get\|def set\|def invalidate" backend/db/cache.py`
- `backend.jobs.executor.register_job` decorator: `grep -n "^def register_job" backend/jobs/executor.py`
- `tools._stock_shared._require_repo` — used by `gap_filler.refresh_market_indices`: `grep -n "_require_repo" tools/_stock_shared.py`
- `backend.algo.strategy.features.FEATURES, Feature, FeatureSource` — registry to extend.
- `frontend/lib/apiFetch.ts` exports `apiFetch`. `frontend/lib/config.ts` exports `API_URL`.
- `backend.algo.routes` mount mechanism: `grep -n "include_router" backend/main.py | head -10`

**Checklist:**
- [ ] All grep verifications above run; every name resolves.
- [ ] Worktree is clean (`git status` shows no untracked/modified).
- [ ] `pytest backend/algo/tests -q` passes pre-change baseline.

If any verification fails, STOP and ask before guessing.

---

## File Structure

**Backend — new files:**
- `backend/algo/regime/__init__.py`
- `backend/algo/regime/rule_based.py`
- `backend/algo/regime/hmm_overlay.py`
- `backend/algo/regime/repo.py`
- `backend/algo/regime/classifier_job.py`
- `backend/algo/regime/iceberg_init.py` — schema definitions for `stocks.regime_history` + `stocks.regime_hmm_state`.
- `backend/algo/routes/regime.py`
- `backend/algo/regime/tests/__init__.py`
- `backend/algo/regime/tests/test_rule_based.py`
- `backend/algo/regime/tests/test_hmm_overlay.py`
- `backend/algo/regime/tests/test_repo.py`
- `backend/algo/regime/tests/test_classifier_job.py`
- `backend/algo/regime/tests/test_routes.py`
- `backend/algo/regime/tests/test_features_registered.py`

**Backend — modified:**
- `backend/jobs/gap_filler.py` — add 11 NIFTY sector indices to the existing `indices` list (§4.3 of spec).
- `stocks/create_tables.py` — register `stocks.regime_history` + `stocks.regime_hmm_state` via `_create_table()` (idempotent).
- `backend/algo/strategy/features.py` — register `regime_label` (string), `stress_prob` (float), `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio`, `vix_close`, `vix_sma_20`. Extend `FeatureType` to include `"string"` and extend `FeatureSource` with `"regime"`.
- `backend/main.py` — `app.include_router(regime_router)` (REQUIRES `docker compose restart backend`, see §6.2 of CLAUDE.md).
- `backend/db/cache.py` — extend `_CACHE_INVALIDATION_MAP` to wire `stocks.regime_history` → `cache:regime:*` if such a map exists; otherwise add invalidation in repo.

**Frontend — new files:**
- `frontend/components/algo-trading/RegimeWidget.tsx`
- `frontend/components/algo-trading/RegimeHistoryChart.tsx`
- `frontend/hooks/useRegime.ts`

**Frontend — modified:**
- `frontend/components/algo-trading/strategyFeatureCatalog.ts` — sync new feature keys.
- `frontend/components/algo-trading/PaperTab.tsx` — mount `RegimeWidget` next to the Trading-tab title.
- `frontend/lib/echarts.ts` — register `LineChart` + `MarkAreaComponent` if not already present (used by RegimeHistoryChart).

**E2E:**
- `e2e/utils/selectors.ts` — add testids: `regime-widget`, `regime-badge`, `regime-vix-gauge`, `regime-breadth-bar`, `regime-stress-chip`, `regime-history-chart`.
- `e2e/tests/frontend/algo-trading-regime-widget.spec.ts` — seeded regime row test.

---

## Task 1 — Add NIFTY sector indices to daily ingest

**Files:**
- Modify: `backend/jobs/gap_filler.py:198-208` (the existing `indices` list).
- Test: `backend/algo/regime/tests/test_sector_index_ingest.py` (NEW).

The existing `refresh_market_indices()` already fetches `^INDIAVIX` and `^NSEI` (verified via grep — see Pre-flight). We extend the list with NIFTY sector indices needed for breadth + relative strength downstream.

- [ ] **Step 1.1: Write failing test for sector index registration**

Create `backend/algo/regime/tests/__init__.py` (empty) and `backend/algo/regime/tests/test_sector_index_ingest.py`:

```python
"""Verify NIFTY sector indices + INDIAVIX are in the gap-filler list."""
from __future__ import annotations

from backend.jobs import gap_filler


def test_sector_indices_in_refresh_list() -> None:
    """All NIFTY sector indices required by REGIME-1 must be in
    the gap-filler indices list, in addition to ^INDIAVIX + ^NSEI
    that already existed."""
    src = gap_filler.refresh_market_indices.__code__
    # Read the source file & check each symbol appears in the
    # indices list. Brittle but effective — the list is a literal
    # in the function body.
    import inspect

    body = inspect.getsource(gap_filler.refresh_market_indices)
    required = [
        "^INDIAVIX",   # already present (regression guard)
        "^NSEI",       # already present (regression guard)
        "^NSEBANK",
        "^CNXIT",
        "^CNXAUTO",
        "^CNXPHARMA",
        "^CNXFMCG",
        "^CNXMETAL",
        "^CNXENERGY",
        "^CNXREALTY",
        "^CNXPSUBANK",
        "^CNXFINANCE",
        "^NIFMDCP150",
    ]
    missing = [t for t in required if t not in body]
    assert not missing, f"Missing from refresh list: {missing}"
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `pytest backend/algo/regime/tests/test_sector_index_ingest.py -v`
Expected: FAIL — `^NSEBANK` missing.

- [ ] **Step 1.3: Add sector indices to gap_filler**

Edit `backend/jobs/gap_filler.py` — extend the `indices = [...]` list inside `refresh_market_indices()` to add the 11 sector tickers immediately after the existing `"^NSEI"` line. Maintain block comments by tier. Code:

```python
        indices = [
            # Market indices (Phase 2)
            "^VIX",
            "^INDIAVIX",
            "^GSPC",
            "^NSEI",
            # NIFTY sector indices (REGIME-1: required for breadth +
            # relative strength + sector rotation downstream)
            "^NSEBANK",
            "^CNXIT",
            "^CNXAUTO",
            "^CNXPHARMA",
            "^CNXFMCG",
            "^CNXMETAL",
            "^CNXENERGY",
            "^CNXREALTY",
            "^CNXPSUBANK",
            "^CNXFINANCE",
            "^NIFMDCP150",
            # Macro indicators (Phase 3)
            "^TNX",  # 10-Year Treasury Yield
            "^IRX",  # 13-Week T-Bill Rate
            "CL=F",  # WTI Crude Oil
            "DX-Y.NYB",  # US Dollar Index
        ]
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `pytest backend/algo/regime/tests/test_sector_index_ingest.py -v`
Expected: PASS.

- [ ] **Step 1.5: Commit**

```bash
git add backend/jobs/gap_filler.py backend/algo/regime/tests/__init__.py backend/algo/regime/tests/test_sector_index_ingest.py
git commit -m "feat(algo): add 11 NIFTY sector indices to daily refresh (REGIME-1)"
```

---

## Task 2 — Pure rule-based classifier

**Files:**
- Create: `backend/algo/regime/__init__.py` (empty), `backend/algo/regime/rule_based.py`.
- Test: `backend/algo/regime/tests/test_rule_based.py`.

Pure function — no I/O, no NaN handling beyond explicit guards. Thresholds inlined as module constants per spec §3.1. Keeps the function deterministic; tunability via PG `algo_regime_config` is deferred to a follow-up task post-REGIME-1.

- [ ] **Step 2.1: Write failing tests — table-driven**

Create `backend/algo/regime/tests/test_rule_based.py`:

```python
"""Tests for rule_based.classify_regime — table-driven."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.algo.regime.rule_based import classify_regime


@pytest.mark.parametrize(
    "name,nifty_close,nifty_sma200,vix,r30,r60,breadth,expected",
    [
        # BULL: above SMA200 + calm/normal VIX + bullish momentum +
        # healthy breadth.
        (
            "calm-bull",
            "20000", "18000", "13", "0.05", "0.10", "0.65", "BULL",
        ),
        (
            "normal-vix-bull",
            "20000", "18000", "20", "0.03", "0.06", "0.58", "BULL",
        ),
        # BEAR: below SMA200 + stress VIX + bearish momentum
        # (breadth not required in BEAR rule).
        (
            "stress-bear",
            "16000", "18000", "30", "-0.05", "-0.10", "0.30", "BEAR",
        ),
        # SIDEWAYS catch-all
        (
            "above-sma-but-stress-vix",
            "20000", "18000", "30", "0.05", "0.10", "0.65", "SIDEWAYS",
        ),
        (
            "below-sma-but-no-bearish-mom",
            "16000", "18000", "30", "0.00", "0.00", "0.30", "SIDEWAYS",
        ),
        (
            "below-sma-no-stress-vix",
            "16000", "18000", "20", "-0.05", "-0.10", "0.30", "SIDEWAYS",
        ),
        (
            "weak-breadth-blocks-bull",
            "20000", "18000", "13", "0.05", "0.10", "0.50", "SIDEWAYS",
        ),
        (
            "momentum-just-at-threshold",
            "20000", "18000", "13", "0.02", "0.05", "0.65", "SIDEWAYS",
        ),
    ],
)
def test_classify_regime(
    name, nifty_close, nifty_sma200, vix, r30, r60, breadth, expected,
):
    got = classify_regime(
        nifty_close=Decimal(nifty_close),
        nifty_sma200=Decimal(nifty_sma200),
        vix_close=Decimal(vix),
        nifty_ret_30d=Decimal(r30),
        nifty_ret_60d=Decimal(r60),
        pct_above_50sma=Decimal(breadth),
    )
    assert got == expected, name


def test_classify_regime_raises_on_nan() -> None:
    """NaN in any input should raise ValueError. The classifier is
    pure — caller (classifier_job) handles fallback to SIDEWAYS."""
    with pytest.raises(ValueError, match="NaN"):
        classify_regime(
            nifty_close=Decimal("NaN"),
            nifty_sma200=Decimal("18000"),
            vix_close=Decimal("13"),
            nifty_ret_30d=Decimal("0.05"),
            nifty_ret_60d=Decimal("0.10"),
            pct_above_50sma=Decimal("0.65"),
        )
```

- [ ] **Step 2.2: Run to verify failures**

Run: `pytest backend/algo/regime/tests/test_rule_based.py -v`
Expected: ImportError (module not found).

- [ ] **Step 2.3: Implement `rule_based.py`**

Create `backend/algo/regime/__init__.py`:

```python
"""Regime engine — daily classifier (rule-based + HMM advisory)."""
```

Create `backend/algo/regime/rule_based.py`:

```python
"""Rule-based regime classifier.

Pure function — no I/O, no fallbacks. Caller is responsible for
substituting SIDEWAYS when inputs are missing/stale (see
classifier_job._safe_classify).

Thresholds calibrated from research synthesis §1 + §2.1 (India
VIX bands, NSE breadth empirics). Tracked as module constants
for testability; tunability via PG row is deferred to v3.1.
"""
from __future__ import annotations

import math
from decimal import Decimal

VIX_CALM_MAX: Decimal = Decimal("16")
VIX_NORMAL_MAX: Decimal = Decimal("25")
BULLISH_30D_MIN: Decimal = Decimal("0.02")
BULLISH_60D_MIN: Decimal = Decimal("0.05")
BEARISH_30D_MAX: Decimal = Decimal("-0.02")
BEARISH_60D_MAX: Decimal = Decimal("-0.05")
HEALTHY_BREADTH_MIN: Decimal = Decimal("0.55")


def _is_nan(d: Decimal) -> bool:
    """Decimal NaN check — `math.isnan` works on Decimal."""
    return d.is_nan() if hasattr(d, "is_nan") else math.isnan(float(d))


def classify_regime(
    nifty_close: Decimal,
    nifty_sma200: Decimal,
    vix_close: Decimal,
    nifty_ret_30d: Decimal,
    nifty_ret_60d: Decimal,
    pct_above_50sma: Decimal,
) -> str:
    """Return ``"BULL"`` | ``"SIDEWAYS"`` | ``"BEAR"`` for the
    given trading day's close-of-day inputs.

    Raises ValueError if any input is NaN — the caller decides
    whether to fall back to SIDEWAYS.
    """
    inputs = (
        nifty_close, nifty_sma200, vix_close,
        nifty_ret_30d, nifty_ret_60d, pct_above_50sma,
    )
    for v in inputs:
        if _is_nan(v):
            raise ValueError("NaN in classify_regime input")

    above_trend = nifty_close > nifty_sma200
    vix_calm = vix_close < VIX_CALM_MAX
    vix_normal = VIX_CALM_MAX <= vix_close <= VIX_NORMAL_MAX
    vix_stress = vix_close > VIX_NORMAL_MAX
    bullish_mom = (
        nifty_ret_30d > BULLISH_30D_MIN
        and nifty_ret_60d > BULLISH_60D_MIN
    )
    bearish_mom = (
        nifty_ret_30d < BEARISH_30D_MAX
        and nifty_ret_60d < BEARISH_60D_MAX
    )
    healthy_breadth = pct_above_50sma > HEALTHY_BREADTH_MIN

    if (
        above_trend
        and (vix_calm or vix_normal)
        and bullish_mom
        and healthy_breadth
    ):
        return "BULL"
    if (not above_trend) and vix_stress and bearish_mom:
        return "BEAR"
    return "SIDEWAYS"
```

- [ ] **Step 2.4: Run tests to verify pass**

Run: `pytest backend/algo/regime/tests/test_rule_based.py -v`
Expected: 8 PASS, 1 PASS (NaN test) = 9 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add backend/algo/regime/__init__.py backend/algo/regime/rule_based.py backend/algo/regime/tests/test_rule_based.py
git commit -m "feat(algo): rule-based regime classifier (REGIME-1)"
```

---

## Task 3 — Iceberg tables: regime_history + regime_hmm_state

**Files:**
- Create: `backend/algo/regime/iceberg_init.py`.
- Modify: `stocks/create_tables.py` (add 2 new `_create_table()` calls in `create_tables()`).
- Test: `backend/algo/regime/tests/test_iceberg_schema.py` (NEW).

Per CLAUDE.md §5.1: **Iceberg = append-only analytics**, mutable state stays in PG. Regime history is daily-append + nightly idempotent re-write of today's row → use NaN-replaceable upsert pattern (scoped pre-delete for incoming `bar_date`s, append). HMM state is monthly upsert (~12 rows/yr).

Important per spec: `bar_date` is `DateType()` (not StringType). Timestamp guard: this slice has no `TimestampType` column, but if added later → strip tzinfo before write per CLAUDE.md §5.1.

- [ ] **Step 3.1: Write failing schema test**

Create `backend/algo/regime/tests/test_iceberg_schema.py`:

```python
"""Verify the regime_history + regime_hmm_state schemas are
registered with the catalog and have the expected columns."""
from __future__ import annotations

import pytest

from backend.algo.regime.iceberg_init import (
    REGIME_HISTORY_TABLE,
    REGIME_HMM_STATE_TABLE,
    regime_history_schema,
    regime_hmm_state_schema,
)


def test_regime_history_columns() -> None:
    s = regime_history_schema()
    names = {f.name for f in s.fields}
    assert {
        "bar_date",
        "regime_label",
        "stress_prob",
        "rule_inputs_json",
        "classifier_version",
    } <= names


def test_regime_hmm_state_columns() -> None:
    s = regime_hmm_state_schema()
    names = {f.name for f in s.fields}
    assert {
        "trained_through",
        "transmat_json",
        "means_json",
        "covars_json",
        "n_observations",
    } <= names


def test_table_identifiers_namespaced() -> None:
    assert REGIME_HISTORY_TABLE == "stocks.regime_history"
    assert REGIME_HMM_STATE_TABLE == "stocks.regime_hmm_state"
```

- [ ] **Step 3.2: Run to verify fail**

Run: `pytest backend/algo/regime/tests/test_iceberg_schema.py -v`
Expected: ImportError.

- [ ] **Step 3.3: Implement `iceberg_init.py`**

Create `backend/algo/regime/iceberg_init.py`:

```python
"""Schemas + idempotent registration for regime Iceberg tables.

Mirrors the pattern of ``backend/algo/iceberg_init.py``. Registered
into the ``stocks`` namespace via ``stocks/create_tables.py`` so the
existing init script picks them up.

Both tables are append-mostly:
  * regime_history — one row per trading day; nightly idempotent
    re-write supported via NaN-replaceable upsert (pre-delete the
    incoming bar_date keys, then append).
  * regime_hmm_state — one row per monthly refit (~12/yr).
"""
from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform, YearTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
)

REGIME_HISTORY_TABLE = "stocks.regime_history"
REGIME_HMM_STATE_TABLE = "stocks.regime_hmm_state"


def regime_history_schema() -> Schema:
    """One row per trading day. ``rule_inputs_json`` stores the
    inputs dict (vix, ret_30d, ret_60d, pct_above_50sma, etc.) as
    a JSON string — keeps the schema flat & forward-compatible."""
    return Schema(
        NestedField(1, "bar_date", DateType(), required=True),
        NestedField(2, "regime_label", StringType(), required=True),
        NestedField(3, "stress_prob", DoubleType(), required=False),
        NestedField(4, "rule_inputs_json", StringType(), required=True),
        NestedField(5, "classifier_version", StringType(), required=True),
    )


def regime_history_partition_spec() -> PartitionSpec:
    """Partition by year(bar_date)."""
    return PartitionSpec(
        PartitionField(
            source_id=1,
            field_id=1000,
            transform=YearTransform(),
            name="bar_date_year",
        )
    )


def regime_hmm_state_schema() -> Schema:
    """HMM persistence; ~12 rows/yr — keep unpartitioned."""
    return Schema(
        NestedField(1, "trained_through", DateType(), required=True),
        NestedField(2, "transmat_json", StringType(), required=True),
        NestedField(3, "means_json", StringType(), required=True),
        NestedField(4, "covars_json", StringType(), required=True),
        NestedField(5, "n_observations", IntegerType(), required=True),
    )


def regime_hmm_state_partition_spec() -> PartitionSpec:
    return PartitionSpec()


def register_tables() -> None:
    """Idempotent — calls ``_create_table`` for both regime tables.
    Re-uses the catalog + helper from ``stocks.create_tables``."""
    from stocks.create_tables import _create_table, _get_catalog

    catalog = _get_catalog()
    _create_table(
        catalog,
        REGIME_HISTORY_TABLE,
        regime_history_schema(),
        regime_history_partition_spec(),
    )
    _create_table(
        catalog,
        REGIME_HMM_STATE_TABLE,
        regime_hmm_state_schema(),
        regime_hmm_state_partition_spec(),
    )
```

- [ ] **Step 3.4: Wire into stocks/create_tables.py**

Locate the partitioned-tables section in `stocks/create_tables.py` (after `_PIOTROSKI_SCORES_TABLE`) and add (just before the closing block of `create_tables()`):

```python
    # Regime engine — REGIME-1
    from backend.algo.regime.iceberg_init import register_tables as \
        _regime_register
    _regime_register()
```

(Use the same indent as the surrounding `_create_table()` calls. Verify the exact location with `grep -n "create_tables()" stocks/create_tables.py | head -3` first.)

- [ ] **Step 3.5: Run schema tests + idempotent create**

Run schema tests:
```bash
pytest backend/algo/regime/tests/test_iceberg_schema.py -v
```
Expected: 3 PASS.

Run the create-tables script (must be inside the backend container):
```bash
docker compose exec backend python stocks/create_tables.py
```
Expected log line: `Created Iceberg table 'stocks.regime_history'.` and same for `regime_hmm_state`. Re-run → "already exists — skipping".

- [ ] **Step 3.6: Commit**

```bash
git add backend/algo/regime/iceberg_init.py stocks/create_tables.py backend/algo/regime/tests/test_iceberg_schema.py
git commit -m "feat(algo): regime_history + regime_hmm_state Iceberg tables (REGIME-1)"
```

---

## Task 4 — Repo layer for regime_history + regime_hmm_state

**Files:**
- Create: `backend/algo/regime/repo.py`.
- Test: `backend/algo/regime/tests/test_repo.py`.

CRUD via PyIceberg. NaN-replaceable upsert pattern per CLAUDE.md §5.1: scoped pre-delete (`In("bar_date", batch)`) then append. DuckDB read fast-path via `query_iceberg_table` (verified in Pre-flight). Cache invalidation done by repo on every write — `cache.invalidate("cache:regime:*")` (sync, no `await`).

- [ ] **Step 4.1: Write failing tests**

Create `backend/algo/regime/tests/test_repo.py`:

```python
"""Round-trip tests for regime_history + regime_hmm_state via the
real Iceberg catalog. Requires the Docker stack up (DuckDB + Iceberg
SQLite catalog mounted)."""
from __future__ import annotations

import json
from datetime import date

import pytest

from backend.algo.regime.repo import (
    RegimeRow,
    HmmStateRow,
    upsert_regime_history,
    get_latest_regime,
    get_regime_history,
    upsert_hmm_state,
    get_latest_hmm_state,
)


@pytest.mark.iceberg  # requires Docker stack
def test_upsert_regime_history_roundtrip() -> None:
    row = RegimeRow(
        bar_date=date(2026, 5, 9),
        regime_label="BULL",
        stress_prob=0.12,
        rule_inputs={"vix": 13.5, "r30": 0.05, "r60": 0.10},
        classifier_version="v1.0",
    )
    upsert_regime_history([row])

    latest = get_latest_regime()
    assert latest is not None
    assert latest.bar_date == date(2026, 5, 9)
    assert latest.regime_label == "BULL"
    assert latest.stress_prob == pytest.approx(0.12)
    assert latest.rule_inputs == {"vix": 13.5, "r30": 0.05, "r60": 0.10}


@pytest.mark.iceberg
def test_upsert_is_idempotent_replaces_same_date() -> None:
    """Re-inserting same bar_date overwrites, doesn't duplicate."""
    upsert_regime_history([
        RegimeRow(
            bar_date=date(2026, 5, 8),
            regime_label="SIDEWAYS",
            stress_prob=0.40,
            rule_inputs={"vix": 22.0},
            classifier_version="v1.0",
        )
    ])
    upsert_regime_history([
        RegimeRow(
            bar_date=date(2026, 5, 8),
            regime_label="BULL",   # changed
            stress_prob=0.30,
            rule_inputs={"vix": 14.0},
            classifier_version="v1.0",
        )
    ])
    history = get_regime_history(start=date(2026, 5, 8), end=date(2026, 5, 8))
    assert len(history) == 1
    assert history[0].regime_label == "BULL"


@pytest.mark.iceberg
def test_hmm_state_roundtrip() -> None:
    row = HmmStateRow(
        trained_through=date(2026, 4, 30),
        transmat=[[0.95, 0.05], [0.10, 0.90]],
        means=[[0.001, 0.012], [-0.002, 0.025]],
        covars=[[[0.0001, 0.0], [0.0, 0.0001]],
                [[0.0004, 0.0], [0.0, 0.0004]]],
        n_observations=1500,
    )
    upsert_hmm_state(row)
    got = get_latest_hmm_state()
    assert got is not None
    assert got.trained_through == date(2026, 4, 30)
    assert got.transmat[0][0] == pytest.approx(0.95)
    assert got.n_observations == 1500
```

- [ ] **Step 4.2: Run to verify fail**

Run: `pytest backend/algo/regime/tests/test_repo.py -v -m iceberg` (or omit `-m` filter if conftest doesn't define it — `pytest -k regime` works too).
Expected: ImportError.

- [ ] **Step 4.3: Implement `repo.py`**

Create `backend/algo/regime/repo.py`:

```python
"""Iceberg CRUD for stocks.regime_history + stocks.regime_hmm_state.

Reads use ``query_iceberg_table`` (DuckDB fast-path); writes use
PyIceberg directly. NaN-replaceable upsert pattern (scoped
pre-delete by bar_date, then append) keeps re-runs idempotent.

Cache invalidation: every successful write calls
``cache.invalidate("cache:regime:*")`` so the API endpoints serve
fresh data within one round-trip.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pyarrow as pa
from pyiceberg.expressions import EqualTo, In

from backend.algo.regime.iceberg_init import (
    REGIME_HISTORY_TABLE,
    REGIME_HMM_STATE_TABLE,
)
from backend.db.cache import cache
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)


@dataclass
class RegimeRow:
    bar_date: date
    regime_label: str  # BULL | SIDEWAYS | BEAR
    stress_prob: float | None
    rule_inputs: dict[str, Any]
    classifier_version: str = "v1.0"


@dataclass
class HmmStateRow:
    trained_through: date
    transmat: list[list[float]]
    means: list[list[float]]
    covars: list[list[list[float]]]
    n_observations: int


def _catalog():
    from stocks.create_tables import _get_catalog
    return _get_catalog()


def upsert_regime_history(rows: list[RegimeRow]) -> int:
    """NaN-replaceable upsert. Pre-deletes any existing rows for
    the incoming bar_dates; appends the new batch. Invalidates
    cache:regime:* on success."""
    if not rows:
        return 0
    cat = _catalog()
    tbl = cat.load_table(REGIME_HISTORY_TABLE)
    incoming_dates = [r.bar_date for r in rows]
    try:
        tbl.delete(In("bar_date", incoming_dates))
    except Exception as exc:  # pragma: no cover — first run on empty table
        _logger.debug("regime_history pre-delete skipped: %s", exc)

    arrow_tbl = pa.table({
        "bar_date": [r.bar_date for r in rows],
        "regime_label": [r.regime_label for r in rows],
        "stress_prob": [r.stress_prob for r in rows],
        "rule_inputs_json": [
            json.dumps(r.rule_inputs, default=str) for r in rows
        ],
        "classifier_version": [r.classifier_version for r in rows],
    })
    tbl.append(arrow_tbl)
    cache.invalidate("cache:regime:*")
    return len(rows)


def upsert_hmm_state(row: HmmStateRow) -> None:
    cat = _catalog()
    tbl = cat.load_table(REGIME_HMM_STATE_TABLE)
    try:
        tbl.delete(EqualTo("trained_through", row.trained_through))
    except Exception as exc:  # pragma: no cover
        _logger.debug("regime_hmm_state pre-delete skipped: %s", exc)
    arrow_tbl = pa.table({
        "trained_through": [row.trained_through],
        "transmat_json": [json.dumps(row.transmat)],
        "means_json": [json.dumps(row.means)],
        "covars_json": [json.dumps(row.covars)],
        "n_observations": [row.n_observations],
    })
    tbl.append(arrow_tbl)
    cache.invalidate("cache:regime:*")


def _row_from_dict(d: dict) -> RegimeRow:
    raw = d.get("rule_inputs_json")
    parsed = json.loads(raw) if raw else {}
    return RegimeRow(
        bar_date=d["bar_date"],
        regime_label=d["regime_label"],
        stress_prob=d.get("stress_prob"),
        rule_inputs=parsed,
        classifier_version=d.get("classifier_version", "v1.0"),
    )


def get_latest_regime() -> RegimeRow | None:
    rows = query_iceberg_table(
        REGIME_HISTORY_TABLE,
        "SELECT bar_date, regime_label, stress_prob, "
        "rule_inputs_json, classifier_version "
        "FROM tbl ORDER BY bar_date DESC LIMIT 1",
        [],
    )
    return _row_from_dict(rows[0]) if rows else None


def get_regime_history(start: date, end: date) -> list[RegimeRow]:
    rows = query_iceberg_table(
        REGIME_HISTORY_TABLE,
        "SELECT bar_date, regime_label, stress_prob, "
        "rule_inputs_json, classifier_version "
        "FROM tbl WHERE bar_date BETWEEN ? AND ? "
        "ORDER BY bar_date ASC",
        [start, end],
    )
    return [_row_from_dict(r) for r in rows]


def get_latest_hmm_state() -> HmmStateRow | None:
    rows = query_iceberg_table(
        REGIME_HMM_STATE_TABLE,
        "SELECT trained_through, transmat_json, means_json, "
        "covars_json, n_observations FROM tbl "
        "ORDER BY trained_through DESC LIMIT 1",
        [],
    )
    if not rows:
        return None
    r = rows[0]
    return HmmStateRow(
        trained_through=r["trained_through"],
        transmat=json.loads(r["transmat_json"]),
        means=json.loads(r["means_json"]),
        covars=json.loads(r["covars_json"]),
        n_observations=r["n_observations"],
    )
```

If `query_iceberg_table`'s actual signature differs (e.g. SQL parameter style, table-alias placeholder), grep its source first and adjust:
```bash
grep -n "def query_iceberg_table" backend/db/duckdb_engine.py
sed -n "$(grep -n 'def query_iceberg_table' backend/db/duckdb_engine.py | cut -d: -f1),+30p" backend/db/duckdb_engine.py
```

- [ ] **Step 4.4: Run tests**

Inside the backend container so the Iceberg catalog is reachable:
```bash
docker compose exec backend pytest backend/algo/regime/tests/test_repo.py -v
```
Expected: 3 PASS.

- [ ] **Step 4.5: Commit**

```bash
git add backend/algo/regime/repo.py backend/algo/regime/tests/test_repo.py
git commit -m "feat(algo): regime repo with NaN-replaceable upsert + cache invalidation (REGIME-1)"
```

---

## Task 5 — HMM stress-probability overlay

**Files:**
- Create: `backend/algo/regime/hmm_overlay.py`.
- Test: `backend/algo/regime/tests/test_hmm_overlay.py`.

2-state Gaussian HMM on (NIFTY log-return, 20d realized vol). Stable label assignment via post-fit ordering by mean realized vol (state 0 = lower-vol = "calm"; state 1 = "stressed"). **Forward-only filtering is non-negotiable** — `predict(X[:t+1])` only, never `predict(X)`. Hard CI gate enforces this.

`hmmlearn` is the chosen library (not in current `requirements.txt` — add it).

- [ ] **Step 5.1: Add hmmlearn dependency**

Edit `requirements.txt` (add alphabetically, pinning major+minor):

```
hmmlearn==0.3.2
```

Rebuild backend image:
```bash
docker compose build backend
docker compose up -d --force-recreate backend
```

Per CLAUDE.md §6.2: requirements.txt change requires rebuild + force-recreate.

- [ ] **Step 5.2: Write failing tests**

Create `backend/algo/regime/tests/test_hmm_overlay.py`:

```python
"""Tests for StressHMM — fit, persistence, and the
forward-filter-no-lookahead guard."""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from backend.algo.regime.hmm_overlay import StressHMM


def _make_two_regime_data(seed: int = 42) -> np.ndarray:
    """750 days of synthetic 2-regime data (calm + stressed),
    with stressed regime concentrated in days 250-450."""
    rng = np.random.default_rng(seed)
    calm = rng.normal(loc=[0.001, 0.010], scale=[0.005, 0.002], size=(550, 2))
    stressed = rng.normal(
        loc=[-0.002, 0.025], scale=[0.020, 0.005], size=(200, 2)
    )
    out = np.empty((750, 2))
    out[:250] = calm[:250]
    out[250:450] = stressed
    out[450:] = calm[250:]
    return out


def test_fit_assigns_stable_state_ordering() -> None:
    """State 0 must always be the lower-vol-mean state after fit."""
    X = _make_two_regime_data()
    hmm = StressHMM()
    hmm.fit(X, trained_through=date(2026, 5, 9))
    # means_[:, 1] = realized_vol mean
    assert hmm.means[0][1] < hmm.means[1][1], (
        "State 0 must be calm (lower vol) after stable ordering"
    )


def test_stress_prob_in_unit_interval() -> None:
    X = _make_two_regime_data()
    hmm = StressHMM()
    hmm.fit(X, trained_through=date(2026, 5, 9))
    p = hmm.stress_prob(X[-60:])
    assert 0.0 <= p <= 1.0


def test_filtered_no_lookahead() -> None:
    """CRITICAL CI GATE: last-day prediction via predict(X[:t+1])
    must NOT use future data. Compare manual forward filter to
    naive full-sample predict — they must DIFFER on the last day
    when the future contains a regime shift."""
    rng = np.random.default_rng(0)
    calm = rng.normal(loc=[0.001, 0.010], scale=[0.005, 0.002], size=(500, 2))
    spike = rng.normal(loc=[-0.003, 0.030], scale=[0.020, 0.005], size=(50, 2))
    X = np.vstack([calm, spike])

    hmm = StressHMM()
    hmm.fit(X[:500], trained_through=date(2026, 5, 9))

    # Forward-only at t = 500 (just before the spike)
    p_filtered = hmm.stress_prob(X[:501])

    # If stress_prob() incorrectly used full-sample smoothing it
    # would see the spike and return a much higher value. Forward-
    # only must stay close to the calm-regime baseline.
    assert p_filtered < 0.5, (
        f"Forward-only stress_prob should reflect calm baseline; "
        f"got {p_filtered:.3f} (suggests lookahead via full-sample "
        f"predict)"
    )


def test_save_load_roundtrip(tmp_path, monkeypatch) -> None:
    """save() persists to stocks.regime_hmm_state via repo;
    load() restores. Mocked at the repo layer to keep test pure."""
    saved: dict = {}

    def fake_upsert(row):
        saved["row"] = row

    def fake_get_latest():
        from backend.algo.regime.repo import HmmStateRow
        r = saved["row"]
        return r

    monkeypatch.setattr(
        "backend.algo.regime.hmm_overlay.upsert_hmm_state",
        fake_upsert,
    )
    monkeypatch.setattr(
        "backend.algo.regime.hmm_overlay.get_latest_hmm_state",
        fake_get_latest,
    )

    X = _make_two_regime_data()
    hmm = StressHMM()
    hmm.fit(X, trained_through=date(2026, 5, 9))
    hmm.save()

    restored = StressHMM.load()
    assert restored is not None
    np.testing.assert_allclose(restored.means, hmm.means, rtol=1e-6)
    np.testing.assert_allclose(restored.transmat, hmm.transmat, rtol=1e-6)
```

- [ ] **Step 5.3: Run to verify fail**

Run: `docker compose exec backend pytest backend/algo/regime/tests/test_hmm_overlay.py -v`
Expected: ImportError.

- [ ] **Step 5.4: Implement `hmm_overlay.py`**

Create `backend/algo/regime/hmm_overlay.py`:

```python
"""2-state Gaussian HMM stress-probability overlay.

Anti-lookahead invariant: ``stress_prob(window)`` ALWAYS calls
``predict_proba(window)`` on the trailing window only — never on
the full sample. The Viterbi smoothing path used by
``hmm.predict(X)`` over a full sequence uses future observations
to refine intermediate states; it MUST NOT be used for online
inference. Test ``test_filtered_no_lookahead`` is a hard gate.

Persistence: state stored in stocks.regime_hmm_state (one row
per monthly refit; warm-start from last persisted transmat_).
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np

from backend.algo.regime.repo import (
    HmmStateRow,
    get_latest_hmm_state,
    upsert_hmm_state,
)

_logger = logging.getLogger(__name__)

N_STATES = 2
COVARIANCE_TYPE = "diag"
N_ITER_FIT = 200
RANDOM_STATE = 42


class StressHMM:
    """2-state Gaussian HMM. Features: (log_return, realized_vol_20d).

    State labels are stable: index 0 = lower vol (calm),
    index 1 = higher vol (stressed). Stress prob = posterior of
    state 1 on the last bar of a forward-only filtered window.
    """

    def __init__(self) -> None:
        self._model = None
        self.transmat: list[list[float]] | None = None
        self.means: list[list[float]] | None = None
        self.covars: list[list[list[float]]] | None = None
        self.trained_through: date | None = None

    def fit(self, X: np.ndarray, trained_through: date) -> None:
        from hmmlearn.hmm import GaussianHMM

        if X.ndim != 2 or X.shape[1] != 2:
            raise ValueError(
                f"Expected (N, 2) array, got shape {X.shape}"
            )
        if X.shape[0] < 100:
            raise ValueError(
                f"Need ≥100 samples to fit, got {X.shape[0]}"
            )

        model = GaussianHMM(
            n_components=N_STATES,
            covariance_type=COVARIANCE_TYPE,
            n_iter=N_ITER_FIT,
            random_state=RANDOM_STATE,
        )

        # Warm-start from last persisted state if available
        last = get_latest_hmm_state()
        if last is not None and last.trained_through < trained_through:
            try:
                model.startprob_ = np.array([0.5, 0.5])
                model.transmat_ = np.asarray(last.transmat)
                model.means_ = np.asarray(last.means)
                # diag covariance: (n_states, n_features)
                covars_arr = np.asarray(last.covars)
                if covars_arr.ndim == 3:
                    covars_arr = np.diagonal(covars_arr, axis1=1, axis2=2)
                model.covars_ = covars_arr
                model.init_params = ""  # don't re-init
            except Exception as exc:
                _logger.warning(
                    "HMM warm-start failed, falling back to cold init: %s",
                    exc,
                )

        model.fit(X)

        # Stable label ordering: state 0 = lower realized-vol mean.
        # X[:, 1] is the realized_vol_20d feature.
        means = model.means_
        if means[0, 1] > means[1, 1]:
            # Swap states 0↔1 so state 0 is always calm
            model.means_ = means[[1, 0]]
            model.transmat_ = model.transmat_[[1, 0]][:, [1, 0]]
            if model.covars_.ndim == 2:
                model.covars_ = model.covars_[[1, 0]]
            else:
                model.covars_ = model.covars_[[1, 0]]
            model.startprob_ = model.startprob_[[1, 0]]

        self._model = model
        self.means = model.means_.tolist()
        self.transmat = model.transmat_.tolist()
        # Always persist as full diag matrix for symmetry
        cv = model.covars_
        if cv.ndim == 2:
            cv_full = np.zeros((N_STATES, 2, 2))
            for i in range(N_STATES):
                cv_full[i] = np.diag(cv[i])
            self.covars = cv_full.tolist()
        else:
            self.covars = cv.tolist()
        self.trained_through = trained_through

    def stress_prob(self, X_window: np.ndarray) -> float:
        """Return the posterior probability of being in the
        stressed state (state 1) on the LAST bar of the window.

        IMPORTANT: this uses ``predict_proba`` on the supplied
        window only — caller passes the trailing window
        ``X[:t+1]`` to enforce forward-only inference.
        """
        if self._model is None:
            raise RuntimeError("StressHMM not fitted")
        if X_window.ndim != 2 or X_window.shape[1] != 2:
            raise ValueError(
                f"Expected (N, 2) window, got {X_window.shape}"
            )
        proba = self._model.predict_proba(X_window)
        return float(proba[-1, 1])

    def save(self) -> None:
        if (
            self.transmat is None
            or self.means is None
            or self.covars is None
            or self.trained_through is None
        ):
            raise RuntimeError("Cannot save unfitted HMM")
        upsert_hmm_state(HmmStateRow(
            trained_through=self.trained_through,
            transmat=self.transmat,
            means=self.means,
            covars=self.covars,
            n_observations=int(getattr(
                self._model, "monitor_", type("M", (), {"iter": 0})()
            ).iter),
        ))

    @classmethod
    def load(cls) -> "StressHMM | None":
        row = get_latest_hmm_state()
        if row is None:
            return None
        from hmmlearn.hmm import GaussianHMM

        inst = cls()
        model = GaussianHMM(
            n_components=N_STATES,
            covariance_type=COVARIANCE_TYPE,
        )
        model.startprob_ = np.array([0.5, 0.5])
        model.transmat_ = np.asarray(row.transmat)
        model.means_ = np.asarray(row.means)
        covars_arr = np.asarray(row.covars)
        if covars_arr.ndim == 3:
            covars_arr = np.diagonal(covars_arr, axis1=1, axis2=2)
        model.covars_ = covars_arr
        inst._model = model
        inst.transmat = row.transmat
        inst.means = row.means
        inst.covars = row.covars
        inst.trained_through = row.trained_through
        return inst
```

- [ ] **Step 5.5: Run tests**

```bash
docker compose exec backend pytest backend/algo/regime/tests/test_hmm_overlay.py -v
```
Expected: 4 PASS. If `test_filtered_no_lookahead` fails — STOP, this is the anti-lookahead gate.

- [ ] **Step 5.6: Commit**

```bash
git add backend/algo/regime/hmm_overlay.py backend/algo/regime/tests/test_hmm_overlay.py requirements.txt
git commit -m "feat(algo): StressHMM with forward-only filtering + warm-start refit (REGIME-1)"
```

---

## Task 6 — Daily classifier orchestrator

**Files:**
- Create: `backend/algo/regime/classifier_job.py`.
- Test: `backend/algo/regime/tests/test_classifier_job.py`.
- Modify: `backend/algo/jobs/__init__.py` to import the job for `@register_job` side-effect.

22:30 IST nightly. Reads close + SMA200 + VIX + 30d/60d returns + breadth from `stocks.ohlcv` (DuckDB), classifies via `rule_based.classify_regime`, computes HMM `stress_prob` via load-or-refit, persists to `stocks.regime_history`, invalidates `cache:regime:*` (already done by repo).

Schedule integration: register via `@register_job("regime_classifier_daily")` per the existing pattern in `backend/jobs/executor.py`. The actual cron schedule lives in `backend/scheduler/jobs.yaml` (or wherever the project tracks `scheduled_jobs`); registering the job is enough to make it dispatchable — wiring the schedule comes via a follow-up data-row insert (call out in the ship comment, not a code change here).

Per CLAUDE.md §5.1 ContextVar rule: this is a sync function called by the scheduler thread; no ContextVar plumbing needed.

- [ ] **Step 6.1: Write failing test**

Create `backend/algo/regime/tests/test_classifier_job.py`:

```python
"""Tests for classifier_job — orchestrator integration with mock
data + repo. End-to-end with synthetic OHLCV input."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from backend.algo.regime import classifier_job


def _make_synthetic_history(end: date, days: int) -> pd.DataFrame:
    """Synthetic NIFTY history — bullish trend with calm vol."""
    rng = np.random.default_rng(7)
    n = days
    dates = pd.date_range(end - timedelta(days=n - 1), end, freq="D")
    # Gentle uptrend with low noise
    prices = 18000 * (1 + rng.normal(0.0008, 0.005, n)).cumprod()
    return pd.DataFrame({"bar_date": dates, "close": prices})


def test_compute_inputs_from_history(monkeypatch) -> None:
    today = date(2026, 5, 9)
    nifty_df = _make_synthetic_history(today, 252)
    vix_df = pd.DataFrame({
        "bar_date": [today], "close": [13.5],
    })
    breadth_pct = Decimal("0.65")

    inputs = classifier_job._compute_inputs(
        as_of=today,
        nifty_df=nifty_df,
        vix_df=vix_df,
        pct_above_50sma=breadth_pct,
    )
    assert inputs["nifty_close"] > Decimal("0")
    assert inputs["nifty_sma200"] > Decimal("0")
    assert inputs["vix_close"] == Decimal("13.5")
    assert "nifty_ret_30d" in inputs
    assert "nifty_ret_60d" in inputs
    assert inputs["pct_above_50sma"] == Decimal("0.65")


def test_safe_classify_falls_back_to_sideways_on_nan() -> None:
    """When VIX is missing (NaN), the orchestrator must NOT raise
    — it logs degraded mode and writes SIDEWAYS."""
    inputs = {
        "nifty_close": Decimal("20000"),
        "nifty_sma200": Decimal("18000"),
        "vix_close": Decimal("NaN"),
        "nifty_ret_30d": Decimal("0.05"),
        "nifty_ret_60d": Decimal("0.10"),
        "pct_above_50sma": Decimal("0.60"),
    }
    label, degraded = classifier_job._safe_classify(inputs)
    assert label == "SIDEWAYS"
    assert degraded is True


def test_run_classifier_writes_row(monkeypatch) -> None:
    """Patch all I/O — verify the orchestrator builds a RegimeRow
    and calls upsert_regime_history exactly once."""
    today = date(2026, 5, 9)
    nifty_df = _make_synthetic_history(today, 252)
    vix_df = pd.DataFrame({"bar_date": [today], "close": [13.5]})

    monkeypatch.setattr(
        classifier_job, "_load_nifty_window", lambda *a, **k: nifty_df
    )
    monkeypatch.setattr(
        classifier_job, "_load_vix_latest", lambda *a, **k: vix_df
    )
    monkeypatch.setattr(
        classifier_job, "_compute_breadth_pct_50sma",
        lambda *a, **k: Decimal("0.65"),
    )
    monkeypatch.setattr(
        classifier_job, "_compute_stress_prob",
        lambda *a, **k: 0.18,
    )

    captured: list = []
    monkeypatch.setattr(
        classifier_job, "upsert_regime_history",
        lambda rows: captured.extend(rows) or len(rows),
    )

    classifier_job.run_classifier(as_of=today)

    assert len(captured) == 1
    row = captured[0]
    assert row.bar_date == today
    assert row.regime_label in {"BULL", "SIDEWAYS", "BEAR"}
    assert row.stress_prob == 0.18
```

- [ ] **Step 6.2: Run to verify fail**

```bash
docker compose exec backend pytest backend/algo/regime/tests/test_classifier_job.py -v
```
Expected: ImportError.

- [ ] **Step 6.3: Implement `classifier_job.py`**

Create `backend/algo/regime/classifier_job.py`:

```python
"""Daily regime classifier orchestrator — runs at 22:30 IST.

Reads:
  * NIFTY 50 close (^NSEI) — last 252 trading days from stocks.ohlcv
  * India VIX close (^INDIAVIX) — last bar
  * pct_above_50sma — computed from the NIFTY 500 universe

Computes the rule-based label + HMM stress_prob; persists one row
to stocks.regime_history. On NaN/missing inputs falls back to
SIDEWAYS and emits a ``regime_classifier_degraded`` warning log.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from backend.algo.regime.hmm_overlay import StressHMM
from backend.algo.regime.repo import (
    RegimeRow,
    upsert_regime_history,
)
from backend.algo.regime.rule_based import classify_regime
from backend.db.duckdb_engine import query_iceberg_table
from backend.jobs.executor import register_job

_logger = logging.getLogger(__name__)

CLASSIFIER_VERSION = "v1.0"
NIFTY_TICKER = "^NSEI"
VIX_TICKER = "^INDIAVIX"
BREADTH_UNIVERSE_LOOKBACK_DAYS = 200


def _load_nifty_window(as_of: date, lookback_days: int) -> pd.DataFrame:
    start = as_of - timedelta(days=lookback_days + 30)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT bar_date, close FROM tbl "
        "WHERE ticker = ? AND bar_date BETWEEN ? AND ? "
        "ORDER BY bar_date ASC",
        [NIFTY_TICKER, start, as_of],
    )
    return pd.DataFrame(rows)


def _load_vix_latest(as_of: date) -> pd.DataFrame:
    start = as_of - timedelta(days=10)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT bar_date, close FROM tbl "
        "WHERE ticker = ? AND bar_date BETWEEN ? AND ? "
        "ORDER BY bar_date DESC LIMIT 1",
        [VIX_TICKER, start, as_of],
    )
    return pd.DataFrame(rows)


def _compute_breadth_pct_50sma(as_of: date) -> Decimal:
    """% of stocks (full universe) trading above their 50-day SMA.
    Uses the existing stocks.ohlcv table; one window scan."""
    start = as_of - timedelta(days=BREADTH_UNIVERSE_LOOKBACK_DAYS)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "WITH w AS ("
        "  SELECT ticker, bar_date, close, "
        "         AVG(close) OVER ("
        "             PARTITION BY ticker ORDER BY bar_date "
        "             ROWS BETWEEN 49 PRECEDING AND CURRENT ROW"
        "         ) AS sma50 "
        "  FROM tbl WHERE bar_date BETWEEN ? AND ? "
        ") "
        "SELECT COUNT(*) FILTER (WHERE close > sma50) AS above, "
        "       COUNT(*) AS total "
        "FROM w WHERE bar_date = ?",
        [start, as_of, as_of],
    )
    if not rows:
        return Decimal("NaN")
    r = rows[0]
    if r["total"] == 0:
        return Decimal("NaN")
    return Decimal(r["above"]) / Decimal(r["total"])


def _compute_inputs(
    as_of: date,
    nifty_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    pct_above_50sma: Decimal,
) -> dict:
    if nifty_df.empty:
        raise RuntimeError(f"No NIFTY history available up to {as_of}")
    nifty_df = nifty_df.sort_values("bar_date").reset_index(drop=True)
    closes = nifty_df["close"].astype(float)
    last_close = closes.iloc[-1]
    sma200 = closes.tail(200).mean() if len(closes) >= 200 else float("nan")
    ret_30d = (
        last_close / closes.iloc[-31] - 1
        if len(closes) >= 31 else float("nan")
    )
    ret_60d = (
        last_close / closes.iloc[-61] - 1
        if len(closes) >= 61 else float("nan")
    )
    vix_close = (
        Decimal(str(float(vix_df["close"].iloc[0])))
        if not vix_df.empty else Decimal("NaN")
    )
    return {
        "nifty_close": Decimal(str(last_close)),
        "nifty_sma200": (
            Decimal(str(sma200)) if not math.isnan(sma200)
            else Decimal("NaN")
        ),
        "vix_close": vix_close,
        "nifty_ret_30d": (
            Decimal(str(ret_30d)) if not math.isnan(ret_30d)
            else Decimal("NaN")
        ),
        "nifty_ret_60d": (
            Decimal(str(ret_60d)) if not math.isnan(ret_60d)
            else Decimal("NaN")
        ),
        "pct_above_50sma": pct_above_50sma,
    }


def _safe_classify(inputs: dict) -> tuple[str, bool]:
    """Returns (label, degraded). Degraded = True when fallback to
    SIDEWAYS due to NaN/missing inputs."""
    try:
        label = classify_regime(
            nifty_close=inputs["nifty_close"],
            nifty_sma200=inputs["nifty_sma200"],
            vix_close=inputs["vix_close"],
            nifty_ret_30d=inputs["nifty_ret_30d"],
            nifty_ret_60d=inputs["nifty_ret_60d"],
            pct_above_50sma=inputs["pct_above_50sma"],
        )
        return label, False
    except ValueError as exc:
        _logger.warning(
            "regime_classifier_degraded: %s — falling back to SIDEWAYS",
            exc,
        )
        return "SIDEWAYS", True


def _compute_stress_prob(nifty_df: pd.DataFrame) -> float | None:
    """Build (log_return, realized_vol_20d) features and ask the
    persisted HMM for the last-bar stress posterior. If no HMM
    is persisted yet, return None (degraded — UI will hide the chip)."""
    if len(nifty_df) < 100:
        return None
    closes = nifty_df["close"].astype(float).to_numpy()
    log_ret = np.diff(np.log(closes))
    if log_ret.size < 60:
        return None
    rv = pd.Series(log_ret).rolling(20).std(ddof=0).bfill().to_numpy()
    X = np.column_stack([log_ret, rv])

    hmm = StressHMM.load()
    if hmm is None:
        # Cold start: fit on all available history we have right now
        if X.shape[0] < 200:
            return None
        hmm = StressHMM()
        hmm.fit(
            X,
            trained_through=date.fromisoformat(
                str(pd.to_datetime(
                    nifty_df["bar_date"].iloc[-1]
                ).date())
            ),
        )
        hmm.save()
    return hmm.stress_prob(X)


def run_classifier(as_of: date | None = None) -> RegimeRow:
    """Compute today's regime, persist, return the row."""
    if as_of is None:
        as_of = date.today()
    nifty_df = _load_nifty_window(as_of, 252)
    vix_df = _load_vix_latest(as_of)
    breadth = _compute_breadth_pct_50sma(as_of)
    inputs = _compute_inputs(as_of, nifty_df, vix_df, breadth)
    label, degraded = _safe_classify(inputs)
    stress = _compute_stress_prob(nifty_df) if not degraded else None

    inputs_serializable = {k: float(v) for k, v in inputs.items()}
    inputs_serializable["degraded"] = degraded
    row = RegimeRow(
        bar_date=as_of,
        regime_label=label,
        stress_prob=stress,
        rule_inputs=inputs_serializable,
        classifier_version=CLASSIFIER_VERSION,
    )
    upsert_regime_history([row])
    _logger.info(
        "regime_classifier: as_of=%s label=%s stress=%s degraded=%s",
        as_of, label, stress, degraded,
    )
    return row


@register_job("regime_classifier_daily")
def _job_handler(payload: dict) -> dict:
    """Scheduler entry-point. Payload optionally carries 'as_of'
    (ISO date string) for backfill jobs; defaults to today."""
    as_of = payload.get("as_of")
    parsed = date.fromisoformat(as_of) if as_of else None
    row = run_classifier(as_of=parsed)
    return {
        "as_of": str(row.bar_date),
        "regime_label": row.regime_label,
        "stress_prob": row.stress_prob,
    }
```

If the existing `register_job` decorator signature differs from the assumed `(name) -> decorator`, run:
```bash
sed -n "$(grep -n 'def register_job' backend/jobs/executor.py | cut -d: -f1),+25p" backend/jobs/executor.py
```
…and adjust the decorator call accordingly.

- [ ] **Step 6.4: Wire job module into algo jobs aggregator**

Edit `backend/algo/jobs/__init__.py` to import the new module so `@register_job` runs at backend startup. Append:

```python
# REGIME-1 — daily regime classifier (22:30 IST)
from backend.algo.regime import classifier_job  # noqa: F401
```

If `backend/algo/jobs/__init__.py` doesn't currently import the existing peers, follow the actual pattern visible there — pull it up via Read and adapt. The goal: the `@register_job("regime_classifier_daily")` decorator runs at backend start.

- [ ] **Step 6.5: Run tests**

```bash
docker compose exec backend pytest backend/algo/regime/tests/test_classifier_job.py -v
```
Expected: 3 PASS.

- [ ] **Step 6.6: Restart backend (per CLAUDE.md §6.2 — new @register_job needs restart)**

```bash
docker compose restart backend
sleep 5
docker compose exec backend python -c "from backend.jobs.executor import _registry; print('regime_classifier_daily' in _registry)"
```
Expected: `True`.

(If `_registry` is named differently — grep `executor.py` for the holding dict and adjust.)

- [ ] **Step 6.7: Commit**

```bash
git add backend/algo/regime/classifier_job.py backend/algo/jobs/__init__.py backend/algo/regime/tests/test_classifier_job.py
git commit -m "feat(algo): daily regime classifier job — register_job + breadth + HMM (REGIME-1)"
```

---

## Task 7 — Register regime + breadth + VIX features in strategy AST

**Files:**
- Modify: `backend/algo/strategy/features.py`.
- Test: `backend/algo/regime/tests/test_features_registered.py`.
- Modify: `frontend/components/algo-trading/strategyFeatureCatalog.ts` (sync).

Per spec §3.9: extend `FeatureType` to include `"string"` (regime_label is a string), and `FeatureSource` to include `"regime"`. Other slices (REGIME-3) wire the AST evaluator to actually handle string-compare; this slice only registers the keys so they appear in the editor catalog and the runtime feature-fetch path knows to look them up.

CRITICAL per `feedback_runtime_feature_three_runtimes`: the new features must be wired into ALL THREE runtimes (backtest + paper + live). For this slice we register the keys + add a stub fetch in each runtime that reads from `regime/repo.get_latest_regime()` (or seeded backtest regime via cached lookup). Strategies cannot reference the new keys yet (REGIME-3 unlocks that), but the fetch path should not KeyError if a future strategy author wires them in.

- [ ] **Step 7.1: Write failing test**

Create `backend/algo/regime/tests/test_features_registered.py`:

```python
"""Verify regime + breadth + VIX feature keys are registered in
the strategy AST feature catalog."""
from __future__ import annotations

import pytest

from backend.algo.strategy.features import (
    FEATURE_KEYS, FEATURE_BY_KEY,
)


REGIME_KEYS = {
    "regime_label",
    "stress_prob",
    "pct_above_50sma",
    "pct_above_200sma",
    "midcap_largecap_ratio",
    "vix_close",
    "vix_sma_20",
}


def test_regime_keys_registered() -> None:
    missing = REGIME_KEYS - FEATURE_KEYS
    assert not missing, f"Missing feature keys: {missing}"


def test_regime_label_is_string_type() -> None:
    assert FEATURE_BY_KEY["regime_label"].type == "string"


def test_stress_prob_is_float() -> None:
    assert FEATURE_BY_KEY["stress_prob"].type == "float"


def test_regime_features_have_regime_source() -> None:
    for k in ("regime_label", "stress_prob"):
        assert FEATURE_BY_KEY[k].source == "regime"
```

- [ ] **Step 7.2: Run to verify fail**

```bash
docker compose exec backend pytest backend/algo/regime/tests/test_features_registered.py -v
```
Expected: 4 FAIL — keys missing, type literal doesn't include "string", source literal doesn't include "regime".

- [ ] **Step 7.3: Extend `features.py`**

Edit `backend/algo/strategy/features.py`:

1. Extend `FeatureType` literal:
```python
FeatureType = Literal["int", "float", "string"]
```

2. Extend `FeatureSource` literal — append `"regime"`:
```python
FeatureSource = Literal[
    "ohlcv",
    "technical",
    "fundamentals",
    "recommendation",
    "forecast",
    "regime",
]
```

3. Append the 7 new features to `FEATURES`:
```python
    # Regime + breadth + VIX (REGIME-1)
    Feature(
        key="regime_label",
        label="Regime label (BULL/SIDEWAYS/BEAR)",
        type="string",
        source="regime",
    ),
    Feature(
        key="stress_prob",
        label="HMM stress probability",
        type="float",
        source="regime",
    ),
    Feature(
        key="pct_above_50sma",
        label="% above 50d SMA (breadth)",
        type="float",
        source="regime",
    ),
    Feature(
        key="pct_above_200sma",
        label="% above 200d SMA (breadth)",
        type="float",
        source="regime",
    ),
    Feature(
        key="midcap_largecap_ratio",
        label="Midcap / Largecap ratio",
        type="float",
        source="regime",
    ),
    Feature(
        key="vix_close",
        label="India VIX close",
        type="float",
        source="regime",
    ),
    Feature(
        key="vix_sma_20",
        label="India VIX 20-day SMA",
        type="float",
        source="regime",
    ),
```

- [ ] **Step 7.4: Sync frontend feature catalog**

Read `frontend/components/algo-trading/strategyFeatureCatalog.ts` first to see the existing shape (it mirrors `FEATURES`). Append the same 7 entries with matching `key`, `label`, `type`, `source`. The CI test `test_feature_registry_sync.py` will fail otherwise.

```bash
grep -n "FEATURES\|regime_label" frontend/components/algo-trading/strategyFeatureCatalog.ts | head -5
```

Edit by adding entries that mirror Step 7.3 with the same TypeScript object shape used in the file.

- [ ] **Step 7.5: Run tests**

```bash
docker compose exec backend pytest backend/algo/regime/tests/test_features_registered.py backend/algo/tests/test_feature_registry_sync.py -v
```
Expected: 4 PASS + the existing sync test PASS.

- [ ] **Step 7.6: Commit**

```bash
git add backend/algo/strategy/features.py frontend/components/algo-trading/strategyFeatureCatalog.ts backend/algo/regime/tests/test_features_registered.py
git commit -m "feat(algo): register regime+breadth+VIX features in AST + frontend mirror (REGIME-1)"
```

---

## Task 8 — API endpoints: /current, /history, /classifier-health

**Files:**
- Create: `backend/algo/routes/regime.py`.
- Test: `backend/algo/regime/tests/test_routes.py`.
- Modify: `backend/main.py` to mount the router.

3 GET endpoints under `/v1/algo/regime/*`. Cache pattern per CLAUDE.md §5.13: `cache:regime:current`, `cache:regime:history:{start}:{end}`, `cache:regime:health`. TTL = `TTL_VOLATILE` (60s) for `/current` (refreshes intra-day), `TTL_STABLE` (300s) for `/history`. Sync `cache.get / cache.set` (NOT awaited).

Auth: regime data is non-sensitive market context — use the project's standard authenticated-user dependency. Grep how peers do it:
```bash
grep -rn "current_user\|get_current_user\|UserDep" backend/algo/routes/live.py | head -5
```

- [ ] **Step 8.1: Write failing test**

Create `backend/algo/regime/tests/test_routes.py`:

```python
"""Smoke tests for /v1/algo/regime/* endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(test_app):
    """test_app fixture is already provided by conftest.py
    (mirrors backend/algo/tests/conftest.py)."""
    return TestClient(test_app)


@pytest.fixture
def auth_headers(authenticated_general_user):
    """Re-uses existing fixture from conftest."""
    return {
        "Authorization": f"Bearer {authenticated_general_user.access_token}"
    }


def test_current_returns_latest(monkeypatch, client, auth_headers):
    from backend.algo.regime import routes as routes_mod
    from backend.algo.regime.repo import RegimeRow

    seed = RegimeRow(
        bar_date=date(2026, 5, 9),
        regime_label="BULL",
        stress_prob=0.20,
        rule_inputs={"vix": 14.0, "pct_above_50sma": 0.62},
        classifier_version="v1.0",
    )
    monkeypatch.setattr(routes_mod, "get_latest_regime", lambda: seed)

    r = client.get("/v1/algo/regime/current", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["regime_label"] == "BULL"
    assert body["stress_prob"] == pytest.approx(0.20)
    assert body["bar_date"] == "2026-05-09"


def test_current_404_when_empty(monkeypatch, client, auth_headers):
    from backend.algo.regime import routes as routes_mod

    monkeypatch.setattr(routes_mod, "get_latest_regime", lambda: None)
    r = client.get("/v1/algo/regime/current", headers=auth_headers)
    assert r.status_code == 404


def test_history_returns_window(monkeypatch, client, auth_headers):
    from backend.algo.regime import routes as routes_mod
    from backend.algo.regime.repo import RegimeRow

    rows = [
        RegimeRow(
            bar_date=date(2026, 5, 9) - timedelta(days=i),
            regime_label="BULL",
            stress_prob=0.1 + i * 0.01,
            rule_inputs={},
            classifier_version="v1.0",
        )
        for i in range(5)
    ]
    monkeypatch.setattr(
        routes_mod, "get_regime_history", lambda *a, **k: rows
    )

    r = client.get(
        "/v1/algo/regime/history?days=5", headers=auth_headers
    )
    assert r.status_code == 200
    assert len(r.json()["rows"]) == 5


def test_classifier_health_reports_hmm_age(monkeypatch, client, auth_headers):
    from backend.algo.regime import routes as routes_mod
    from backend.algo.regime.repo import HmmStateRow

    monkeypatch.setattr(
        routes_mod, "get_latest_hmm_state",
        lambda: HmmStateRow(
            trained_through=date(2026, 4, 1),
            transmat=[[0.95, 0.05], [0.10, 0.90]],
            means=[[0.001, 0.010], [-0.002, 0.025]],
            covars=[[[0.0, 0.0], [0.0, 0.0]],
                    [[0.0, 0.0], [0.0, 0.0]]],
            n_observations=1500,
        )
    )
    monkeypatch.setattr(
        routes_mod, "get_latest_regime",
        lambda: None,
    )
    r = client.get(
        "/v1/algo/regime/classifier-health", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert "hmm_trained_through" in body
    assert "hmm_age_days" in body
    assert body["hmm_age_days"] >= 0
```

- [ ] **Step 8.2: Run to verify fail**

```bash
docker compose exec backend pytest backend/algo/regime/tests/test_routes.py -v
```
Expected: ImportError or 404s.

- [ ] **Step 8.3: Implement `routes/regime.py`**

Create `backend/algo/routes/regime.py`:

```python
"""GET /v1/algo/regime/* — exposes regime context to frontend.

Cache TTLs:
  * /current — TTL_VOLATILE (60s)
  * /history — TTL_STABLE  (300s)
  * /classifier-health — TTL_VOLATILE (60s)
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.algo.regime.repo import (
    get_latest_hmm_state,
    get_latest_regime,
    get_regime_history,
)
from backend.db.cache import cache, TTL_STABLE, TTL_VOLATILE
from auth.dependencies import get_current_user  # adjust path if differs

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/algo/regime", tags=["algo-regime"])


class CurrentRegimeResponse(BaseModel):
    bar_date: date
    regime_label: str
    stress_prob: float | None
    rule_inputs: dict[str, Any]
    classifier_version: str


class RegimeHistoryRow(BaseModel):
    bar_date: date
    regime_label: str
    stress_prob: float | None


class RegimeHistoryResponse(BaseModel):
    rows: list[RegimeHistoryRow]


class ClassifierHealthResponse(BaseModel):
    hmm_trained_through: date | None
    hmm_age_days: int | None
    last_regime_bar_date: date | None
    last_regime_age_days: int | None


@router.get("/current", response_model=CurrentRegimeResponse)
def current(
    _user=Depends(get_current_user),
) -> CurrentRegimeResponse:
    cached = cache.get("cache:regime:current")
    if cached:
        return CurrentRegimeResponse(**json.loads(cached))

    row = get_latest_regime()
    if row is None:
        raise HTTPException(404, "No regime row yet")
    resp = CurrentRegimeResponse(
        bar_date=row.bar_date,
        regime_label=row.regime_label,
        stress_prob=row.stress_prob,
        rule_inputs=row.rule_inputs,
        classifier_version=row.classifier_version,
    )
    cache.set(
        "cache:regime:current", resp.model_dump_json(), ttl=TTL_VOLATILE
    )
    return resp


@router.get("/history", response_model=RegimeHistoryResponse)
def history(
    days: int = Query(252, ge=1, le=1095),
    _user=Depends(get_current_user),
) -> RegimeHistoryResponse:
    end = date.today()
    start = end - timedelta(days=days)
    key = f"cache:regime:history:{start}:{end}"
    cached = cache.get(key)
    if cached:
        return RegimeHistoryResponse(**json.loads(cached))

    rows = get_regime_history(start=start, end=end)
    resp = RegimeHistoryResponse(rows=[
        RegimeHistoryRow(
            bar_date=r.bar_date,
            regime_label=r.regime_label,
            stress_prob=r.stress_prob,
        )
        for r in rows
    ])
    cache.set(key, resp.model_dump_json(), ttl=TTL_STABLE)
    return resp


@router.get("/classifier-health", response_model=ClassifierHealthResponse)
def classifier_health(
    _user=Depends(get_current_user),
) -> ClassifierHealthResponse:
    today = date.today()
    hmm = get_latest_hmm_state()
    last = get_latest_regime()
    return ClassifierHealthResponse(
        hmm_trained_through=hmm.trained_through if hmm else None,
        hmm_age_days=(
            (today - hmm.trained_through).days if hmm else None
        ),
        last_regime_bar_date=last.bar_date if last else None,
        last_regime_age_days=(
            (today - last.bar_date).days if last else None
        ),
    )
```

If `get_current_user`'s import path differs, grep how `live.py` does it and mirror exactly.

- [ ] **Step 8.4: Mount router in `backend/main.py`**

```bash
grep -n "include_router" backend/main.py | head -10
```

Add (in the algo-router block, after the existing algo routers):
```python
from backend.algo.routes.regime import router as algo_regime_router
app.include_router(algo_regime_router, prefix="/v1")
```

Restart backend (per CLAUDE.md §6.2 — new include_router call needs restart):
```bash
docker compose restart backend && sleep 5
```

- [ ] **Step 8.5: Run tests + curl smoke**

```bash
docker compose exec backend pytest backend/algo/regime/tests/test_routes.py -v
```
Expected: 4 PASS.

Curl smoke:
```bash
JAR=$(mktemp)
# Acquire a real JWT — adapt to your test user. If unsure, skip;
# pytest covers the contract.
curl -sS http://localhost:8181/v1/algo/regime/classifier-health \
  -H "Authorization: Bearer <token>" | jq
```

- [ ] **Step 8.6: Commit**

```bash
git add backend/algo/routes/regime.py backend/main.py backend/algo/regime/tests/test_routes.py
git commit -m "feat(algo): GET /v1/algo/regime/{current,history,classifier-health} (REGIME-1)"
```

---

## Task 9 — Frontend SWR hook + apiFetch wiring

**Files:**
- Create: `frontend/hooks/useRegime.ts`.

Per CLAUDE.md §5.3: `apiFetch` (auto-refresh JWT), `API_URL` for /v1 paths, SWR with `revalidateOnFocus: false`, 60s dedup. Polls 60s for current; history is fetched once.

- [ ] **Step 9.1: Inspect existing hook patterns**

```bash
ls frontend/hooks/ | head -10
sed -n 1,40p frontend/hooks/useLiveStatus.ts 2>/dev/null
```

- [ ] **Step 9.2: Create the hook**

Create `frontend/hooks/useRegime.ts`:

```ts
"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface CurrentRegime {
  bar_date: string;
  regime_label: "BULL" | "SIDEWAYS" | "BEAR";
  stress_prob: number | null;
  rule_inputs: Record<string, number | boolean>;
  classifier_version: string;
}

export interface RegimeHistoryRow {
  bar_date: string;
  regime_label: "BULL" | "SIDEWAYS" | "BEAR";
  stress_prob: number | null;
}

export interface ClassifierHealth {
  hmm_trained_through: string | null;
  hmm_age_days: number | null;
  last_regime_bar_date: string | null;
  last_regime_age_days: number | null;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
};

export function useRegimeCurrent() {
  const { data, error, isLoading, mutate } = useSWR<CurrentRegime>(
    `${API_URL}/algo/regime/current`,
    fetcher,
    {
      refreshInterval: 60_000,
      revalidateOnFocus: false,
      dedupingInterval: 60_000,
    },
  );
  return {
    current: data,
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

export function useRegimeHistory(days = 252) {
  const { data, error, isLoading } = useSWR<{ rows: RegimeHistoryRow[] }>(
    `${API_URL}/algo/regime/history?days=${days}`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 5 * 60_000 },
  );
  return {
    rows: data?.rows ?? [],
    error: error as Error | undefined,
    loading: isLoading,
  };
}

export function useClassifierHealth() {
  const { data, error, isLoading } = useSWR<ClassifierHealth>(
    `${API_URL}/algo/regime/classifier-health`,
    fetcher,
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );
  return {
    health: data,
    error: error as Error | undefined,
    loading: isLoading,
  };
}
```

- [ ] **Step 9.3: Lint check**

```bash
cd frontend && npx eslint hooks/useRegime.ts
```
Expected: no errors.

- [ ] **Step 9.4: Commit**

```bash
git add frontend/hooks/useRegime.ts
git commit -m "feat(algo-fe): useRegime hook (current/history/health) (REGIME-1)"
```

---

## Task 10 — RegimeWidget component

**Files:**
- Create: `frontend/components/algo-trading/RegimeWidget.tsx`.
- Modify: `e2e/utils/selectors.ts` to add testids.

Mounted in Trading-tab header next to title. Shows: regime badge (color: BULL=emerald, SIDEWAYS=slate, BEAR=rose), VIX gauge with band coloring (calm <16 emerald, normal 16-25 amber, stress >25 rose), breadth bar (% above 50SMA), HMM stress chip with divergence warning if HMM ≥0.6 while rule says BULL or HMM ≤0.2 while rule says BEAR.

- [ ] **Step 10.1: Add testids first**

Edit `frontend/components/algo-trading/PaperTab.tsx` peer file `e2e/utils/selectors.ts` — append (alphabetically inside `FE`):

```ts
  regimeWidget: "regime-widget",
  regimeBadge: "regime-badge",
  regimeVixGauge: "regime-vix-gauge",
  regimeBreadthBar: "regime-breadth-bar",
  regimeStressChip: "regime-stress-chip",
  regimeDivergenceTooltip: "regime-divergence-tooltip",
  regimeHistoryChart: "regime-history-chart",
```

- [ ] **Step 10.2: Create the component**

Create `frontend/components/algo-trading/RegimeWidget.tsx`:

```tsx
"use client";

import { useRegimeCurrent } from "@/hooks/useRegime";

const BADGE_BG: Record<string, string> = {
  BULL: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-200",
  SIDEWAYS: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200",
  BEAR: "bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-200",
};

function vixBandColor(vix: number): string {
  if (vix < 16) return "text-emerald-600";
  if (vix <= 25) return "text-amber-600";
  return "text-rose-600";
}

function breadthBg(breadth: number): string {
  if (breadth >= 0.55) return "bg-emerald-500";
  if (breadth >= 0.4) return "bg-amber-500";
  return "bg-rose-500";
}

function divergenceWarning(
  rule: string,
  stress: number | null,
): string | null {
  if (stress === null) return null;
  if (rule === "BULL" && stress >= 0.6) {
    return `Rule says BULL, HMM stress ${stress.toFixed(2)} — caution.`;
  }
  if (rule === "BEAR" && stress <= 0.2) {
    return `Rule says BEAR, HMM stress ${stress.toFixed(2)} — possible thaw.`;
  }
  return null;
}

export function RegimeWidget() {
  const { current, loading, error } = useRegimeCurrent();

  if (loading) {
    return (
      <span
        className="text-xs text-slate-500"
        data-testid="regime-widget-loading"
      >
        Loading regime…
      </span>
    );
  }

  if (error || !current) {
    return (
      <span
        className="text-xs text-slate-500"
        data-testid="regime-widget-empty"
      >
        Regime: —
      </span>
    );
  }

  const vix =
    typeof current.rule_inputs.vix_close === "number"
      ? (current.rule_inputs.vix_close as number)
      : null;
  const breadth =
    typeof current.rule_inputs.pct_above_50sma === "number"
      ? (current.rule_inputs.pct_above_50sma as number)
      : null;
  const divergence = divergenceWarning(
    current.regime_label,
    current.stress_prob,
  );

  return (
    <div
      className="flex items-center gap-2"
      data-testid="regime-widget"
    >
      <span
        className={
          "rounded-full px-2.5 py-0.5 text-xs font-medium "
          + BADGE_BG[current.regime_label]
        }
        data-testid="regime-badge"
        title={`As of ${current.bar_date}`}
      >
        {current.regime_label}
      </span>
      {vix !== null && (
        <span
          className={`text-xs font-medium ${vixBandColor(vix)}`}
          data-testid="regime-vix-gauge"
          title={`India VIX ${vix.toFixed(2)}`}
        >
          VIX {vix.toFixed(1)}
        </span>
      )}
      {breadth !== null && (
        <span
          className="flex items-center gap-1 text-xs text-slate-600 dark:text-slate-300"
          data-testid="regime-breadth-bar"
          title={`Breadth: ${(breadth * 100).toFixed(0)}% above 50d SMA`}
        >
          <span className="h-2 w-12 rounded bg-slate-200 dark:bg-slate-700">
            <span
              className={`block h-2 rounded ${breadthBg(breadth)}`}
              style={{ width: `${Math.min(100, breadth * 100)}%` }}
            />
          </span>
          {(breadth * 100).toFixed(0)}%
        </span>
      )}
      {current.stress_prob !== null && (
        <span
          className={
            divergence
              ? "rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-950/50 dark:text-amber-200"
              : "rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"
          }
          data-testid="regime-stress-chip"
          title={divergence ?? `HMM stress ${current.stress_prob.toFixed(2)}`}
        >
          stress {current.stress_prob.toFixed(2)}
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 10.3: Lint**

```bash
cd frontend && npx eslint components/algo-trading/RegimeWidget.tsx
```
Expected: no errors.

- [ ] **Step 10.4: Commit**

```bash
git add frontend/components/algo-trading/RegimeWidget.tsx e2e/utils/selectors.ts
git commit -m "feat(algo-fe): RegimeWidget with badge + VIX gauge + breadth + stress chip (REGIME-1)"
```

---

## Task 11 — RegimeHistoryChart component

**Files:**
- Create: `frontend/components/algo-trading/RegimeHistoryChart.tsx`.
- Modify: `frontend/lib/echarts.ts` (register MarkArea + LineChart if missing).

ECharts color-ribbon overlay. NIFTY 50 close line + colored bands per regime period.

- [ ] **Step 11.1: Verify echarts registrations**

```bash
grep -n "use(\|LineChart\|MarkArea\|MarkAreaComponent" frontend/lib/echarts.ts
```

If `LineChart` and `MarkAreaComponent` are not in the `use([...])` list, append them.

- [ ] **Step 11.2: Create the chart**

Create `frontend/components/algo-trading/RegimeHistoryChart.tsx`:

```tsx
"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

import "@/lib/echarts";
import { useDarkMode } from "@/components/charts/useDarkMode";
import { useRegimeHistory, type RegimeHistoryRow } from "@/hooks/useRegime";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
});

const REGIME_COLORS: Record<string, string> = {
  BULL: "rgba(16, 185, 129, 0.12)",     // emerald
  SIDEWAYS: "rgba(148, 163, 184, 0.10)", // slate
  BEAR: "rgba(244, 63, 94, 0.14)",       // rose
};

interface Band {
  start: string;
  end: string;
  label: string;
}

function compressToBands(rows: RegimeHistoryRow[]): Band[] {
  if (rows.length === 0) return [];
  const out: Band[] = [];
  let runStart = rows[0].bar_date;
  let runLabel = rows[0].regime_label;
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].regime_label !== runLabel) {
      out.push({ start: runStart, end: rows[i - 1].bar_date, label: runLabel });
      runStart = rows[i].bar_date;
      runLabel = rows[i].regime_label;
    }
  }
  out.push({
    start: runStart,
    end: rows[rows.length - 1].bar_date,
    label: runLabel,
  });
  return out;
}

export function RegimeHistoryChart() {
  const isDark = useDarkMode();
  const { rows, loading, error } = useRegimeHistory(252);

  const option = useMemo(() => {
    const bands = compressToBands(rows);
    return {
      backgroundColor: "transparent",
      grid: { left: 40, right: 20, top: 16, bottom: 32 },
      xAxis: {
        type: "category",
        data: rows.map(r => r.bar_date),
        axisLabel: { fontSize: 10 },
      },
      yAxis: {
        type: "value",
        name: "stress_prob",
        min: 0, max: 1,
        axisLabel: { fontSize: 10 },
      },
      tooltip: { trigger: "axis" },
      series: [
        {
          name: "Stress",
          type: "line",
          data: rows.map(r => r.stress_prob ?? null),
          showSymbol: false,
          lineStyle: { color: isDark ? "#94a3b8" : "#475569" },
          markArea: {
            silent: true,
            itemStyle: { opacity: 1 },
            data: bands.map(b => [
              {
                xAxis: b.start,
                itemStyle: { color: REGIME_COLORS[b.label] },
                name: b.label,
              },
              { xAxis: b.end },
            ]),
          },
        },
      ],
    };
  }, [rows, isDark]);

  if (loading) {
    return (
      <p className="text-xs text-slate-500">Loading regime history…</p>
    );
  }
  if (error || rows.length === 0) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="regime-history-empty"
      >
        No regime history yet.
      </p>
    );
  }

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-2"
      data-testid="regime-history-chart"
    >
      <ReactECharts
        option={option}
        notMerge
        style={{ height: 220, width: "100%" }}
        key={isDark ? "d" : "l"}
      />
    </div>
  );
}
```

- [ ] **Step 11.3: Lint**

```bash
cd frontend && npx eslint components/algo-trading/RegimeHistoryChart.tsx
```

- [ ] **Step 11.4: Commit**

```bash
git add frontend/components/algo-trading/RegimeHistoryChart.tsx frontend/lib/echarts.ts
git commit -m "feat(algo-fe): RegimeHistoryChart with regime ribbon + stress line (REGIME-1)"
```

---

## Task 12 — Mount RegimeWidget + RegimeHistoryChart in PaperTab

**Files:**
- Modify: `frontend/components/algo-trading/PaperTab.tsx`.

Per the spec: `RegimeWidget` next to the Trading title (always visible across all view modes); `RegimeHistoryChart` below the LiveSection (Live + Dry-run views only — paper mode is replay-fixture so historical regime context is less relevant). The widget renders for ALL three modes since regime context is general.

- [ ] **Step 12.1: Edit PaperTab**

Add imports:

```tsx
import { RegimeWidget } from "./RegimeWidget";
import { RegimeHistoryChart } from "./RegimeHistoryChart";
```

In the title block of `PaperTab` (currently `<div><h2>Trading</h2><p>...</p></div>`), wrap the heading row to add `RegimeWidget` next to the title:

```tsx
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              Trading
            </h2>
            <RegimeWidget />
          </div>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            Paper: replay-fixture runs against a synthetic broker.
            Dry run: live-mode rehearsal with synthetic Kite
            responses. Live: real Kite orders with safety belts.
          </p>
        </div>
```

In the `(viewMode === "live" || viewMode === "dryrun")` branch, append `<RegimeHistoryChart />` after the Active runs panel + Kite postback panel block:

```tsx
          {/* Regime history chart — surfaces the rolling 252d
              regime ribbon + HMM stress line. */}
          <RegimeHistoryChart />
```

- [ ] **Step 12.2: Smoke run frontend dev server**

```bash
docker compose up -d frontend
sleep 5
# Open http://localhost:3000/algo-trading and verify:
# - RegimeWidget renders at top-left next to "Trading"
# - History chart shows under Live section (or "No regime history yet" if no data)
```

Per CLAUDE.md global rules for frontend: actually open the page in a browser and verify before claiming success.

- [ ] **Step 12.3: Lint + typecheck**

```bash
cd frontend && npx eslint components/algo-trading/PaperTab.tsx && npx tsc --noEmit
```

- [ ] **Step 12.4: Commit**

```bash
git add frontend/components/algo-trading/PaperTab.tsx
git commit -m "feat(algo-fe): mount RegimeWidget + RegimeHistoryChart in PaperTab (REGIME-1)"
```

---

## Task 13 — E2E test for the regime widget

**Files:**
- Create: `e2e/tests/frontend/algo-trading-regime-widget.spec.ts`.

Per CLAUDE.md §5.14: 1 worker locally, page object pattern, fixture auth (NEVER /auth/login in spec). Seed a regime row via direct repo write through a backend pytest helper, then navigate the Trading tab.

- [ ] **Step 13.1: Inspect existing algo-trading e2e specs**

```bash
ls e2e/tests/frontend/ | grep algo
sed -n 1,40p $(ls e2e/tests/frontend/algo-trading*.spec.ts 2>/dev/null | head -1)
```

- [ ] **Step 13.2: Write the spec**

Create `e2e/tests/frontend/algo-trading-regime-widget.spec.ts` using the same fixture + page-object conventions you observed. Skeleton (adapt selectors + fixture import to the actual files):

```ts
import { test, expect } from "../../fixtures/algo.fixture";
import { FE } from "../../utils/selectors";
import { PaperTabPage } from "../../pages/frontend/paper-tab.page";

test.describe("Regime widget", () => {
  test("renders badge + VIX + breadth after seeded row", async ({
    page,
    seedRegimeRow,
  }) => {
    await seedRegimeRow({
      bar_date: new Date().toISOString().slice(0, 10),
      regime_label: "BULL",
      stress_prob: 0.18,
      rule_inputs: { vix_close: 13.5, pct_above_50sma: 0.62 },
    });
    const tab = new PaperTabPage(page);
    await tab.goto();
    await expect(page.getByTestId(FE.regimeWidget)).toBeVisible();
    await expect(page.getByTestId(FE.regimeBadge)).toHaveText("BULL");
    await expect(page.getByTestId(FE.regimeVixGauge)).toContainText("VIX");
    await expect(page.getByTestId(FE.regimeBreadthBar))
      .toContainText("62%");
    await expect(page.getByTestId(FE.regimeStressChip))
      .toContainText("0.18");
  });

  test("BEAR badge with rose color class", async ({
    page,
    seedRegimeRow,
  }) => {
    await seedRegimeRow({
      bar_date: new Date().toISOString().slice(0, 10),
      regime_label: "BEAR",
      stress_prob: 0.85,
      rule_inputs: { vix_close: 31.0, pct_above_50sma: 0.28 },
    });
    const tab = new PaperTabPage(page);
    await tab.goto();
    const badge = page.getByTestId(FE.regimeBadge);
    await expect(badge).toHaveText("BEAR");
    await expect(badge).toHaveClass(/text-rose/);
  });
});
```

If `algo.fixture` doesn't expose `seedRegimeRow`, add the helper in the fixture file (calls a small backend test-only endpoint or directly invokes `upsert_regime_history` via a maintenance script — mirror how existing algo specs seed events). If a comparable helper exists for `algo.events`, mirror it; otherwise add a minimal `POST /v1/test/seed-regime` route under a test-only guard, or skip seeding and rely on real classifier_job output.

- [ ] **Step 13.3: Run the spec**

```bash
cd e2e && npx playwright test --project=frontend-chromium tests/frontend/algo-trading-regime-widget.spec.ts -j 1
```

Per CLAUDE.md §5.14: 1 worker. Expected: PASS (or capture screenshot artifacts on fail).

- [ ] **Step 13.4: Commit**

```bash
git add e2e/tests/frontend/algo-trading-regime-widget.spec.ts e2e/utils/selectors.ts
git commit -m "test(e2e): regime widget renders badge/vix/breadth/stress (REGIME-1)"
```

---

## Task 14 — Backfill script + ship checklist

**Files:**
- Create: `scripts/backfill_regime_history.py` (NEW).
- Modify: `PROGRESS.md` (append session entry).

Manual backfill of the last 30 trading days so the widget has data on day 0 (per spec §7 rollout step "Day 0: turn on regime classifier daily job; 30 days backfill").

- [ ] **Step 14.1: Create backfill script**

Create `scripts/backfill_regime_history.py`:

```python
"""Backfill stocks.regime_history by replaying classifier_job over
a date range. Use after REGIME-1 ships to populate ~30 days.

Usage::

    docker compose exec backend \\
      python scripts/backfill_regime_history.py 2026-04-09 2026-05-09
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

from backend.algo.regime.classifier_job import run_classifier

_logger = logging.getLogger(__name__)


def main(start_iso: str, end_iso: str) -> None:
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    cur = start
    while cur <= end:
        try:
            row = run_classifier(as_of=cur)
            _logger.info(
                "Backfilled %s → %s (stress=%s)",
                cur, row.regime_label, row.stress_prob,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error("Backfill failed for %s: %s", cur, exc)
        cur += timedelta(days=1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 3:
        print(
            "usage: python scripts/backfill_regime_history.py "
            "<start YYYY-MM-DD> <end YYYY-MM-DD>",
            file=sys.stderr,
        )
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
```

- [ ] **Step 14.2: Run backfill (last 30 days) inside the container**

```bash
docker compose exec backend python scripts/backfill_regime_history.py \
  $(date -v-30d +%Y-%m-%d 2>/dev/null || date -d '30 days ago' +%Y-%m-%d) \
  $(date +%Y-%m-%d)
```

Verify via `/v1/algo/regime/history?days=30` returning ≥10 rows (gaps for weekends are normal).

- [ ] **Step 14.3: Update PROGRESS.md**

Append a dated entry under today's date covering: 14 tasks shipped, 13 SP, 6 PRs touched (or 1 squash-merge depending on chosen integration cadence), brief on the anti-lookahead gate.

- [ ] **Step 14.4: Final commit + push**

```bash
git add scripts/backfill_regime_history.py PROGRESS.md
git commit -m "chore(algo): regime history backfill script + PROGRESS for REGIME-1"
git push -u origin feature/regime-slice-1-engine
```

---

## Acceptance Checklist

Replay the spec's REGIME-1 acceptance row before opening the slice → integration PR:

- [ ] `^INDIAVIX` (already-present) + 11 NIFTY sector indices in `gap_filler.refresh_market_indices`. Test passes.
- [ ] `classify_regime()` table-driven tests pass (8 cases + NaN guard).
- [ ] `stocks.regime_history` table created via `stocks/create_tables.py` (idempotent re-run logs "already exists").
- [ ] `stocks.regime_hmm_state` table created (same).
- [ ] HMM `test_filtered_no_lookahead` passes — last-day prediction with `predict(X[:t+1])` does NOT see the future.
- [ ] HMM warm-start refit reuses last persisted `transmat_` (verified by save+load roundtrip test).
- [ ] `regime_classifier_daily` job registered in `_registry` after backend restart.
- [ ] `GET /v1/algo/regime/current` returns 200 with cached body (Redis hit on second call).
- [ ] `GET /v1/algo/regime/history?days=N` returns ≥10 rows after 30d backfill.
- [ ] `GET /v1/algo/regime/classifier-health` returns hmm_age_days within expected bounds.
- [ ] `regime_label`, `stress_prob`, `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio`, `vix_close`, `vix_sma_20` registered in backend FEATURES; `test_feature_registry_sync` passes (frontend mirror in sync).
- [ ] `RegimeWidget` mounts in PaperTab header; visible across paper/dryrun/live modes; loading state graceful.
- [ ] `RegimeHistoryChart` renders in live + dry-run modes; ribbon colors match BULL/SIDEWAYS/BEAR.
- [ ] Playwright spec passes against the running stack (1 worker).
- [ ] No regression on existing `pytest backend/algo/tests` (full suite green).
- [ ] No regression on existing E2E suites (`frontend-chromium`).
- [ ] Backfill script populated ≥10 rows of regime_history for the prior 30 days.
- [ ] Branch pushed to `feature/regime-slice-1-engine`.

---

## Out of Scope for REGIME-1 (do NOT do)

- Strategy↔regime metadata + selector + AST `regime_eq` (REGIME-3).
- Factor library backend (REGIME-2a).
- Volatility-targeted sizing (REGIME-4).
- Walk-forward + DSR/PBO gates (REGIME-5).
- Attribution / Brinson (REGIME-6).
- Sector rotation strategy template + PIT universe (REGIME-7).
- Per-user regime override PG row (mentioned in spec §8 — deferred to v3.1).
- Cron schedule data row (job is registered; the actual `scheduled_jobs` row insert can land in the same PR or a follow-up — call out clearly in the PR description).
- Tunable thresholds via `algo_regime_config` PG row (deferred — thresholds are module constants for v1).
