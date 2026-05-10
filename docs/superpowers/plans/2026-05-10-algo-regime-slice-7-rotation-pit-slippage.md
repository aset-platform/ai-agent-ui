# Regime-Aware Multi-Factor System — Slice REGIME-7: PIT Universe + Slippage + Sector Rotation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Final v3 hardening — point-in-time universe snapshot (eliminates survivorship bias), ADTV-scaled slippage in backtest (replaces fixed bps), AST 2007-01-01 backtest start floor, and a sector rotation strategy template using the factor library + regime overlay.

**Architecture:**
- Monthly job (1st Sunday 03:00 IST) rebuilds `stocks.universe_snapshot` Iceberg table partitioned by `year(rebalance_date)`. Top-200 by 60d ADTV from active stock_master with `market_cap ≥ ₹500cr`. Listing-age filter deferred (no `listing_date` column in stock_master — substituted with `created_at`-based proxy where possible).
- `pit_resolver.resolve_pit_universe(bar_date)` queries the snapshot ≤ bar_date.
- `SimBroker.execute()` applies `max(5, 50 × order_value / adtv) bps` slippage as a price adjustment after fee computation. Live runtime untouched.
- `BacktestRequest` (Pydantic) gets a `field_validator` rejecting `period_start < 2007-01-01`.
- Sector rotation template = JSON strategy + thin Python loader + reference doc.

**Tech Stack:** Python 3.12, PyIceberg, pandas, Pydantic v2. NO new deps.

**Spec:** §3.7 (universe snapshot), §3.8 (slippage), §4.1, §5.1 REGIME-7, §6.1.
**Research:** §8 (sector rotation NSE), §9 (rolling universe survivorship), §10 (anti-pattern: 2007 floor + slippage formula).

**Branch:** `feature/regime-slice-7-rotation-pit-slippage` (already created).

**Estimated SP:** 13

---

## Pre-flight

Verify BEFORE each task:

- **Iceberg helpers:** `_create_table`, `_get_catalog`, `_NAMESPACE` in `stocks/create_tables.py`.
- **DuckDB read:** `query_iceberg_table` in `backend/db/duckdb_engine.py` — uses SHORT table name in SQL.
- **OHLCV column is `date`** (NOT `bar_date`).
- **`stock_master` model:** `backend/db/models/stock_master.py` — has `market_cap` (int|None), `is_active` (bool), `created_at`. NO `listing_date` field. Use `created_at` as proxy for listing-age filter, OR skip the filter for v3 with a TODO.
- **SimBroker:** `backend/algo/backtest/sim_broker.py:25` `class SimBroker`; `execute()` at line ~46 returns `Fill` with `fill_price=next_bar.open`. Slippage hooks here.
- **BacktestRequest:** `backend/algo/backtest/types.py:18` Pydantic with `period_start: date`. Add `field_validator` for floor.
- **WalkForwardConfig:** `backend/algo/backtest/walkforward.py` ALSO has `period_start` (REGIME-5). Same floor must apply.
- **Existing universe code:** `backend/algo/backtest/universe.py::resolve_universe(user, strategy)` (async). Wrap with PIT check OR have callers call `resolve_pit_universe` directly when applicable.
- **`@register_job`** wrappers in `backend/jobs/executor.py`. Side-effect import in `backend/algo/jobs/__init__.py`.
- **Factor library cache** (REGIME-2a): `backend/algo/factors/repo.py::get_factors_window` provides ADTV indirectly via `volume_x_avg_20`; but for slippage we need raw ADTV in INR (avg_vol × avg_close). Compute on-the-fly from `stocks.ohlcv` rolled 60 days.

If any name doesn't resolve, STOP.

---

## File Structure

**Backend — new:**
- `backend/algo/universe/__init__.py`
- `backend/algo/universe/iceberg_init.py` — schema + register helper for `stocks.universe_snapshot`
- `backend/algo/universe/snapshot_job.py` — `rebuild_universe_snapshot(rebalance_date)` orchestrator
- `backend/algo/universe/pit_resolver.py` — `resolve_pit_universe(bar_date) -> list[str]`
- `backend/algo/strategy/templates/__init__.py`
- `backend/algo/strategy/templates/sector_rotation_monthly.json` — reference JSON strategy
- `backend/algo/strategy/templates/loader.py` — load + parse template by name
- `backend/algo/tests/test_universe_iceberg_schema.py`
- `backend/algo/tests/test_universe_snapshot_job.py`
- `backend/algo/tests/test_pit_resolver.py`
- `backend/algo/tests/test_slippage_model.py`
- `backend/algo/tests/test_backtest_start_floor.py`
- `backend/algo/tests/test_strategy_templates.py`

**Backend — modified:**
- `stocks/create_tables.py` — register `stocks.universe_snapshot` (mirror REGIME-1/2a pattern)
- `backend/algo/backtest/sim_broker.py` — slippage adjustment in `execute()`; constructor accepts optional `adtv_lookup: dict[str, Decimal]`
- `backend/algo/backtest/runner.py` — pre-load ADTV per-ticker from OHLCV (60d avg), pass into `SimBroker(...)`. Optionally consult PIT resolver for the strategy's universe filtering.
- `backend/algo/backtest/types.py` — `BacktestRequest.period_start` validator (≥ 2007-01-01)
- `backend/algo/backtest/walkforward.py` — `WalkForwardConfig.period_start` validator (same floor)
- `backend/algo/jobs/__init__.py` — import `universe.snapshot_job` for `@register_job` side-effect
- `backend/jobs/executor.py` — `@register_job("universe_snapshot_monthly")` wrapper

**Frontend:** None.

---

## Task 1 — `stocks.universe_snapshot` Iceberg table

**Files:**
- Create: `backend/algo/universe/__init__.py`, `backend/algo/universe/iceberg_init.py`
- Modify: `stocks/create_tables.py`
- Test: `backend/algo/tests/test_universe_iceberg_schema.py`

Schema: `rebalance_date DATE (req)`, `ticker STRING (req)`, `adtv_inr_60d DOUBLE`, `market_cap_inr DOUBLE`, `sector STRING`, `included_in_top_200 BOOLEAN`. Partition by `year(rebalance_date)`.

- [ ] **Step 1.1: Failing schema test**

```python
"""Verify universe_snapshot schema."""
from __future__ import annotations

from backend.algo.universe.iceberg_init import (
    UNIVERSE_SNAPSHOT_TABLE,
    universe_snapshot_schema,
)


def test_columns() -> None:
    s = universe_snapshot_schema()
    names = {f.name for f in s.fields}
    assert {
        "rebalance_date", "ticker", "adtv_inr_60d",
        "market_cap_inr", "sector", "included_in_top_200",
    } <= names


def test_table_identifier() -> None:
    assert UNIVERSE_SNAPSHOT_TABLE == "stocks.universe_snapshot"
```

- [ ] **Step 1.2: Implement** (mirror `backend/algo/regime/iceberg_init.py`)

```python
from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import (
    BooleanType, DateType, DoubleType,
    NestedField, StringType,
)

UNIVERSE_SNAPSHOT_TABLE = "stocks.universe_snapshot"


def universe_snapshot_schema() -> Schema:
    return Schema(
        NestedField(1, "rebalance_date", DateType(), required=True),
        NestedField(2, "ticker", StringType(), required=True),
        NestedField(3, "adtv_inr_60d", DoubleType(), required=False),
        NestedField(4, "market_cap_inr", DoubleType(), required=False),
        NestedField(5, "sector", StringType(), required=False),
        NestedField(6, "included_in_top_200",
                    BooleanType(), required=True),
    )


def universe_snapshot_partition_spec() -> PartitionSpec:
    return PartitionSpec(
        PartitionField(
            source_id=1, field_id=1100,
            transform=YearTransform(),
            name="rebalance_date_year",
        )
    )


def register_tables() -> None:
    from stocks.create_tables import _create_table, _get_catalog
    catalog = _get_catalog()
    _create_table(
        catalog, UNIVERSE_SNAPSHOT_TABLE,
        universe_snapshot_schema(),
        universe_snapshot_partition_spec(),
    )
```

- [ ] **Step 1.3: Wire into `stocks/create_tables.py`** (after the factors register call):

```python
    # Universe snapshot — REGIME-7
    from backend.algo.universe.iceberg_init import register_tables as \
        _universe_register
    _universe_register()
```

- [ ] **Step 1.4: Run + create + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_universe_iceberg_schema.py -v
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend python stocks/create_tables.py
git add backend/algo/universe/__init__.py backend/algo/universe/iceberg_init.py backend/algo/tests/test_universe_iceberg_schema.py stocks/create_tables.py
git commit -m "feat(algo): stocks.universe_snapshot Iceberg table (REGIME-7)"
```

---

## Task 2 — Snapshot rebuild job + PIT resolver

**Files:**
- Create: `backend/algo/universe/snapshot_job.py`, `backend/algo/universe/pit_resolver.py`
- Test: `backend/algo/tests/test_universe_snapshot_job.py`, `backend/algo/tests/test_pit_resolver.py`

**Snapshot rebuild logic:**
1. Read all active tickers from `stocks.stock_master` (Indian only — `is_active=True` AND `yf_ticker LIKE '%.NS'`).
2. For each ticker, compute 60d ADTV = AVG(close × volume) over last 60 trading days from `stocks.ohlcv` (single bulk DuckDB query).
3. Apply filters: `market_cap_inr ≥ 5_000_000_000` (₹500cr), `adtv_inr_60d ≥ 100_000_000` (₹10cr).
4. Sort by `adtv_inr_60d DESC`, take top 200 → `included_in_top_200 = True`. Remaining filtered tickers stored with `included_in_top_200 = False` (still useful for ratings).
5. NaN-replaceable upsert per CLAUDE.md §5.1: pre-delete `WHERE rebalance_date = ?`, then append.

**PIT resolver:**
- Query `stocks.universe_snapshot WHERE rebalance_date <= bar_date AND included_in_top_200 = True ORDER BY rebalance_date DESC` and pick the most-recent rebalance group.
- Returns `list[str]`. Empty list when no snapshot exists yet.

- [ ] **Step 2.1: PIT resolver test**

```python
"""PIT resolver tests."""
from __future__ import annotations

from datetime import date

from backend.algo.universe.pit_resolver import resolve_pit_universe


def test_resolve_returns_latest_snapshot(monkeypatch) -> None:
    rows = [
        # snapshot for 2026-04-01
        {"rebalance_date": date(2026, 4, 1), "ticker": "A.NS"},
        {"rebalance_date": date(2026, 4, 1), "ticker": "B.NS"},
        # newer snapshot for 2026-05-01 (must beat the older one)
        {"rebalance_date": date(2026, 5, 1), "ticker": "C.NS"},
        {"rebalance_date": date(2026, 5, 1), "ticker": "D.NS"},
    ]
    from backend.algo.universe import pit_resolver as mod
    monkeypatch.setattr(
        mod, "_query_snapshot_rows", lambda d: rows,
    )
    out = resolve_pit_universe(date(2026, 5, 15))
    # Latest rebalance ≤ bar_date is 2026-05-01
    assert sorted(out) == ["C.NS", "D.NS"]


def test_empty_when_no_snapshot(monkeypatch) -> None:
    from backend.algo.universe import pit_resolver as mod
    monkeypatch.setattr(mod, "_query_snapshot_rows", lambda d: [])
    assert resolve_pit_universe(date(2026, 5, 15)) == []


def test_picks_correct_snapshot_at_boundary(monkeypatch) -> None:
    rows = [
        {"rebalance_date": date(2026, 5, 1), "ticker": "X.NS"},
    ]
    from backend.algo.universe import pit_resolver as mod
    monkeypatch.setattr(mod, "_query_snapshot_rows", lambda d: rows)
    # Exactly on rebalance date — included
    assert resolve_pit_universe(date(2026, 5, 1)) == ["X.NS"]
```

- [ ] **Step 2.2: Implement `pit_resolver.py`**

```python
"""Point-in-time universe resolver. Reads stocks.universe_snapshot
and returns the cohort active as-of bar_date."""
from __future__ import annotations

import logging
from datetime import date

from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)


def _query_snapshot_rows(bar_date: date) -> list[dict]:
    return query_iceberg_table(
        "stocks.universe_snapshot",
        "SELECT rebalance_date, ticker FROM universe_snapshot "
        "WHERE rebalance_date <= ? "
        "  AND included_in_top_200 = TRUE",
        [bar_date],
    )


def resolve_pit_universe(bar_date: date) -> list[str]:
    """Tickers in the top-200 snapshot active as-of bar_date.
    Empty list if no snapshot exists yet."""
    rows = _query_snapshot_rows(bar_date)
    if not rows:
        return []
    latest = max(r["rebalance_date"] for r in rows)
    return sorted({
        r["ticker"] for r in rows if r["rebalance_date"] == latest
    })
```

- [ ] **Step 2.3: Snapshot rebuild test**

```python
"""Snapshot rebuild orchestrator unit tests — mock SQL + repo."""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.universe import snapshot_job


def test_rebuild_filters_and_caps_at_200(monkeypatch) -> None:
    # 250 candidates with descending ADTV; expect top 200 included
    candidates = [
        {
            "ticker": f"T{i}.NS",
            "adtv_inr_60d": float(250 - i) * 1e8,
            "market_cap_inr": 1e10,
            "sector": "IT",
        }
        for i in range(250)
    ]
    monkeypatch.setattr(
        snapshot_job, "_load_candidates", lambda d: candidates,
    )
    captured: list = []
    monkeypatch.setattr(
        snapshot_job, "_upsert_snapshot",
        lambda d, rows: captured.extend(rows),
    )
    snapshot_job.rebuild_universe_snapshot(date(2026, 5, 1))
    included = [r for r in captured if r["included_in_top_200"]]
    excluded = [r for r in captured if not r["included_in_top_200"]]
    assert len(included) == 200
    assert len(excluded) == 50


def test_rebuild_filters_low_mcap(monkeypatch) -> None:
    candidates = [
        # 1 valid + 1 too-small mcap (should be dropped entirely)
        {
            "ticker": "OK.NS", "adtv_inr_60d": 5e8,
            "market_cap_inr": 1e10, "sector": "IT",
        },
        {
            "ticker": "SMALL.NS", "adtv_inr_60d": 5e8,
            "market_cap_inr": 1e8,   # 10cr → below 500cr floor
            "sector": "IT",
        },
    ]
    monkeypatch.setattr(
        snapshot_job, "_load_candidates", lambda d: candidates,
    )
    captured: list = []
    monkeypatch.setattr(
        snapshot_job, "_upsert_snapshot",
        lambda d, rows: captured.extend(rows),
    )
    snapshot_job.rebuild_universe_snapshot(date(2026, 5, 1))
    tickers = {r["ticker"] for r in captured}
    assert "SMALL.NS" not in tickers
    assert "OK.NS" in tickers


def test_rebuild_empty_candidates_no_op(monkeypatch) -> None:
    monkeypatch.setattr(
        snapshot_job, "_load_candidates", lambda d: [],
    )
    monkeypatch.setattr(
        snapshot_job, "_upsert_snapshot",
        lambda d, rows: pytest.fail("Should not upsert empty"),
    )
    # No raise; just logs and returns
    snapshot_job.rebuild_universe_snapshot(date(2026, 5, 1))
```

- [ ] **Step 2.4: Implement `snapshot_job.py`**

```python
"""Monthly universe-snapshot rebuilder. Runs 1st Sunday 03:00 IST.

Filters NSE active tickers by market cap (≥ ₹500cr) + 60d ADTV
(≥ ₹10cr); top-200 by ADTV → included_in_top_200=True; remaining
candidates persisted with included_in_top_200=False (useful for
follow-up filters).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pyarrow as pa
from pyiceberg.expressions import EqualTo

from backend.algo.universe.iceberg_init import (
    UNIVERSE_SNAPSHOT_TABLE,
)
from backend.cache import get_cache
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)

_logger = logging.getLogger(__name__)

MARKET_CAP_MIN_INR = 5_000_000_000      # ₹500cr
ADTV_MIN_INR = 100_000_000              # ₹10cr
TOP_N = 200
ADTV_LOOKBACK_DAYS = 90  # calendar; ~60 trading days


def _load_candidates(rebalance_date: date) -> list[dict]:
    """Compute 60d ADTV per .NS active ticker + join market cap."""
    start = rebalance_date - timedelta(days=ADTV_LOOKBACK_DAYS)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT ticker, AVG(close * volume) AS adtv_inr_60d "
        "FROM ohlcv "
        "WHERE date BETWEEN ? AND ? "
        "  AND ticker LIKE '%.NS' "
        "  AND ticker NOT LIKE '^%' "
        "GROUP BY ticker",
        [start, rebalance_date],
    )
    if not rows:
        return []
    # Join sector + market_cap from piotroski (covers most active
    # NSE names; companies missing scores get NaN sector → skipped).
    placeholders = ",".join(["?"] * len(rows))
    sector_rows = query_iceberg_table(
        "stocks.piotroski_scores",
        f"SELECT ticker, MAX(market_cap) AS market_cap, "
        f"       MAX(sector) AS sector "
        f"FROM piotroski_scores "
        f"WHERE ticker IN ({placeholders}) "
        f"GROUP BY ticker",
        [r["ticker"] for r in rows],
    )
    by_t = {r["ticker"]: r for r in sector_rows}
    out: list[dict] = []
    for r in rows:
        meta = by_t.get(r["ticker"], {})
        mc = meta.get("market_cap") or 0
        out.append({
            "ticker": r["ticker"],
            "adtv_inr_60d": float(r["adtv_inr_60d"] or 0),
            "market_cap_inr": float(mc),
            "sector": meta.get("sector"),
        })
    return out


def _upsert_snapshot(
    rebalance_date: date, rows: list[dict],
) -> None:
    if not rows:
        return
    from stocks.create_tables import _get_catalog
    cat = _get_catalog()
    tbl = cat.load_table(UNIVERSE_SNAPSHOT_TABLE)
    try:
        tbl.delete(EqualTo("rebalance_date", rebalance_date))
    except Exception as exc:  # pragma: no cover
        _logger.debug("snapshot pre-delete skipped: %s", exc)

    schema = pa.schema([
        pa.field("rebalance_date", pa.date32(), nullable=False),
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("adtv_inr_60d", pa.float64(), nullable=True),
        pa.field("market_cap_inr", pa.float64(), nullable=True),
        pa.field("sector", pa.string(), nullable=True),
        pa.field("included_in_top_200", pa.bool_(), nullable=False),
    ])
    arrow_tbl = pa.table({
        "rebalance_date": [rebalance_date] * len(rows),
        "ticker": [r["ticker"] for r in rows],
        "adtv_inr_60d": [r["adtv_inr_60d"] for r in rows],
        "market_cap_inr": [r["market_cap_inr"] for r in rows],
        "sector": [r.get("sector") for r in rows],
        "included_in_top_200": [
            bool(r.get("included_in_top_200", False)) for r in rows
        ],
    }, schema=schema)
    tbl.append(arrow_tbl)
    invalidate_metadata(UNIVERSE_SNAPSHOT_TABLE)
    get_cache().invalidate("cache:universe:*")


def rebuild_universe_snapshot(rebalance_date: date) -> dict:
    candidates = _load_candidates(rebalance_date)
    if not candidates:
        _logger.warning(
            "No candidates for rebalance_date=%s — snapshot skipped",
            rebalance_date,
        )
        return {"included": 0, "excluded": 0}
    # Filter by market cap + ADTV
    filtered = [
        c for c in candidates
        if c["market_cap_inr"] >= MARKET_CAP_MIN_INR
        and c["adtv_inr_60d"] >= ADTV_MIN_INR
    ]
    # Sort by ADTV desc, top N → included
    filtered.sort(key=lambda c: c["adtv_inr_60d"], reverse=True)
    included = filtered[:TOP_N]
    excluded = filtered[TOP_N:]
    rows = [
        {**c, "included_in_top_200": True} for c in included
    ] + [
        {**c, "included_in_top_200": False} for c in excluded
    ]
    _upsert_snapshot(rebalance_date, rows)
    _logger.info(
        "universe_snapshot: as_of=%s included=%d excluded=%d",
        rebalance_date, len(included), len(excluded),
    )
    return {
        "included": len(included),
        "excluded": len(excluded),
    }
```

- [ ] **Step 2.5: Wire scheduler entry**

In `backend/algo/jobs/__init__.py`:
```python
# REGIME-7 — monthly universe snapshot (1st Sunday 03:00 IST)
from backend.algo.universe import snapshot_job  # noqa: F401
```

In `backend/jobs/executor.py` after the REGIME-6 wrappers:
```python
@register_job("universe_snapshot_monthly")
def _universe_snapshot_monthly(payload: dict) -> dict:
    from backend.algo.universe.snapshot_job import (
        rebuild_universe_snapshot,
    )
    from datetime import date
    rd = payload.get("rebalance_date")
    parsed = date.fromisoformat(rd) if rd else date.today()
    return rebuild_universe_snapshot(parsed)
```

- [ ] **Step 2.6: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_pit_resolver.py backend/algo/tests/test_universe_snapshot_job.py -v
docker compose restart backend && sleep 6
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend python -c "from jobs.executor import JOB_EXECUTORS; print('universe_snapshot_monthly registered:', 'universe_snapshot_monthly' in JOB_EXECUTORS)"
git add backend/algo/universe/snapshot_job.py backend/algo/universe/pit_resolver.py backend/algo/tests/test_pit_resolver.py backend/algo/tests/test_universe_snapshot_job.py backend/algo/jobs/__init__.py backend/jobs/executor.py
git commit -m "feat(algo): universe snapshot rebuild job + PIT resolver (REGIME-7)"
```

---

## Task 3 — Slippage model upgrade

**Files:**
- Modify: `backend/algo/backtest/sim_broker.py`
- Modify: `backend/algo/backtest/runner.py` (pre-compute ADTV per universe ticker, pass to SimBroker)
- Test: `backend/algo/tests/test_slippage_model.py`

**Slippage formula:**
```
slip_bps = max(5, 50 * order_value_inr / adtv_inr)
```

Applied as a price adjustment AFTER fee computation:
- BUY: `fill_price = next_bar.open * (1 + slip_bps / 10000)` — pay more
- SELL: `fill_price = next_bar.open * (1 - slip_bps / 10000)` — receive less

If `adtv_inr` for the ticker is missing/zero/NaN, default to a high value (so slippage falls back to the 5bps minimum). Don't raise.

`SimBroker.__init__` gains an optional `adtv_lookup: dict[str, Decimal] | None = None`. `runner.py` builds this dict by querying `stocks.ohlcv` for the period and computing `mean(close * volume)` per ticker over the last 60 days before period_start.

- [ ] **Step 3.1: Failing slippage tests**

```python
"""Slippage model tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.algo.backtest.sim_broker import estimate_slippage_bps


def test_min_5bps_when_order_tiny() -> None:
    bps = estimate_slippage_bps(
        order_value_inr=Decimal("1000"),
        ticker_adtv_inr=Decimal("1000000000"),  # 100cr ADTV
    )
    assert bps == Decimal("5")


def test_scales_with_order_value() -> None:
    """Order = 1% of ADTV → 50 * 0.01 = 0.5bps → still floored at 5"""
    bps_low = estimate_slippage_bps(
        order_value_inr=Decimal("1000000"),       # 10L
        ticker_adtv_inr=Decimal("100000000"),     # 10cr
    )
    # order = 100_000 / 100_000_000 = 0.01 = 1%; 50*0.01=0.5
    assert bps_low == Decimal("5")
    # Now make order much bigger
    bps_high = estimate_slippage_bps(
        order_value_inr=Decimal("100000000"),      # 10cr
        ticker_adtv_inr=Decimal("100000000"),      # 10cr
    )
    # 100% of ADTV → 50 * 1 = 50bps
    assert bps_high == Decimal("50")


def test_zero_adtv_returns_minimum() -> None:
    bps = estimate_slippage_bps(
        order_value_inr=Decimal("100000"),
        ticker_adtv_inr=Decimal("0"),
    )
    # Formula would div/zero — caller defaults to min 5bps
    assert bps == Decimal("5")


def test_nan_adtv_returns_minimum() -> None:
    bps = estimate_slippage_bps(
        order_value_inr=Decimal("100000"),
        ticker_adtv_inr=Decimal("NaN"),
    )
    assert bps == Decimal("5")
```

- [ ] **Step 3.2: Implement slippage helper + apply in `execute()`**

Add at top of `backend/algo/backtest/sim_broker.py`:

```python
SLIPPAGE_MIN_BPS = Decimal("5")
SLIPPAGE_IMPACT_BPS = Decimal("50")


def estimate_slippage_bps(
    order_value_inr: Decimal,
    ticker_adtv_inr: Decimal,
) -> Decimal:
    """Per research §10: ``max(5, 50 × order_value / ADTV) bps``."""
    if (
        ticker_adtv_inr.is_nan()
        or ticker_adtv_inr <= 0
    ):
        return SLIPPAGE_MIN_BPS
    impact = SLIPPAGE_IMPACT_BPS * (order_value_inr / ticker_adtv_inr)
    return max(SLIPPAGE_MIN_BPS, impact)
```

Modify `SimBroker.__init__` signature:
```python
def __init__(
    self,
    *,
    bars: dict[str, list[BarData]],
    fee_as_of: date,
    adtv_lookup: dict[str, Decimal] | None = None,
) -> None:
    self._bars = bars
    self._fees = IndianFeeModel(as_of=fee_as_of)
    self._adtv = adtv_lookup or {}
    # ... existing index-build code
```

Modify `execute()` to adjust `fill_price` BEFORE returning the Fill:

```python
        next_bar = self._bars[intent.ticker][next_idx]
        # Slippage adjustment (REGIME-7) — applied to open
        adtv = self._adtv.get(
            intent.ticker, Decimal("NaN"),
        )
        order_value = Decimal(intent.qty) * next_bar.open
        bps = estimate_slippage_bps(order_value, adtv)
        slip_factor = bps / Decimal("10000")
        if intent.side == "BUY":
            fill_price = next_bar.open * (Decimal("1") + slip_factor)
        else:
            fill_price = next_bar.open * (Decimal("1") - slip_factor)

        # Compute fees on the executed leg (uses fill_price, not bar open)
        product = "DELIVERY"
        exchange = "NSE"
        breakdown = self._fees.compute(
            Trade(
                symbol=intent.ticker,
                exchange=exchange,
                side=intent.side,
                product=product,
                qty=intent.qty,
                price=fill_price,
            ),
        )
        return Fill(
            intent_id=intent.intent_id,
            ticker=intent.ticker,
            side=intent.side,
            qty=intent.qty,
            fill_price=fill_price,
            fill_date=next_bar.date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
        )
```

Existing backtest tests should still pass because they didn't pass `adtv_lookup`, so all slippage is the 5bps minimum (≈0.05% on each leg → may shift expected PnL by tiny fraction). If specific tests assert exact PnL values, they'll need updating; document deviations in the report.

- [ ] **Step 3.3: Wire in `runner.py`**

Add at top:
```python
from backend.db.duckdb_engine import query_iceberg_table
```

In `run_backtest`, after `bars = load_ohlcv_window(...)` (around the existing code that loads bars):

```python
    # Pre-compute 60d ADTV per ticker for slippage model (REGIME-7)
    from datetime import timedelta as _td
    adtv_start = request.period_start - _td(days=90)
    adtv_rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT ticker, AVG(close * volume) AS adtv FROM ohlcv "
        "WHERE ticker IN ({}) AND date BETWEEN ? AND ? "
        "GROUP BY ticker".format(",".join(["?"] * len(universe))),
        [*universe, adtv_start, request.period_start],
    )
    adtv_lookup: dict[str, Decimal] = {
        r["ticker"]: Decimal(str(r["adtv"] or 0))
        for r in adtv_rows
    }
    sim = SimBroker(
        bars=bars,
        fee_as_of=request.period_start,
        adtv_lookup=adtv_lookup,
    )
```

- [ ] **Step 3.4: Run + regression check + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_slippage_model.py -v
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_backtest_runner.py backend/algo/tests/test_backtest_equity_curve.py -q --no-header 2>&1 | tail -10
git add backend/algo/backtest/sim_broker.py backend/algo/backtest/runner.py backend/algo/tests/test_slippage_model.py
git commit -m "feat(algo): ADTV-scaled slippage model in backtest sim_broker (REGIME-7)"
```

---

## Task 4 — AST 2007-01-01 backtest start floor

**Files:**
- Modify: `backend/algo/backtest/types.py` (`BacktestRequest`)
- Modify: `backend/algo/backtest/walkforward.py` (`WalkForwardConfig`)
- Test: `backend/algo/tests/test_backtest_start_floor.py`

Pydantic v2 `field_validator`:

```python
from pydantic import field_validator

@field_validator("period_start")
@classmethod
def _start_floor(cls, v: date) -> date:
    if v < date(2007, 1, 1):
        raise ValueError(
            "Backtest start floor is 2007-01-01 (mandatory to "
            "include 2008 bear market for survivorship + regime "
            "validation)."
        )
    return v
```

Apply same validator to both Pydantic models. Add `from pydantic import field_validator` if missing.

- [ ] **Step 4.1: Test**

```python
"""2007-01-01 backtest start floor (REGIME-7)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.algo.backtest.types import BacktestRequest
from backend.algo.backtest.walkforward import WalkForwardConfig


def test_2007_accepted_in_backtest_request() -> None:
    req = BacktestRequest(
        strategy_id=uuid4(),
        period_start=date(2007, 1, 1),
        period_end=date(2008, 1, 1),
    )
    assert req.period_start == date(2007, 1, 1)


def test_2006_rejected_in_backtest_request() -> None:
    with pytest.raises(ValidationError, match="2007-01-01"):
        BacktestRequest(
            strategy_id=uuid4(),
            period_start=date(2006, 12, 31),
            period_end=date(2008, 1, 1),
        )


def test_1970_rejected_in_backtest_request() -> None:
    with pytest.raises(ValidationError, match="2007-01-01"):
        BacktestRequest(
            strategy_id=uuid4(),
            period_start=date(1970, 1, 1),
            period_end=date(2008, 1, 1),
        )


def test_2007_accepted_in_walkforward_config() -> None:
    cfg = WalkForwardConfig(
        strategy_id=uuid4(),
        period_start=date(2007, 1, 1),
        period_end=date(2010, 1, 1),
        train_days=180,
        test_days=30,
        step_days=30,
    )
    assert cfg.period_start == date(2007, 1, 1)


def test_2006_rejected_in_walkforward_config() -> None:
    with pytest.raises(ValidationError, match="2007-01-01"):
        WalkForwardConfig(
            strategy_id=uuid4(),
            period_start=date(2006, 12, 31),
            period_end=date(2010, 1, 1),
            train_days=180,
            test_days=30,
            step_days=30,
        )
```

- [ ] **Step 4.2: Implement in both Pydantic models** + commit

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_backtest_start_floor.py -v
git add backend/algo/backtest/types.py backend/algo/backtest/walkforward.py backend/algo/tests/test_backtest_start_floor.py
git commit -m "feat(algo): AST 2007-01-01 backtest start floor (REGIME-7)"
```

---

## Task 5 — Sector rotation strategy template

**Files:**
- Create: `backend/algo/strategy/templates/__init__.py`
- Create: `backend/algo/strategy/templates/sector_rotation_monthly.json` — JSON strategy
- Create: `backend/algo/strategy/templates/loader.py` — `load_template(name) -> Strategy`
- Test: `backend/algo/tests/test_strategy_templates.py`

The JSON is a reference (gallery item) — it doesn't need to be a runnable end-to-end strategy in v3 (regime-aware factor-based selection is partially expressed in the AST today; full sector rotation needs more grammar). Ship a SIMPLIFIED version using the existing AST grammar:
- Universe: top 200 (PIT)
- Entry: regime_label == "BULL" AND mom_12_1 > 0.10 AND f_score >= 7
- Exit: mom_12_1 < 0
- Sizing: vol_target_pct = 1.5
- Applicable regimes: ["bull", "sideways"]

This exercises EVERY new v3 feature (REGIME-1 regime, REGIME-2a factors, REGIME-3 binding, REGIME-4 vol-target, REGIME-7 PIT). It's a working template, not just docs.

- [ ] **Step 5.1: Template JSON**

Create `backend/algo/strategy/templates/sector_rotation_monthly.json`:

```json
{
  "id": "00000000-0000-0000-0000-000000000007",
  "name": "Regime-aware momentum + quality (template)",
  "universe": {"scope": "watchlist"},
  "schedule": {"on": "bar_close", "timeframe": "1d"},
  "rebalance": {"every": "1d"},
  "root": {
    "type": "and",
    "operands": [
      {
        "type": "compare",
        "left": {"feature": "regime_label"},
        "op": "==",
        "right": {"literal": "BULL"}
      },
      {
        "type": "compare",
        "left": {"feature": "mom_12_1"},
        "op": ">",
        "right": {"literal": 0.10}
      },
      {
        "type": "compare",
        "left": {"feature": "f_score"},
        "op": ">=",
        "right": {"literal": 7}
      }
    ]
  },
  "risk": {
    "per_trade": {"stop_loss_pct": 8.0},
    "portfolio": {"max_open_positions": 10},
    "daily": {"max_loss_pct": 5.0}
  }
}
```

(The AST evaluator returns a "buy on true / hold on false" semantic per the existing `_root_must_be_actionable` validator — see ast.py:281.)

- [ ] **Step 5.2: Loader**

```python
"""Strategy template loader."""
from __future__ import annotations

import json
from pathlib import Path

from backend.algo.strategy.ast import Strategy, parse_strategy

_TEMPLATE_DIR = Path(__file__).parent


def list_templates() -> list[str]:
    return sorted(p.stem for p in _TEMPLATE_DIR.glob("*.json"))


def load_template(name: str) -> Strategy:
    """Load a JSON template by stem name. Raises FileNotFoundError
    if not found, ValidationError if AST is malformed."""
    path = _TEMPLATE_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Template not found: {name}")
    with path.open() as fh:
        payload = json.load(fh)
    return parse_strategy(payload)
```

- [ ] **Step 5.3: Test**

```python
"""Template loader + sector_rotation_monthly parsing."""
from __future__ import annotations

import pytest

from backend.algo.strategy.templates.loader import (
    list_templates,
    load_template,
)


def test_list_includes_sector_rotation() -> None:
    assert "sector_rotation_monthly" in list_templates()


def test_sector_rotation_parses() -> None:
    s = load_template("sector_rotation_monthly")
    assert s.name.startswith("Regime-aware")


def test_unknown_template_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_template("does_not_exist")
```

- [ ] **Step 5.4: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_strategy_templates.py -v
git add backend/algo/strategy/templates/
git add backend/algo/tests/test_strategy_templates.py
git commit -m "feat(algo): sector rotation strategy template (REGIME-7)"
```

---

## Task 6 — Push + close v3 epic

```bash
git push origin feature/regime-slice-7-rotation-pit-slippage
```

---

## Acceptance Checklist

- [ ] `stocks.universe_snapshot` Iceberg table created
- [ ] Snapshot rebuild filters by mcap (≥ ₹500cr) + ADTV (≥ ₹10cr); top-200 by ADTV included
- [ ] PIT resolver returns latest snapshot ≤ bar_date
- [ ] `universe_snapshot_monthly` job registered
- [ ] Slippage formula `max(5, 50 × order_value / ADTV) bps` applied to BUY + SELL
- [ ] Existing backtest tests still pass (slippage with empty adtv_lookup defaults to 5bps minimum — minimal PnL drift)
- [ ] `BacktestRequest.period_start ≥ 2007-01-01` enforced via Pydantic validator
- [ ] `WalkForwardConfig.period_start ≥ 2007-01-01` same
- [ ] Sector rotation template JSON parses + loads via `load_template()`
- [ ] Branch pushed

---

## Out of Scope for REGIME-7

- **Listing-age filter** — `stock_master.listing_date` doesn't exist; would require new column + backfill. v3.1.
- **Real NIFTY 500 constituent list** — using `.NS` active stock_master + ADTV+mcap filter as proxy.
- **PIT enforcement in backtest runner** — universe is currently passed in by the caller; the PIT resolver is available but plumbing it into every `run_backtest` call requires a broader contract change. Documented as v3.1 wiring.
- **Per-sector regime overlay** — v4.
- **Sector rotation as user-editable template** — the JSON ships as a code asset; a UI gallery for templates is v4.
- **Backtest fail-fast on missing PIT universe** — same reason as PIT enforcement; deferred.
- **ADTV backfill historical** — the snapshot job runs going forward; backfilling 24mo of snapshots is a one-shot ops script (v3.1).
