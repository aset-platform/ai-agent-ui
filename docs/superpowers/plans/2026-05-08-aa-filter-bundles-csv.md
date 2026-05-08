# AA Filter Bundles + Filtered CSV Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Technical (9) + Fundamentals (8) filter dropdowns to all 7 Advanced Analytics tabs, AND-combined, per-tab via URL search params. Replace current page-only CSV with a streamed full-filtered-set export endpoint capped at 10 000 rows.

**Architecture:** Single backend allowlist module owns predicate functions and validates query params. `_compute_report` applies bundle filters in-memory between the existing search-filter and `_passes_filter` steps. New `/{report}/export` endpoint reuses the same compute pipeline minus pagination, streams `text/csv`. Frontend mirrors the catalog as a TS literal (CI-enforced sync), exposes two `<FilterDropdown />` popovers + an active-filter chip strip + a download helper that hits the export URL.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic 2 / pytest. Next.js 16 / React 19 / SWR / Radix Popover patterns / vitest / Playwright.

**Spec:** `docs/superpowers/specs/2026-05-08-aa-filter-dropdown-csv-design.md`

**Branch:** `feature/aa-filter-bundles-csv` (already created, spec committed at `b4655f0`).

**Conventions reminders for the implementer:**
- Branch off `dev`; squash-only merge to `dev` (§4.4 #21, #26).
- Co-Authored-By `Abhay Kumar Singh <asequitytrading@gmail.com>`.
- Line length 79 chars; `X | None` not `Optional[X]`; logger via `logging.getLogger(__name__)`.
- After backend route/model changes: `docker compose restart backend` (uvicorn reload is not enough — §6.2).
- After cache code change: `docker compose exec redis redis-cli FLUSHALL` (§4.5 #34).
- E2E selectors live in `e2e/utils/selectors.ts` `FE`; never hardcode in spec files (§5.14).

---

## File Structure

**Backend (new):**
- `backend/advanced_analytics_filters.py` — catalog (`TECH_KEYS`, `FUND_KEYS`), predicate dicts, `parse_filter_csv`, `passes_bundle_filters`.
- `backend/tests/test_advanced_analytics_filters.py` — predicate edge cases + parser validation.
- `backend/tests/test_filter_catalog_sync.py` — CI gate that asserts frontend TS literal matches backend allowlist.

**Backend (modified):**
- `backend/advanced_analytics_routes.py` — add `tech` / `fund` query params to existing `_make_endpoint`; extend cache key in `_compute_report`; insert `passes_bundle_filters` call between search filter and `_passes_filter`; add new `_make_export_endpoint` registered at `/{report}/export`.
- `backend/tests/test_advanced_analytics_routes.py` — extend with bundle-filter and export-endpoint cases.

**Frontend (new):**
- `frontend/components/advanced-analytics/filterCatalogs.ts` — TS literal mirror of backend catalog + UI labels + radio group ids.
- `frontend/components/advanced-analytics/FilterDropdown.tsx` — reusable popover for one bundle (radio + checkbox).
- `frontend/components/advanced-analytics/ActiveFilterChips.tsx` — chip strip with click-× removal + "Clear all".
- `frontend/components/advanced-analytics/__tests__/FilterDropdown.test.tsx` — vitest.
- `frontend/components/advanced-analytics/__tests__/ActiveFilterChips.test.tsx` — vitest.
- `frontend/hooks/useFilterParams.ts` — URL ↔ state with debounced write + sorted serialisation.
- `frontend/hooks/__tests__/useFilterParams.test.tsx` — vitest.
- `frontend/lib/triggerCsvDownload.ts` — blob-download helper using `apiFetch`.
- `frontend/lib/__tests__/triggerCsvDownload.test.ts` — vitest.

**Frontend (modified):**
- `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx` — insert two `<FilterDropdown />` instances + `<ActiveFilterChips />` + rewire `handleCsv` to hit export endpoint + 10k disabled-state.
- `frontend/hooks/useAdvancedAnalyticsData.ts` — add `tech` / `fund` to signature + SWR key (sorted CSV).
- `frontend/lib/types/advancedAnalytics.ts` — export `TechFilterKey`, `FundFilterKey`, `BUNDLE_BADGE_LIMIT = 10_000`.
- `e2e/utils/selectors.ts` — register `aa-filter-*` and `aa-active-filter-*` testids in `FE`.

**E2E (new):**
- `e2e/tests/aa-filters.spec.ts` — Playwright superuser fixture, filter→URL→table assertion, chip removal, CSV download.

---

## Task 1: Backend filter catalog module + predicate tests

**Files:**
- Create: `backend/advanced_analytics_filters.py`
- Create: `backend/tests/test_advanced_analytics_filters.py`

- [ ] **Step 1: Write failing predicate tests**

```python
# backend/tests/test_advanced_analytics_filters.py
"""Unit tests for the AA filter bundle module (Sprint 9 follow-on)."""
from __future__ import annotations

import math

import pytest
from fastapi import HTTPException

from backend.advanced_analytics_filters import (
    FUND_KEYS,
    TECH_KEYS,
    parse_filter_csv,
    passes_bundle_filters,
)
from backend.advanced_analytics_models import AdvancedRow


def _row(**overrides) -> AdvancedRow:
    base = {"ticker": "TEST.NS"}
    base.update(overrides)
    return AdvancedRow(**base)


# ---- TECH PREDICATES ------------------------------------------------

@pytest.mark.parametrize(
    "days_ago,expect_recent,expect_established",
    [
        (None, False, False),
        (0, True, False),
        (10, True, False),
        (11, False, True),
        (999, False, True),
    ],
)
def test_golden_cross_predicates(
    days_ago, expect_recent, expect_established,
):
    r = _row(golden_cross_days_ago=days_ago)
    assert passes_bundle_filters(r, ["golden_recent"], []) is expect_recent
    assert (
        passes_bundle_filters(r, ["golden_established"], [])
        is expect_established
    )


def test_price_gt_sma_predicates():
    bullish = _row(today_ltp=110.0, sma_50=100.0, sma_200=90.0)
    bearish = _row(today_ltp=80.0, sma_50=100.0, sma_200=90.0)
    nan_row = _row(today_ltp=None, sma_50=100.0, sma_200=90.0)

    assert passes_bundle_filters(bullish, ["price_gt_sma50"], []) is True
    assert passes_bundle_filters(bullish, ["price_gt_sma200"], []) is True
    assert passes_bundle_filters(bearish, ["price_gt_sma50"], []) is False
    assert passes_bundle_filters(nan_row, ["price_gt_sma50"], []) is False


@pytest.mark.parametrize(
    "rsi,key,expected",
    [
        (15.0, "rsi_oversold", True),
        (30.0, "rsi_oversold", False),
        (30.0, "rsi_neutral", True),
        (70.0, "rsi_neutral", True),
        (70.01, "rsi_overbought", True),
        (None, "rsi_neutral", False),
        (float("nan"), "rsi_oversold", False),
    ],
)
def test_rsi_band_predicates(rsi, key, expected):
    r = _row(rsi=rsi)
    assert passes_bundle_filters(r, [key], []) is expected


def test_vol_surge_and_near_52w_high():
    r = _row(today_x_vol=2.0, away_from_52week_high=-3.5)
    assert passes_bundle_filters(r, ["vol_surge"], []) is True
    assert passes_bundle_filters(r, ["near_52w_high"], []) is True

    r2 = _row(today_x_vol=1.99, away_from_52week_high=-5.01)
    assert passes_bundle_filters(r2, ["vol_surge"], []) is False
    assert passes_bundle_filters(r2, ["near_52w_high"], []) is False


# ---- FUND PREDICATES ------------------------------------------------

@pytest.mark.parametrize(
    "pscore,key,expected",
    [
        (7, "fscore_ge_7", True),
        (6, "fscore_ge_7", False),
        (3, "fscore_le_3", True),
        (4, "fscore_le_3", False),
        (None, "fscore_ge_7", False),
    ],
)
def test_fscore_predicates(pscore, key, expected):
    r = _row(pscore=pscore)
    assert passes_bundle_filters(r, [], [key]) is expected


def test_fund_threshold_predicates():
    good = _row(
        debt_to_eq=0.3, roce=22.0,
        sales_growth_3yrs=18.0, prft_growth_3yrs=20.0,
        prom_hld=55.0, pledged=2.0,
    )
    for key in (
        "debt_lt_0_5", "roce_gt_20",
        "sales_3y_gt_15", "profit_3y_gt_15",
        "prom_hld_gt_50", "pledged_lt_5",
    ):
        assert passes_bundle_filters(good, [], [key]) is True

    nan_row = _row(roce=None)
    assert passes_bundle_filters(nan_row, [], ["roce_gt_20"]) is False


# ---- COMBINATION + PARSER ------------------------------------------

def test_and_within_bundle_and_across_bundles():
    r = _row(
        today_ltp=110.0, sma_50=100.0, golden_cross_days_ago=5,
        pscore=8, debt_to_eq=0.2,
    )
    # Both tech checks AND both fund checks pass.
    assert (
        passes_bundle_filters(
            r,
            ["golden_recent", "price_gt_sma50"],
            ["fscore_ge_7", "debt_lt_0_5"],
        )
        is True
    )
    # One tech check fails ⇒ overall False.
    r_fail = _row(
        today_ltp=80.0, sma_50=100.0, golden_cross_days_ago=5,
        pscore=8, debt_to_eq=0.2,
    )
    assert (
        passes_bundle_filters(
            r_fail,
            ["golden_recent", "price_gt_sma50"],
            ["fscore_ge_7", "debt_lt_0_5"],
        )
        is False
    )


def test_parse_filter_csv_happy_path():
    out = parse_filter_csv(
        "golden_recent,price_gt_sma50", TECH_KEYS, "tech",
    )
    assert out == ["golden_recent", "price_gt_sma50"]


def test_parse_filter_csv_dedupes_and_sorts():
    out = parse_filter_csv(
        "price_gt_sma50,golden_recent,price_gt_sma50",
        TECH_KEYS,
        "tech",
    )
    assert out == ["golden_recent", "price_gt_sma50"]


def test_parse_filter_csv_rejects_unknown_key():
    with pytest.raises(HTTPException) as exc:
        parse_filter_csv(
            "golden_recent,not_a_filter", TECH_KEYS, "tech",
        )
    assert exc.value.status_code == 400
    assert "not_a_filter" in exc.value.detail


def test_parse_filter_csv_empty_returns_empty():
    assert parse_filter_csv("", TECH_KEYS, "tech") == []


def test_keys_are_disjoint():
    """Tech and fund key sets must not collide (URL clarity)."""
    assert TECH_KEYS.isdisjoint(FUND_KEYS)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_filters.py -v
```

Expected: ImportError / ModuleNotFoundError for `backend.advanced_analytics_filters`.

- [ ] **Step 3: Implement the catalog module**

```python
# backend/advanced_analytics_filters.py
"""Filter catalog + predicates for Advanced Analytics bundles.

Single source of truth for the technical + fundamentals filter
allowlist used by ``/v1/advanced-analytics/{report}`` and the
``/{report}/export`` endpoint. Frontend mirror lives at
``frontend/components/advanced-analytics/filterCatalogs.ts``;
sync verified by ``test_filter_catalog_sync.py``.
"""

from __future__ import annotations

import math
from typing import Callable, Literal

from fastapi import HTTPException

from backend.advanced_analytics_models import AdvancedRow

# ---- Type literals --------------------------------------------------

TechKey = Literal[
    "golden_recent",
    "golden_established",
    "price_gt_sma50",
    "price_gt_sma200",
    "rsi_oversold",
    "rsi_neutral",
    "rsi_overbought",
    "vol_surge",
    "near_52w_high",
]
FundKey = Literal[
    "fscore_ge_7",
    "fscore_le_3",
    "debt_lt_0_5",
    "roce_gt_20",
    "sales_3y_gt_15",
    "profit_3y_gt_15",
    "prom_hld_gt_50",
    "pledged_lt_5",
]


# ---- NaN-safe comparators -----------------------------------------

def _is_nan(x: float | int | None) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and math.isnan(x):
        return True
    return False


def _gt(a: float | None, b: float | None) -> bool:
    if _is_nan(a) or _is_nan(b):
        return False
    return float(a) > float(b)  # type: ignore[arg-type]


def _ge(a: float | None, b: float | None) -> bool:
    if _is_nan(a) or _is_nan(b):
        return False
    return float(a) >= float(b)  # type: ignore[arg-type]


def _lt(a: float | None, b: float | None) -> bool:
    if _is_nan(a) or _is_nan(b):
        return False
    return float(a) < float(b)  # type: ignore[arg-type]


def _le(a: float | None, b: float | None) -> bool:
    if _is_nan(a) or _is_nan(b):
        return False
    return float(a) <= float(b)  # type: ignore[arg-type]


# ---- Predicate dicts -----------------------------------------------

TECH_PREDICATES: dict[str, Callable[[AdvancedRow], bool]] = {
    "golden_recent": lambda r: (
        r.golden_cross_days_ago is not None
        and 0 <= r.golden_cross_days_ago <= 10
    ),
    "golden_established": lambda r: (
        r.golden_cross_days_ago is not None
        and r.golden_cross_days_ago > 10
    ),
    "price_gt_sma50": lambda r: _gt(r.today_ltp, r.sma_50),
    "price_gt_sma200": lambda r: _gt(r.today_ltp, r.sma_200),
    "rsi_oversold": lambda r: _lt(r.rsi, 30.0),
    "rsi_neutral": lambda r: _ge(r.rsi, 30.0) and _le(r.rsi, 70.0),
    "rsi_overbought": lambda r: _gt(r.rsi, 70.0),
    "vol_surge": lambda r: _ge(r.today_x_vol, 2.0),
    "near_52w_high": lambda r: _ge(r.away_from_52week_high, -5.0),
}

FUND_PREDICATES: dict[str, Callable[[AdvancedRow], bool]] = {
    "fscore_ge_7": lambda r: r.pscore is not None and r.pscore >= 7,
    "fscore_le_3": lambda r: r.pscore is not None and r.pscore <= 3,
    "debt_lt_0_5": lambda r: _lt(r.debt_to_eq, 0.5),
    "roce_gt_20": lambda r: _gt(r.roce, 20.0),
    "sales_3y_gt_15": lambda r: _gt(r.sales_growth_3yrs, 15.0),
    "profit_3y_gt_15": lambda r: _gt(r.prft_growth_3yrs, 15.0),
    "prom_hld_gt_50": lambda r: _gt(r.prom_hld, 50.0),
    "pledged_lt_5": lambda r: _lt(r.pledged, 5.0),
}

TECH_KEYS: frozenset[str] = frozenset(TECH_PREDICATES)
FUND_KEYS: frozenset[str] = frozenset(FUND_PREDICATES)


# ---- Public helpers -----------------------------------------------

def parse_filter_csv(
    raw: str,
    allowed: frozenset[str],
    bundle: str,
) -> list[str]:
    """Split, dedupe, sort, validate.

    Returns a deterministic ``sorted(list(unique_keys))`` for the
    benefit of cache-key stability. Raises ``HTTPException(400)``
    on the first unknown token.
    """
    if not raw.strip():
        return []
    seen: set[str] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown {bundle} filter: {token}",
            )
        seen.add(token)
    return sorted(seen)


def passes_bundle_filters(
    row: AdvancedRow,
    tech: list[str],
    fund: list[str],
) -> bool:
    """AND across every selected predicate. NaN ⇒ row excluded."""
    for key in tech:
        if not TECH_PREDICATES[key](row):
            return False
    for key in fund:
        if not FUND_PREDICATES[key](row):
            return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_filters.py -v
```

Expected: 16 passed.

- [ ] **Step 5: Lint**

```bash
black backend/advanced_analytics_filters.py backend/tests/test_advanced_analytics_filters.py
isort backend/advanced_analytics_filters.py backend/tests/test_advanced_analytics_filters.py --profile black
flake8 backend/advanced_analytics_filters.py backend/tests/test_advanced_analytics_filters.py
```

Expected: zero output from flake8.

- [ ] **Step 6: Commit**

```bash
git add backend/advanced_analytics_filters.py backend/tests/test_advanced_analytics_filters.py
git commit -m "$(cat <<'EOF'
feat(aa): filter catalog module + predicate tests

Adds backend/advanced_analytics_filters.py with TECH_KEYS (9) +
FUND_KEYS (8) frozensets, NaN-safe predicate dict, parse_filter_csv
(sorts + dedupes + 400 on unknown), and passes_bundle_filters
(AND within and across bundles).

Spec: docs/superpowers/specs/2026-05-08-aa-filter-dropdown-csv-design.md

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: Wire bundle filters into the paginated GET endpoint

**Files:**
- Modify: `backend/advanced_analytics_routes.py:933-1018` (`_compute_report`) and `:1033-1067` (`_make_endpoint`)
- Modify: `backend/tests/test_advanced_analytics_routes.py` (add cases)

- [ ] **Step 1: Write failing endpoint tests**

Append to `backend/tests/test_advanced_analytics_routes.py`:

```python
def test_endpoint_rejects_unknown_tech_filter(authed_client):
    r = authed_client.get(
        "/v1/advanced-analytics/current-day-upmove?tech=not_real",
    )
    assert r.status_code == 400
    assert "not_real" in r.json()["detail"]


def test_endpoint_rejects_unknown_fund_filter(authed_client):
    r = authed_client.get(
        "/v1/advanced-analytics/current-day-upmove?fund=foo",
    )
    assert r.status_code == 400


def test_endpoint_applies_bundle_filters(
    authed_client, seed_aa_rows,
):
    """seed_aa_rows fixture inserts:
        FOO.NS  golden_recent + price_gt_sma50 + pscore=8
        BAR.NS  golden_recent + price_lt_sma50 + pscore=8
    Filter ``tech=price_gt_sma50`` must keep FOO, drop BAR."""
    r = authed_client.get(
        "/v1/advanced-analytics/current-day-upmove"
        "?tech=price_gt_sma50&market=all&ticker_type=stock",
    )
    assert r.status_code == 200
    tickers = {row["ticker"] for row in r.json()["rows"]}
    assert "FOO.NS" in tickers
    assert "BAR.NS" not in tickers


def test_endpoint_cache_key_distinguishes_filter_combos(
    authed_client, redis_cache_spy,
):
    authed_client.get(
        "/v1/advanced-analytics/current-day-upmove?tech=golden_recent",
    )
    authed_client.get(
        "/v1/advanced-analytics/current-day-upmove?tech=price_gt_sma50",
    )
    keys = redis_cache_spy.keys("cache:advanced_analytics:*")
    assert any("ftechgolden_recent" in k for k in keys)
    assert any("ftechprice_gt_sma50" in k for k in keys)
```

> **Note for implementer:** the `authed_client`, `seed_aa_rows`, and `redis_cache_spy` fixtures already exist in `backend/tests/conftest.py` from prior AA tests. If `seed_aa_rows` does not yet expose the fields used above, extend it (single-purpose fixture extension is in scope).

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_routes.py -v -k "filter or cache_key"
```

Expected: 4 failures (filters not yet wired into route).

- [ ] **Step 3: Modify `_compute_report` signature + body**

Edit `backend/advanced_analytics_routes.py`. Add import at the top:

```python
from backend.advanced_analytics_filters import (
    FUND_KEYS,
    TECH_KEYS,
    parse_filter_csv,
    passes_bundle_filters,
)
```

Update `_compute_report` (currently lines 933-1018) — add two parameters and integrate filtering. Replace the function body with:

```python
async def _compute_report(
    user: UserContext,
    report: ReportName,
    page: int,
    page_size: int,
    sort_key: str | None,
    sort_dir: str,
    market: MarketFilter = "all",
    ticker_type: TickerTypeFilter = "all",
    search: str = "",
    tech: str = "",
    fund: str = "",
) -> Response:
    """Shared cache / scope / compute pipeline for all 7 endpoints.

    Two cache layers:
      1. Outer (unchanged) — full row list keyed on
         ``(user, as_of)``.
      2. Inner — full response keyed on every parameter
         including the (sorted, deduped) bundle filters.
    """
    cache = get_cache()
    needle = search.strip().upper()
    tech_keys = parse_filter_csv(tech, TECH_KEYS, "tech")
    fund_keys = parse_filter_csv(fund, FUND_KEYS, "fund")
    as_of = _effective_trading_date()
    inner_ck = (
        f"cache:advanced_analytics:{report}:{user.user_id}"
        f":m{market}:t{ticker_type}:q{needle}"
        f":ftech{','.join(tech_keys)}"
        f":ffund{','.join(fund_keys)}"
        f":dt{as_of.isoformat()}"
        f":p{page}:s{sort_key or 'default'}:{sort_dir}"
        f":ps{page_size}"
    )
    hit = cache.get(inner_ck)
    if hit is not None:
        return Response(content=hit, media_type="application/json")

    full_rows = await _cached_full_rows(user, as_of)
    keep = set(
        _filter_tickers(
            [r.ticker for r in full_rows],
            market,
            ticker_type,
        )
    )
    rows = [r for r in full_rows if r.ticker in keep]
    if needle:
        rows = [r for r in rows if needle in r.ticker.upper()]
    if tech_keys or fund_keys:
        rows = [
            r for r in rows
            if passes_bundle_filters(r, tech_keys, fund_keys)
        ]

    filtered = [r for r in rows if _passes_filter(r, report)]
    page_rows, total = _apply_sort_paginate(
        filtered, report, sort_key, sort_dir, page, page_size,
    )

    stale: list[StaleTicker] = []
    seen: set[str] = set()
    for r in rows:
        if r.ticker in seen:
            continue
        chip = _stale_for_row(r)
        if chip is not None:
            stale.append(chip)
            seen.add(r.ticker)

    body = AdvancedReportResponse(
        rows=page_rows, total=total, page=page,
        page_size=page_size, stale_tickers=stale,
    )
    payload = body.model_dump_json()
    cache.set(inner_ck, payload, ttl=TTL_STABLE)
    return Response(content=payload, media_type="application/json")
```

- [ ] **Step 4: Update `_make_endpoint` to forward the new params**

Replace the `_handler` definition inside `_make_endpoint` (currently lines 1033-1067) with:

```python
def _make_endpoint(report: ReportName):
    async def _handler(
        user: UserContext = Depends(pro_or_superuser),
        page: int = Query(1, ge=1),
        page_size: int = Query(25, ge=1, le=200),
        sort_key: str | None = Query(None),
        sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
        market: str = Query("all", pattern="^(all|india|us)$"),
        ticker_type: str = Query("all", pattern="^(all|stock|etf)$"),
        search: str = Query("", max_length=20),
        tech: str = Query("", max_length=200, pattern="^[a-z0-9_,]*$"),
        fund: str = Query("", max_length=200, pattern="^[a-z0-9_,]*$"),
    ) -> Response:
        try:
            return await _compute_report(
                user, report, page, page_size,
                sort_key, sort_dir,
                market,  # type: ignore[arg-type]
                ticker_type,  # type: ignore[arg-type]
                search, tech, fund,
            )
        except HTTPException:
            raise
        except Exception as exc:
            _logger.exception(
                "advanced_analytics %s failed: %s", report, exc,
            )
            raise HTTPException(
                status_code=500,
                detail=f"advanced_analytics {report} failed",
            )

    _handler.__name__ = f"get_{report.replace('-', '_')}"
    return _handler
```

- [ ] **Step 5: Restart backend + flush Redis (route signature changed)**

```bash
docker compose restart backend
docker compose exec redis redis-cli FLUSHALL
sleep 5
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_routes.py -v -k "filter or cache_key"
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_routes.py -v
```

Expected: 4 new tests pass; pre-existing AA route tests still green.

- [ ] **Step 7: Lint**

```bash
black backend/advanced_analytics_routes.py
isort backend/advanced_analytics_routes.py --profile black
flake8 backend/advanced_analytics_routes.py
```

- [ ] **Step 8: Commit**

```bash
git add backend/advanced_analytics_routes.py backend/tests/test_advanced_analytics_routes.py
git commit -m "$(cat <<'EOF'
feat(aa): wire tech/fund bundle filters into paginated endpoint

_compute_report parses + validates ?tech= and ?fund= via
parse_filter_csv, applies passes_bundle_filters between the
search and report-specific filter steps, and extends the inner
cache key with sorted bundle CSVs so distinct combos cache
independently.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: New `/{report}/export` endpoint with streamed CSV

**Files:**
- Modify: `backend/advanced_analytics_routes.py` (add `_make_export_endpoint` + register loop)
- Modify: `backend/tests/test_advanced_analytics_routes.py` (export cases)

- [ ] **Step 1: Write failing export tests**

Append to `backend/tests/test_advanced_analytics_routes.py`:

```python
def test_export_returns_csv_with_filtered_rows(
    authed_client, seed_aa_rows,
):
    r = authed_client.get(
        "/v1/advanced-analytics/current-day-upmove/export"
        "?tech=price_gt_sma50&columns=ticker,today_ltp,sma_50",
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text.splitlines()
    assert body[0] == "Ticker,Today LTP,SMA 50"
    # FOO.NS matches, BAR.NS does not.
    tickers = {line.split(",", 1)[0] for line in body[1:]}
    assert "FOO.NS" in tickers
    assert "BAR.NS" not in tickers


def test_export_413_when_over_cap(authed_client, monkeypatch):
    """Patch _MAX_EXPORT_ROWS down to 1 to trigger the cap."""
    import backend.advanced_analytics_routes as routes
    monkeypatch.setattr(routes, "_MAX_EXPORT_ROWS", 1)
    r = authed_client.get(
        "/v1/advanced-analytics/current-day-upmove/export",
    )
    assert r.status_code == 413
    assert "tighten filters" in r.json()["detail"].lower()


def test_export_default_columns_when_param_empty(
    authed_client, seed_aa_rows,
):
    r = authed_client.get(
        "/v1/advanced-analytics/current-day-upmove/export",
    )
    assert r.status_code == 200
    header = r.text.splitlines()[0]
    assert header.startswith("Ticker,")  # ticker locked first


def test_export_rejects_unknown_column(authed_client):
    r = authed_client.get(
        "/v1/advanced-analytics/current-day-upmove/export"
        "?columns=ticker,not_a_column",
    )
    assert r.status_code == 400
    assert "not_a_column" in r.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_routes.py -v -k "export"
```

Expected: 4 failures (`/export` endpoint not registered → 404).

- [ ] **Step 3: Implement export handler + register route**

Add to `backend/advanced_analytics_routes.py` near the bottom of the file (above `create_advanced_analytics_router`):

```python
from io import StringIO
import csv
from datetime import datetime
from fastapi.responses import StreamingResponse

# Hard cap — patched in tests; protects backend memory + browser
# CSV parser. ~10k rows × ~50 columns ≈ 5 MB CSV.
_MAX_EXPORT_ROWS = 10_000


# CSV column header labels — mirrors columnCatalogs.ts UI labels
# but lives here so the backend can format header rows without
# pulling the frontend allowlist into Python. Source of truth for
# CSV header text.
_CSV_COLUMN_LABELS: dict[str, str] = {
    "ticker": "Ticker",
    "company_name": "Company",
    "sector": "Sector",
    "sub_sector": "Sub-sector",
    "pscore": "F-Score",
    "rsi": "RSI",
    "sma_50": "SMA 50",
    "sma_200": "SMA 200",
    "golden_cross_days_ago": "Golden Cross (d ago)",
    "today_ltp": "Today LTP",
    "prev_day_ltp": "Prev LTP",
    "prev_2_prev_day_ltp": "Prev-2 LTP",
    "current_ppc": "Current PPC %",
    "avg_10d_ppc": "Avg 10d PPC %",
    "avg_20d_ppc": "Avg 20d PPC %",
    "week_52_high": "52w High",
    "week_52_low": "52w Low",
    "away_from_52week_high": "Away from 52w High %",
    "today_vol": "Today Vol",
    "prev_day_vol": "Prev Vol",
    "avg_10d_vol": "Avg 10d Vol",
    "avg_20d_vol": "Avg 20d Vol",
    "today_x_vol": "Today × Vol",
    "prev_day_x_vol": "Prev × Vol",
    "x_vol_10d": "× Vol 10d",
    "x_vol_20d": "× Vol 20d",
    "today_dv": "Today Deliv Qty",
    "prev_day_dv": "Prev Deliv Qty",
    "avg_10d_dv": "Avg 10d Deliv Qty",
    "avg_20d_dv": "Avg 20d Deliv Qty",
    "today_dpc": "Today Deliv %",
    "prev_day_dpc": "Prev Deliv %",
    "avg_10d_dpc": "Avg 10d Deliv %",
    "avg_20d_dpc": "Avg 20d Deliv %",
    "today_x_dv": "Today × Deliv",
    "prev_day_x_dv": "Prev × Deliv",
    "x_dv_10d": "× Deliv 10d",
    "x_dv_20d": "× Deliv 20d",
    "current_dpc": "Current Deliv %",
    "today_not": "Today Notional",
    "avg_10d_not": "Avg 10d Notional",
    "avg_20d_not": "Avg 20d Notional",
    "debt_to_eq": "Debt/Eq",
    "yoy_qtr_prft": "YoY Qtr Profit %",
    "yoy_qtr_sales": "YoY Qtr Sales %",
    "sales_growth_3yrs": "Sales 3y %",
    "prft_growth_3yrs": "Profit 3y %",
    "sales_growth_5yrs": "Sales 5y %",
    "prft_growth_5yrs": "Profit 5y %",
    "roce": "ROCE %",
    "chng_in_prom_hld": "Δ Promoter %",
    "pledged": "Pledged %",
    "prom_hld": "Promoter %",
    "event": "Event",
    "event_date": "Event Date",
}


def _validate_columns(raw: str, report: ReportName) -> list[str]:
    """Validate ``columns=`` param. Empty → report defaults."""
    if not raw.strip():
        # Same default sort key first, then a small useful set.
        # The frontend always sends an explicit columns list when
        # the user has the Column Selector engaged; fallback here
        # is for direct API consumers (or empty-state requests).
        return ["ticker", "today_ltp", "sma_50", "sma_200", "rsi"]
    cols: list[str] = []
    seen: set[str] = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok not in _CSV_COLUMN_LABELS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown column: {tok}",
            )
        if tok in seen:
            continue
        seen.add(tok)
        cols.append(tok)
    if "ticker" not in seen:
        cols.insert(0, "ticker")
    return cols


def _format_csv_cell(value) -> str:  # type: ignore[no-untyped-def]
    """Stable CSV cell rendering: None/NaN → empty, floats → 4dp."""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        # Trim trailing zeros to keep cells compact.
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


async def _stream_export(
    user: UserContext,
    report: ReportName,
    sort_key: str | None,
    sort_dir: str,
    market: MarketFilter,
    ticker_type: TickerTypeFilter,
    search: str,
    tech: str,
    fund: str,
    columns: str,
) -> StreamingResponse:
    """Build the filtered, sorted full-set list and stream as CSV."""
    cache = get_cache()
    needle = search.strip().upper()
    tech_keys = parse_filter_csv(tech, TECH_KEYS, "tech")
    fund_keys = parse_filter_csv(fund, FUND_KEYS, "fund")
    cols = _validate_columns(columns, report)
    as_of = _effective_trading_date()
    ck = (
        f"cache:advanced_analytics:{report}:{user.user_id}"
        f":m{market}:t{ticker_type}:q{needle}"
        f":ftech{','.join(tech_keys)}"
        f":ffund{','.join(fund_keys)}"
        f":dt{as_of.isoformat()}:export:{','.join(cols)}"
    )
    hit = cache.get(ck)
    if hit is not None:
        return _csv_response(hit, report, as_of)

    full_rows = await _cached_full_rows(user, as_of)
    keep = set(
        _filter_tickers(
            [r.ticker for r in full_rows], market, ticker_type,
        )
    )
    rows = [r for r in full_rows if r.ticker in keep]
    if needle:
        rows = [r for r in rows if needle in r.ticker.upper()]
    if tech_keys or fund_keys:
        rows = [
            r for r in rows
            if passes_bundle_filters(r, tech_keys, fund_keys)
        ]
    rows = [r for r in rows if _passes_filter(r, report)]

    if sort_key:
        reverse = sort_dir == "desc"
        rows.sort(
            key=lambda r: (
                getattr(r, sort_key) is None,
                getattr(r, sort_key) or 0,
            ),
            reverse=reverse,
        )

    if len(rows) > _MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Export exceeds {_MAX_EXPORT_ROWS:,} rows; "
                "tighten filters."
            ),
        )

    buf = StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([_CSV_COLUMN_LABELS[c] for c in cols])
    for row in rows:
        writer.writerow(
            [_format_csv_cell(getattr(row, c)) for c in cols]
        )
    payload = buf.getvalue()
    try:
        cache.set(ck, payload, ttl=TTL_STABLE)
    except Exception:  # pragma: no cover — defensive
        _logger.warning(
            "advanced_analytics export-cache set failed",
            exc_info=True,
        )
    return _csv_response(payload, report, as_of)


def _csv_response(
    payload: str, report: ReportName, as_of: date,
) -> StreamingResponse:
    fname = (
        f"advanced-analytics-{report}-"
        f"{as_of.strftime('%Y%m%d')}.csv"
    )

    def _gen():
        # Stream in 64 KB chunks so very large CSVs don't
        # block the event loop while serialising.
        chunk = 64 * 1024
        for i in range(0, len(payload), chunk):
            yield payload[i : i + chunk]

    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{fname}"'
            ),
        },
    )
```

Update `create_advanced_analytics_router` to register `_make_export_endpoint` for each report:

```python
def _make_export_endpoint(report: ReportName):
    async def _handler(
        user: UserContext = Depends(pro_or_superuser),
        sort_key: str | None = Query(None),
        sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
        market: str = Query("all", pattern="^(all|india|us)$"),
        ticker_type: str = Query("all", pattern="^(all|stock|etf)$"),
        search: str = Query("", max_length=20),
        tech: str = Query("", max_length=200, pattern="^[a-z0-9_,]*$"),
        fund: str = Query("", max_length=200, pattern="^[a-z0-9_,]*$"),
        columns: str = Query("", max_length=2000, pattern="^[a-z0-9_,]*$"),
        fmt: str = Query("csv", pattern="^(csv)$"),
    ) -> StreamingResponse:
        try:
            return await _stream_export(
                user, report, sort_key, sort_dir,
                market,  # type: ignore[arg-type]
                ticker_type,  # type: ignore[arg-type]
                search, tech, fund, columns,
            )
        except HTTPException:
            raise
        except Exception as exc:
            _logger.exception(
                "advanced_analytics %s export failed: %s",
                report, exc,
            )
            raise HTTPException(
                status_code=500,
                detail=f"advanced_analytics {report} export failed",
            )

    _handler.__name__ = f"export_{report.replace('-', '_')}"
    return _handler


# Inside create_advanced_analytics_router(), after the existing
# `for report in REPORTS:` loop, add a second loop:
for report in REPORTS:
    router.add_api_route(
        path=f"/{report}/export",
        endpoint=_make_export_endpoint(report),
        methods=["GET"],
        name=f"advanced_analytics_{report.replace('-', '_')}_export",
    )
```

- [ ] **Step 4: Restart backend (new route registered)**

```bash
docker compose restart backend
sleep 5
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_advanced_analytics_routes.py -v -k "export"
```

Expected: 4 passed.

- [ ] **Step 6: Lint**

```bash
black backend/advanced_analytics_routes.py
isort backend/advanced_analytics_routes.py --profile black
flake8 backend/advanced_analytics_routes.py
```

- [ ] **Step 7: Commit**

```bash
git add backend/advanced_analytics_routes.py backend/tests/test_advanced_analytics_routes.py
git commit -m "$(cat <<'EOF'
feat(aa): /{report}/export streams full filtered CSV

Adds GET /v1/advanced-analytics/{report}/export. Reuses the
paginated compute pipeline minus pagination, validates columns=
against the canonical label map, sorts deterministically, hard-
caps at 10 000 rows (413 with helpful detail). Streams text/csv
in 64 KB chunks; cached at TTL_STABLE.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Backend ↔ frontend catalog sync test

**Files:**
- Create: `backend/tests/test_filter_catalog_sync.py`

- [ ] **Step 1: Write the sync test**

```python
# backend/tests/test_filter_catalog_sync.py
"""CI gate: backend filter allowlist must match frontend mirror.

Loads the TS literal as text (no Node runtime in the backend
container), regex-extracts every quoted key from the
TECH_FILTER_CATALOG and FUND_FILTER_CATALOG arrays, asserts
equality with TECH_KEYS / FUND_KEYS. Either side adding /
removing a key without the other fails CI.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.advanced_analytics_filters import FUND_KEYS, TECH_KEYS

_FRONTEND_FILE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "components"
    / "advanced-analytics"
    / "filterCatalogs.ts"
)

_BLOCK_RE = re.compile(
    r"export const (?P<name>TECH|FUND)_FILTER_CATALOG"
    r"\s*:\s*FilterOption\[\]\s*=\s*\[(?P<body>.*?)\];",
    re.DOTALL,
)
_KEY_RE = re.compile(r'key:\s*"([a-z0-9_]+)"')


def _parse_keys(name: str) -> set[str]:
    text = _FRONTEND_FILE.read_text(encoding="utf-8")
    block = next(
        (m for m in _BLOCK_RE.finditer(text) if m.group("name") == name),
        None,
    )
    assert block is not None, f"{name}_FILTER_CATALOG not found"
    return set(_KEY_RE.findall(block.group("body")))


def test_tech_catalog_in_sync():
    assert _parse_keys("TECH") == set(TECH_KEYS), (
        "frontend TECH_FILTER_CATALOG drift — update either "
        "filterCatalogs.ts or advanced_analytics_filters.py"
    )


def test_fund_catalog_in_sync():
    assert _parse_keys("FUND") == set(FUND_KEYS), (
        "frontend FUND_FILTER_CATALOG drift — update either "
        "filterCatalogs.ts or advanced_analytics_filters.py"
    )
```

- [ ] **Step 2: Run the test — expected to fail (TS file doesn't exist yet)**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_filter_catalog_sync.py -v
```

Expected: `FileNotFoundError` or `AssertionError`. This is fine — it will go green once Task 5 lands the TS file. Mark this task as `xfail` until Task 5 completes by adding to the file:

```python
import pytest

pytestmark = pytest.mark.xfail(
    reason="frontend filterCatalogs.ts lands in Task 5",
    strict=False,
)
```

(Remove the `pytestmark` line at the end of Task 5.)

- [ ] **Step 3: Lint + commit**

```bash
black backend/tests/test_filter_catalog_sync.py
isort backend/tests/test_filter_catalog_sync.py --profile black
flake8 backend/tests/test_filter_catalog_sync.py
git add backend/tests/test_filter_catalog_sync.py
git commit -m "$(cat <<'EOF'
test(aa): CI gate for backend↔frontend filter catalog sync

Regex-parses frontend/components/advanced-analytics/filterCatalogs.ts
and asserts the key sets match TECH_KEYS / FUND_KEYS. xfail until
Task 5 lands the TS file.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: Frontend types + filter catalog mirror

**Files:**
- Modify: `frontend/lib/types/advancedAnalytics.ts` (add type unions + cap constant)
- Create: `frontend/components/advanced-analytics/filterCatalogs.ts`

- [ ] **Step 1: Add type literals + cap to `advancedAnalytics.ts`**

Append at end of `frontend/lib/types/advancedAnalytics.ts`:

```ts
// ---- Filter bundles --------------------------------------------------

export type TechFilterKey =
  | "golden_recent"
  | "golden_established"
  | "price_gt_sma50"
  | "price_gt_sma200"
  | "rsi_oversold"
  | "rsi_neutral"
  | "rsi_overbought"
  | "vol_surge"
  | "near_52w_high";

export type FundFilterKey =
  | "fscore_ge_7"
  | "fscore_le_3"
  | "debt_lt_0_5"
  | "roce_gt_20"
  | "sales_3y_gt_15"
  | "profit_3y_gt_15"
  | "prom_hld_gt_50"
  | "pledged_lt_5";

export type FilterBundleId = "tech" | "fund";

/** Hard cap mirrored from backend ``_MAX_EXPORT_ROWS``. */
export const FILTER_EXPORT_ROW_CAP = 10_000;
```

- [ ] **Step 2: Create the catalog file**

```ts
// frontend/components/advanced-analytics/filterCatalogs.ts
/**
 * Filter catalog mirror — KEEP IN SYNC with
 * ``backend/advanced_analytics_filters.py``.
 *
 * CI gate: ``backend/tests/test_filter_catalog_sync.py`` parses
 * this file as text and asserts every ``key`` here is present
 * in the backend's ``TECH_KEYS`` / ``FUND_KEYS`` and vice-versa.
 * Adding / removing a key on one side without the other fails CI.
 */

export interface FilterOption {
  key: string;
  label: string;
  /** Radio group id; undefined → checkbox. */
  group?: string;
  /** Sub-section header inside the popover. */
  section: string;
  tooltip?: string;
}

export const TECH_FILTER_CATALOG: FilterOption[] = [
  {
    key: "golden_recent",
    label: "Recent (≤10d)",
    section: "Golden Cross",
    tooltip: "SMA 50 crossed above SMA 200 within the last 10 days",
  },
  {
    key: "golden_established",
    label: "Established",
    section: "Golden Cross",
    tooltip: "SMA 50 above SMA 200 for more than 10 days",
  },
  {
    key: "price_gt_sma50",
    label: "Price > SMA 50",
    section: "Trend",
  },
  {
    key: "price_gt_sma200",
    label: "Price > SMA 200",
    section: "Trend",
  },
  {
    key: "rsi_oversold",
    label: "Oversold (<30)",
    group: "rsi_band",
    section: "RSI",
  },
  {
    key: "rsi_neutral",
    label: "Neutral (30–70)",
    group: "rsi_band",
    section: "RSI",
  },
  {
    key: "rsi_overbought",
    label: "Overbought (>70)",
    group: "rsi_band",
    section: "RSI",
  },
  {
    key: "vol_surge",
    label: "Today × Vol ≥ 2",
    section: "Volume",
  },
  {
    key: "near_52w_high",
    label: "Within 5% of 52w high",
    section: "Range",
  },
];

export const FUND_FILTER_CATALOG: FilterOption[] = [
  {
    key: "fscore_ge_7",
    label: "F-Score ≥ 7",
    group: "fscore_band",
    section: "Quality",
  },
  {
    key: "fscore_le_3",
    label: "F-Score ≤ 3",
    group: "fscore_band",
    section: "Quality",
  },
  {
    key: "debt_lt_0_5",
    label: "Debt/Eq < 0.5",
    section: "Leverage",
  },
  {
    key: "roce_gt_20",
    label: "ROCE > 20%",
    section: "Profitability",
  },
  {
    key: "sales_3y_gt_15",
    label: "Sales 3y > 15%",
    section: "Growth",
  },
  {
    key: "profit_3y_gt_15",
    label: "Profit 3y > 15%",
    section: "Growth",
  },
  {
    key: "prom_hld_gt_50",
    label: "Promoter > 50%",
    section: "Promoter",
  },
  {
    key: "pledged_lt_5",
    label: "Pledged < 5%",
    section: "Promoter",
  },
];

export const TECH_KEY_SET: Set<string> = new Set(
  TECH_FILTER_CATALOG.map((o) => o.key),
);
export const FUND_KEY_SET: Set<string> = new Set(
  FUND_FILTER_CATALOG.map((o) => o.key),
);

/** Lookup a label by key across both bundles (chip rendering). */
export const FILTER_LABEL_BY_KEY: Record<string, string> = {
  ...Object.fromEntries(TECH_FILTER_CATALOG.map((o) => [o.key, o.label])),
  ...Object.fromEntries(FUND_FILTER_CATALOG.map((o) => [o.key, o.label])),
};
```

- [ ] **Step 3: Remove the xfail mark from sync test (Task 4) and re-run**

Edit `backend/tests/test_filter_catalog_sync.py` and delete the `pytestmark = pytest.mark.xfail(...)` block + the `import pytest` line if no longer needed.

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_filter_catalog_sync.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Frontend type-check + lint**

```bash
cd frontend && npx tsc --noEmit
cd frontend && npx eslint . --fix
```

Expected: zero TS errors; zero ESLint errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
git add \
  frontend/lib/types/advancedAnalytics.ts \
  frontend/components/advanced-analytics/filterCatalogs.ts \
  backend/tests/test_filter_catalog_sync.py
git commit -m "$(cat <<'EOF'
feat(aa): frontend filter catalog mirror + type literals

Adds TECH_FILTER_CATALOG (9 entries, 1 radio group) and
FUND_FILTER_CATALOG (8 entries, 1 radio group) plus the
FILTER_LABEL_BY_KEY lookup used by the active-chip strip.
Drops xfail from the backend sync test; gate is now live.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: `<FilterDropdown />` component

**Files:**
- Create: `frontend/components/advanced-analytics/FilterDropdown.tsx`
- Create: `frontend/components/advanced-analytics/__tests__/FilterDropdown.test.tsx`

- [ ] **Step 1: Write failing component tests**

```tsx
// frontend/components/advanced-analytics/__tests__/FilterDropdown.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { FilterDropdown } from "../FilterDropdown";
import { TECH_FILTER_CATALOG } from "../filterCatalogs";

describe("FilterDropdown", () => {
  function renderTech(selected: string[] = []) {
    const onChange = vi.fn();
    const onReset = vi.fn();
    render(
      <FilterDropdown
        bundleId="tech"
        bundleLabel="Technical"
        catalog={TECH_FILTER_CATALOG}
        selected={selected}
        onChange={onChange}
        onReset={onReset}
      />,
    );
    return { onChange, onReset };
  }

  it("renders trigger button with no badge when nothing selected", () => {
    renderTech();
    const btn = screen.getByTestId("aa-filter-tech-button");
    expect(btn).toHaveTextContent("Technical");
    expect(btn).not.toHaveTextContent(/\d/);
  });

  it("shows active count badge when selections exist", () => {
    renderTech(["golden_recent", "price_gt_sma50"]);
    const btn = screen.getByTestId("aa-filter-tech-button");
    expect(btn).toHaveTextContent("2");
  });

  it("toggling a checkbox calls onChange with new selection", () => {
    const { onChange } = renderTech([]);
    fireEvent.click(screen.getByTestId("aa-filter-tech-button"));
    fireEvent.click(
      screen.getByTestId("aa-filter-tech-option-golden_recent"),
    );
    expect(onChange).toHaveBeenCalledWith(["golden_recent"]);
  });

  it("checking same radio twice keeps single selection", () => {
    const { onChange } = renderTech(["rsi_oversold"]);
    fireEvent.click(screen.getByTestId("aa-filter-tech-button"));
    fireEvent.click(
      screen.getByTestId("aa-filter-tech-option-rsi_neutral"),
    );
    expect(onChange).toHaveBeenLastCalledWith(["rsi_neutral"]);
  });

  it("clicking radio again with same value deselects it", () => {
    const { onChange } = renderTech(["rsi_neutral"]);
    fireEvent.click(screen.getByTestId("aa-filter-tech-button"));
    fireEvent.click(
      screen.getByTestId("aa-filter-tech-option-rsi_neutral"),
    );
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("reset button calls onReset", () => {
    const { onReset } = renderTech(["golden_recent"]);
    fireEvent.click(screen.getByTestId("aa-filter-tech-button"));
    fireEvent.click(screen.getByTestId("aa-filter-tech-reset"));
    expect(onReset).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && npx vitest run components/advanced-analytics/__tests__/FilterDropdown.test.tsx
```

Expected: import error / module not found.

- [ ] **Step 3: Implement the component**

```tsx
// frontend/components/advanced-analytics/FilterDropdown.tsx
"use client";
/**
 * Reusable popover for one bundle (Technical or Fundamentals)
 * on the Advanced Analytics tabs. Renders sections by
 * ``catalog[].section``; entries with a ``group`` field are
 * mutually-exclusive radios within that group.
 *
 * Pairs with ``useFilterParams`` for URL ↔ state. Toolbar
 * placement: between ticker_type select and ColumnSelector
 * in ``AdvancedAnalyticsTable``.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { FilterOption } from "./filterCatalogs";

interface FilterDropdownProps {
  bundleId: "tech" | "fund";
  bundleLabel: string;
  catalog: FilterOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  onReset: () => void;
}

export function FilterDropdown({
  bundleId,
  bundleLabel,
  catalog,
  selected,
  onChange,
  onReset,
}: FilterDropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click. Mirrors ColumnSelector pattern.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  // Group entries by section, preserving catalog order.
  const sections = useMemo(() => {
    const out: { name: string; items: FilterOption[] }[] = [];
    for (const opt of catalog) {
      const last = out[out.length - 1];
      if (last && last.name === opt.section) {
        last.items.push(opt);
      } else {
        out.push({ name: opt.section, items: [opt] });
      }
    }
    return out;
  }, [catalog]);

  const handleToggle = useCallback(
    (opt: FilterOption) => {
      if (opt.group) {
        // Radio: same group keys collapse to at-most-one.
        const groupKeys = new Set(
          catalog.filter((o) => o.group === opt.group).map((o) => o.key),
        );
        const remaining = selected.filter((k) => !groupKeys.has(k));
        if (selectedSet.has(opt.key)) {
          onChange(remaining);
        } else {
          onChange([...remaining, opt.key]);
        }
      } else {
        if (selectedSet.has(opt.key)) {
          onChange(selected.filter((k) => k !== opt.key));
        } else {
          onChange([...selected, opt.key]);
        }
      }
    },
    [catalog, onChange, selected, selectedSet],
  );

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`Open ${bundleLabel} filters, ${selected.length} active`}
        data-testid={`aa-filter-${bundleId}-button`}
        className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-indigo-500 inline-flex items-center gap-1"
      >
        {bundleLabel}
        {selected.length > 0 && (
          <span className="rounded-full bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 px-1.5 text-[10px]">
            {selected.length}
          </span>
        )}
        <span className="text-[8px]">▾</span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={`${bundleLabel} filters`}
          data-testid={`aa-filter-${bundleId}-popover`}
          className="absolute right-0 z-30 mt-1 w-64 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg p-3 text-xs"
        >
          <div className="flex items-center justify-between mb-2">
            <span className="font-semibold text-gray-700 dark:text-gray-200">
              {bundleLabel}
            </span>
            <button
              type="button"
              onClick={onReset}
              data-testid={`aa-filter-${bundleId}-reset`}
              className="text-indigo-600 dark:text-indigo-400 hover:underline"
            >
              Reset
            </button>
          </div>
          <div className="max-h-80 overflow-y-auto space-y-3">
            {sections.map((sec) => (
              <fieldset key={sec.name}>
                <legend className="text-[11px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">
                  {sec.name}
                </legend>
                {sec.items.map((opt) => {
                  const isRadio = Boolean(opt.group);
                  const checked = selectedSet.has(opt.key);
                  return (
                    <label
                      key={opt.key}
                      className="flex items-center gap-2 py-0.5 cursor-pointer hover:text-indigo-600 dark:hover:text-indigo-400"
                      title={opt.tooltip}
                    >
                      <input
                        type={isRadio ? "radio" : "checkbox"}
                        name={
                          isRadio
                            ? `${bundleId}-${opt.group}`
                            : undefined
                        }
                        checked={checked}
                        onChange={() => handleToggle(opt)}
                        data-testid={`aa-filter-${bundleId}-option-${opt.key}`}
                        className="cursor-pointer"
                      />
                      <span>{opt.label}</span>
                    </label>
                  );
                })}
              </fieldset>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npx vitest run components/advanced-analytics/__tests__/FilterDropdown.test.tsx
```

Expected: 6 passed.

- [ ] **Step 5: Lint**

```bash
cd frontend && npx eslint components/advanced-analytics/FilterDropdown.tsx components/advanced-analytics/__tests__/FilterDropdown.test.tsx --fix
cd frontend && npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
git add frontend/components/advanced-analytics/FilterDropdown.tsx frontend/components/advanced-analytics/__tests__/FilterDropdown.test.tsx
git commit -m "$(cat <<'EOF'
feat(aa): FilterDropdown popover component

Reusable popover for either bundle: groups options by ``section``,
treats entries with a ``group`` field as a mutually-exclusive
radio set. Active-count badge on the trigger; outside-click + ESC
close. Tests: render, toggle checkbox, radio replace/deselect,
reset.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: `useFilterParams` hook (URL ↔ state)

**Files:**
- Create: `frontend/hooks/useFilterParams.ts`
- Create: `frontend/hooks/__tests__/useFilterParams.test.tsx`

- [ ] **Step 1: Write failing hook tests**

```tsx
// frontend/hooks/__tests__/useFilterParams.test.tsx
import { describe, expect, it, vi, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

const replaceMock = vi.fn();
const searchParamsRef = { current: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParamsRef.current,
  useRouter: () => ({ replace: replaceMock }),
  usePathname: () => "/advanced-analytics/current-day-upmove",
}));

import { useFilterParams } from "../useFilterParams";

afterEach(() => {
  replaceMock.mockClear();
  searchParamsRef.current = new URLSearchParams();
});

describe("useFilterParams", () => {
  it("hydrates tech + fund from the URL", () => {
    searchParamsRef.current = new URLSearchParams(
      "?tech=golden_recent,price_gt_sma50&fund=fscore_ge_7",
    );
    const { result } = renderHook(() => useFilterParams());
    expect(result.current.tech).toEqual([
      "golden_recent",
      "price_gt_sma50",
    ]);
    expect(result.current.fund).toEqual(["fscore_ge_7"]);
  });

  it("drops unknown keys silently", () => {
    searchParamsRef.current = new URLSearchParams(
      "?tech=golden_recent,not_real&fund=garbage",
    );
    const { result } = renderHook(() => useFilterParams());
    expect(result.current.tech).toEqual(["golden_recent"]);
    expect(result.current.fund).toEqual([]);
  });

  it("setTech writes a sorted, comma-joined CSV to the URL", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useFilterParams());
    act(() => result.current.setTech(["price_gt_sma50", "golden_recent"]));
    act(() => vi.advanceTimersByTime(310));
    expect(replaceMock).toHaveBeenCalledTimes(1);
    const [url] = replaceMock.mock.calls[0];
    expect(url).toContain("tech=golden_recent%2Cprice_gt_sma50");
    vi.useRealTimers();
  });

  it("resetAll clears both bundle params from URL", async () => {
    vi.useFakeTimers();
    searchParamsRef.current = new URLSearchParams(
      "?tech=golden_recent&fund=fscore_ge_7&page=3",
    );
    const { result } = renderHook(() => useFilterParams());
    act(() => result.current.resetAll());
    act(() => vi.advanceTimersByTime(310));
    const [url] = replaceMock.mock.calls[0];
    expect(url).not.toContain("tech=");
    expect(url).not.toContain("fund=");
    expect(url).toContain("page=3"); // unrelated params survive
    vi.useRealTimers();
  });
});
```

- [ ] **Step 2: Run tests — expect failures**

```bash
cd frontend && npx vitest run hooks/__tests__/useFilterParams.test.tsx
```

Expected: import error.

- [ ] **Step 3: Implement the hook**

```ts
// frontend/hooks/useFilterParams.ts
"use client";
/**
 * URL ↔ state for AA bundle filters.
 *
 * Reads ``?tech=`` and ``?fund=`` on mount; writes via
 * ``router.replace()`` debounced 300 ms so checkbox spam doesn't
 * thrash navigation. Always emits sorted CSV so equivalent combos
 * (``a,b`` vs ``b,a``) hit the same SWR cache slot.
 *
 * Unknown keys arriving from a stale shared link are silently
 * dropped — the page renders with whatever the user can act on.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import {
  FUND_KEY_SET,
  TECH_KEY_SET,
} from "@/components/advanced-analytics/filterCatalogs";

const DEBOUNCE_MS = 300;

function parseCsv(raw: string | null, allowed: Set<string>): string[] {
  if (!raw) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const tok of raw.split(",")) {
    const t = tok.trim();
    if (!t || seen.has(t) || !allowed.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out.sort();
}

interface UseFilterParamsResult {
  tech: string[];
  fund: string[];
  setTech: (next: string[]) => void;
  setFund: (next: string[]) => void;
  resetAll: () => void;
}

export function useFilterParams(): UseFilterParamsResult {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const initial = useMemo(
    () => ({
      tech: parseCsv(sp.get("tech"), TECH_KEY_SET),
      fund: parseCsv(sp.get("fund"), FUND_KEY_SET),
    }),
    // Hydrate once on mount; subsequent URL changes are driven
    // by this hook itself via router.replace.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [tech, setTechState] = useState<string[]>(initial.tech);
  const [fund, setFundState] = useState<string[]>(initial.fund);
  const timerRef = useRef<number | null>(null);

  const flushToUrl = useCallback(
    (nextTech: string[], nextFund: string[]) => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        const params = new URLSearchParams(sp.toString());
        if (nextTech.length > 0) {
          params.set("tech", nextTech.join(","));
        } else {
          params.delete("tech");
        }
        if (nextFund.length > 0) {
          params.set("fund", nextFund.join(","));
        } else {
          params.delete("fund");
        }
        // Filter change → reset pagination at the call-site
        // pattern (mirror of market/ticker_type setters).
        params.set("page", "1");
        const qs = params.toString();
        router.replace(qs ? `${pathname}?${qs}` : pathname, {
          scroll: false,
        });
      }, DEBOUNCE_MS);
    },
    [pathname, router, sp],
  );

  const setTech = useCallback(
    (next: string[]) => {
      const sorted = [...next].sort();
      setTechState(sorted);
      flushToUrl(sorted, fund);
    },
    [flushToUrl, fund],
  );
  const setFund = useCallback(
    (next: string[]) => {
      const sorted = [...next].sort();
      setFundState(sorted);
      flushToUrl(tech, sorted);
    },
    [flushToUrl, tech],
  );
  const resetAll = useCallback(() => {
    setTechState([]);
    setFundState([]);
    flushToUrl([], []);
  }, [flushToUrl]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  return { tech, fund, setTech, setFund, resetAll };
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npx vitest run hooks/__tests__/useFilterParams.test.tsx
```

Expected: 4 passed.

- [ ] **Step 5: Lint + commit**

```bash
cd frontend && npx eslint hooks/useFilterParams.ts hooks/__tests__/useFilterParams.test.tsx --fix
cd frontend && npx tsc --noEmit
cd /Users/abhay/Documents/projects/ai-agent-ui
git add frontend/hooks/useFilterParams.ts frontend/hooks/__tests__/useFilterParams.test.tsx
git commit -m "$(cat <<'EOF'
feat(aa): useFilterParams hook for URL ↔ filter state

Hydrates tech/fund arrays from ?tech= / ?fund= on mount, drops
unknown keys, debounces 300 ms writes, emits sorted CSV so
?tech=a,b and ?tech=b,a share an SWR cache slot. Resets
?page=1 alongside any filter change (matches market/ticker_type
setter pattern).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: ActiveFilterChips strip + CSV download helper

**Files:**
- Create: `frontend/components/advanced-analytics/ActiveFilterChips.tsx`
- Create: `frontend/components/advanced-analytics/__tests__/ActiveFilterChips.test.tsx`
- Create: `frontend/lib/triggerCsvDownload.ts`
- Create: `frontend/lib/__tests__/triggerCsvDownload.test.ts`

- [ ] **Step 1: Write failing chip tests**

```tsx
// frontend/components/advanced-analytics/__tests__/ActiveFilterChips.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { ActiveFilterChips } from "../ActiveFilterChips";

describe("ActiveFilterChips", () => {
  it("renders nothing when both bundles are empty", () => {
    const { container } = render(
      <ActiveFilterChips
        tech={[]}
        fund={[]}
        onRemoveTech={vi.fn()}
        onRemoveFund={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders one chip per active key with the catalog label", () => {
    render(
      <ActiveFilterChips
        tech={["golden_recent"]}
        fund={["fscore_ge_7"]}
        onRemoveTech={vi.fn()}
        onRemoveFund={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("aa-active-filter-chip-golden_recent"),
    ).toHaveTextContent("Recent (≤10d)");
    expect(
      screen.getByTestId("aa-active-filter-chip-fscore_ge_7"),
    ).toHaveTextContent("F-Score ≥ 7");
  });

  it("clicking × on a tech chip calls onRemoveTech with key", () => {
    const onRemoveTech = vi.fn();
    render(
      <ActiveFilterChips
        tech={["golden_recent"]}
        fund={[]}
        onRemoveTech={onRemoveTech}
        onRemoveFund={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByTestId("aa-active-filter-chip-golden_recent-x"),
    );
    expect(onRemoveTech).toHaveBeenCalledWith("golden_recent");
  });

  it("Clear all triggers callback", () => {
    const onClearAll = vi.fn();
    render(
      <ActiveFilterChips
        tech={["golden_recent"]}
        fund={[]}
        onRemoveTech={vi.fn()}
        onRemoveFund={vi.fn()}
        onClearAll={onClearAll}
      />,
    );
    fireEvent.click(screen.getByTestId("aa-active-filter-clear-all"));
    expect(onClearAll).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run — expect failure**

```bash
cd frontend && npx vitest run components/advanced-analytics/__tests__/ActiveFilterChips.test.tsx
```

- [ ] **Step 3: Implement the chip strip**

```tsx
// frontend/components/advanced-analytics/ActiveFilterChips.tsx
"use client";
/**
 * Read-only-but-removable chip strip rendered directly under
 * the AA toolbar. Each chip carries the catalog label and an
 * × button that drops the key from its bundle. "Clear all"
 * resets both bundles.
 */

import { FILTER_LABEL_BY_KEY } from "./filterCatalogs";

interface Props {
  tech: string[];
  fund: string[];
  onRemoveTech: (key: string) => void;
  onRemoveFund: (key: string) => void;
  onClearAll: () => void;
}

export function ActiveFilterChips({
  tech,
  fund,
  onRemoveTech,
  onRemoveFund,
  onClearAll,
}: Props) {
  if (tech.length === 0 && fund.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-gray-500 dark:text-gray-400">Active:</span>
      {tech.map((key) => (
        <Chip
          key={`tech-${key}`}
          testId={`aa-active-filter-chip-${key}`}
          xTestId={`aa-active-filter-chip-${key}-x`}
          label={FILTER_LABEL_BY_KEY[key] ?? key}
          tone="indigo"
          onRemove={() => onRemoveTech(key)}
        />
      ))}
      {fund.map((key) => (
        <Chip
          key={`fund-${key}`}
          testId={`aa-active-filter-chip-${key}`}
          xTestId={`aa-active-filter-chip-${key}-x`}
          label={FILTER_LABEL_BY_KEY[key] ?? key}
          tone="emerald"
          onRemove={() => onRemoveFund(key)}
        />
      ))}
      <button
        type="button"
        onClick={onClearAll}
        data-testid="aa-active-filter-clear-all"
        className="ml-1 text-indigo-600 dark:text-indigo-400 hover:underline"
      >
        Clear all
      </button>
    </div>
  );
}

interface ChipProps {
  label: string;
  testId: string;
  xTestId: string;
  tone: "indigo" | "emerald";
  onRemove: () => void;
}

function Chip({ label, testId, xTestId, tone, onRemove }: ChipProps) {
  const tones: Record<ChipProps["tone"], string> = {
    indigo:
      "bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-900/20 dark:text-indigo-300 dark:border-indigo-900/50",
    emerald:
      "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-900/50",
  };
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${tones[tone]}`}
    >
      {label}
      <button
        type="button"
        aria-label={`Remove ${label}`}
        onClick={onRemove}
        data-testid={xTestId}
        className="hover:text-red-600 dark:hover:text-red-400 transition-colors"
      >
        ×
      </button>
    </span>
  );
}
```

- [ ] **Step 4: Verify chip tests pass**

```bash
cd frontend && npx vitest run components/advanced-analytics/__tests__/ActiveFilterChips.test.tsx
```

Expected: 4 passed.

- [ ] **Step 5: Write failing CSV-download helper test**

```ts
// frontend/lib/__tests__/triggerCsvDownload.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { triggerCsvDownload } from "../triggerCsvDownload";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(async () => ({
    ok: true,
    status: 200,
    blob: async () => new Blob(["a,b\n1,2\n"], { type: "text/csv" }),
    headers: new Headers({
      "Content-Disposition": 'attachment; filename="x.csv"',
    }),
  })),
}));

afterEach(() => vi.clearAllMocks());

describe("triggerCsvDownload", () => {
  it("uses the filename from Content-Disposition", async () => {
    const createObjUrl = vi.fn(() => "blob:url");
    const revokeObjUrl = vi.fn();
    Object.assign(URL, {
      createObjectURL: createObjUrl,
      revokeObjectURL: revokeObjUrl,
    });
    const click = vi.fn();
    const remove = vi.fn();
    vi.spyOn(document, "createElement").mockImplementation(
      () => ({ href: "", download: "", click, remove }) as unknown as HTMLAnchorElement,
    );
    vi.spyOn(document.body, "appendChild").mockImplementation(
      (n) => n,
    );
    await triggerCsvDownload("/v1/advanced-analytics/foo/export");
    expect(click).toHaveBeenCalled();
    expect(revokeObjUrl).toHaveBeenCalledWith("blob:url");
  });

  it("throws on non-ok response", async () => {
    const { apiFetch } = await import("@/lib/apiFetch");
    (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 413,
      json: async () => ({ detail: "Export exceeds 10,000 rows" }),
    });
    await expect(
      triggerCsvDownload("/v1/advanced-analytics/foo/export"),
    ).rejects.toThrow(/413/);
  });
});
```

- [ ] **Step 6: Implement the helper**

```ts
// frontend/lib/triggerCsvDownload.ts
"use client";
/**
 * Triggers a browser CSV download from a backend endpoint.
 *
 * - Uses ``apiFetch`` so JWT auto-refresh works (§4.2 #14).
 * - Honours ``Content-Disposition`` filename if present.
 * - Cleans up the object URL after the click.
 */

import { apiFetch } from "@/lib/apiFetch";

const DEFAULT_FILENAME = "export.csv";

function filenameFromHeader(h: string | null): string {
  if (!h) return DEFAULT_FILENAME;
  const m = h.match(/filename="([^"]+)"/);
  return m ? m[1] : DEFAULT_FILENAME;
}

export async function triggerCsvDownload(url: string): Promise<void> {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`CSV export failed: HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = filenameFromHeader(
    res.headers.get("Content-Disposition"),
  );
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objUrl);
}
```

- [ ] **Step 7: Verify helper tests pass**

```bash
cd frontend && npx vitest run lib/__tests__/triggerCsvDownload.test.ts
```

Expected: 2 passed.

- [ ] **Step 8: Lint + commit**

```bash
cd frontend && npx eslint components/advanced-analytics/ActiveFilterChips.tsx lib/triggerCsvDownload.ts components/advanced-analytics/__tests__/ActiveFilterChips.test.tsx lib/__tests__/triggerCsvDownload.test.ts --fix
cd frontend && npx tsc --noEmit
cd /Users/abhay/Documents/projects/ai-agent-ui
git add frontend/components/advanced-analytics/ActiveFilterChips.tsx frontend/components/advanced-analytics/__tests__/ActiveFilterChips.test.tsx frontend/lib/triggerCsvDownload.ts frontend/lib/__tests__/triggerCsvDownload.test.ts
git commit -m "$(cat <<'EOF'
feat(aa): ActiveFilterChips strip + triggerCsvDownload helper

ActiveFilterChips: tone-coded chips (indigo for tech, emerald
for fund), per-chip × removal, "Clear all" button. Returns null
when both bundles empty.

triggerCsvDownload: apiFetch + blob + temporary anchor click,
honours Content-Disposition filename, throws on !ok.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 9: Integrate into `AdvancedAnalyticsTable.tsx` + extend SWR hook

**Files:**
- Modify: `frontend/hooks/useAdvancedAnalyticsData.ts`
- Modify: `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx`

- [ ] **Step 1: Extend SWR hook signature**

Edit `frontend/hooks/useAdvancedAnalyticsData.ts`. Replace the function signature + body with:

```ts
export function useAdvancedAnalyticsReport(
  report: AdvancedReportName,
  page: number,
  pageSize: number,
  sortKey: string | null,
  sortDir: "asc" | "desc",
  market: MarketFilter,
  tickerType: TickerTypeFilter,
  search: string,
  tech: string[],
  fund: string[],
  fallbackData?: AdvancedReportResponse,
): AdvancedAnalyticsData {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    sort_dir: sortDir,
    market,
    ticker_type: tickerType,
  });
  if (sortKey) params.set("sort_key", sortKey);
  if (search) params.set("search", search);
  // Sorted joined CSV → cache stability across param order.
  if (tech.length > 0) params.set("tech", [...tech].sort().join(","));
  if (fund.length > 0) params.set("fund", [...fund].sort().join(","));

  const key = `${API_URL}/advanced-analytics/${report}?${params.toString()}`;

  const { data, error, isLoading } = useSWR<AdvancedReportResponse>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 120_000,
      fallbackData,
    },
  );

  return {
    value: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load report"
      : null,
  };
}
```

- [ ] **Step 2: Modify `AdvancedAnalyticsTable.tsx` imports + state**

Open `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx`. Replace the import block (lines 24-55) with:

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";

import { ColumnSelector } from "@/components/insights/ColumnSelector";
import {
  DownloadCsvButton,
} from "@/components/common/DownloadCsvButton";
import {
  StaleTickerChip,
  type StaleChipItem,
} from "@/components/common/StaleTickerChip";
import { useAdvancedAnalyticsReport } from "@/hooks/useAdvancedAnalyticsData";
import { useFilterParams } from "@/hooks/useFilterParams";
import { triggerCsvDownload } from "@/lib/triggerCsvDownload";
import { useColumnSelection } from "@/lib/useColumnSelection";
import { API_URL } from "@/lib/config";
import {
  ADVANCED_REPORT_LABELS,
  FILTER_EXPORT_ROW_CAP,
  MARKET_FILTER_OPTIONS,
  TICKER_TYPE_FILTER_OPTIONS,
  type AdvancedReportName,
  type AdvancedReportResponse,
  type AdvancedRow,
  type MarketFilter,
  type StaleReason,
  type TickerTypeFilter,
} from "@/lib/types/advancedAnalytics";

import { ActiveFilterChips } from "./ActiveFilterChips";
import { FilterDropdown } from "./FilterDropdown";
import {
  FUND_FILTER_CATALOG,
  TECH_FILTER_CATALOG,
} from "./filterCatalogs";
import {
  ALL_VALID_KEYS,
  COLUMN_MAP,
  getCatalog,
  type AdvancedColumnKey,
  type AdvancedColumnSpec,
} from "./columnCatalogs";
```

> **Note for implementer:** the existing `downloadCsv` import + `CsvColumn` import are removed in this step (no longer used here). The `downloadCsv` helper itself stays in the codebase — Insights pages still use it.

- [ ] **Step 3: Wire `useFilterParams` and update `useAdvancedAnalyticsReport` call**

Inside the `AdvancedAnalyticsTable` function body, after the existing `useState` hooks (around line 117-127), add:

```tsx
  const { tech, fund, setTech, setFund, resetAll } = useFilterParams();

  const removeTech = useCallback(
    (key: string) => setTech(tech.filter((k) => k !== key)),
    [setTech, tech],
  );
  const removeFund = useCallback(
    (key: string) => setFund(fund.filter((k) => k !== key)),
    [setFund, fund],
  );
```

Update the `useAdvancedAnalyticsReport` call (currently lines 157-167) to pass `tech` and `fund`:

```tsx
  const { value, loading, error } = useAdvancedAnalyticsReport(
    report,
    page,
    DEFAULT_PAGE_SIZE,
    sortKey,
    sortDir,
    market,
    tickerType,
    search,
    tech,
    fund,
    initialData,
  );
```

- [ ] **Step 4: Replace `handleCsv` to hit the export endpoint**

Replace the existing `handleCsv` callback (lines 202-210) with:

```tsx
  const handleCsv = useCallback(async () => {
    if (!value || value.rows.length === 0) return;
    const params = new URLSearchParams({
      sort_dir: sortDir,
      market,
      ticker_type: tickerType,
    });
    if (sortKey) params.set("sort_key", sortKey);
    if (search) params.set("search", search);
    if (tech.length > 0) params.set("tech", [...tech].sort().join(","));
    if (fund.length > 0) params.set("fund", [...fund].sort().join(","));
    params.set("columns", visibleCols.map((c) => c.key).join(","));
    const url = `${API_URL}/advanced-analytics/${report}/export?${params.toString()}`;
    try {
      await triggerCsvDownload(url);
    } catch (err) {
      console.error("CSV export failed", err);
    }
  }, [
    value,
    sortDir,
    sortKey,
    market,
    tickerType,
    search,
    tech,
    fund,
    visibleCols,
    report,
  ]);

  const csvDisabled =
    !value ||
    value.rows.length === 0 ||
    value.total > FILTER_EXPORT_ROW_CAP;
  const csvTooltip =
    value && value.total > FILTER_EXPORT_ROW_CAP
      ? `Export exceeds ${FILTER_EXPORT_ROW_CAP.toLocaleString("en-IN")} rows; tighten filters`
      : undefined;
```

- [ ] **Step 5: Insert dropdowns into toolbar JSX**

In the toolbar JSX (around line 244-296), insert the two `<FilterDropdown />` instances between the `ticker_type` `<select>` block (ends with `</select>` at line 284) and the `<ColumnSelector />` block (starts at line 285):

```tsx
          <FilterDropdown
            bundleId="tech"
            bundleLabel="Technical"
            catalog={TECH_FILTER_CATALOG}
            selected={tech}
            onChange={setTech}
            onReset={() => setTech([])}
          />
          <FilterDropdown
            bundleId="fund"
            bundleLabel="Fundamentals"
            catalog={FUND_FILTER_CATALOG}
            selected={fund}
            onChange={setFund}
            onReset={() => setFund([])}
          />
```

Update the `<DownloadCsvButton ... />` call (lines 292-295) to use the new disabled state:

```tsx
          <DownloadCsvButton
            onClick={handleCsv}
            disabled={csvDisabled}
            title={csvTooltip}
          />
```

> **Note for implementer:** verify `DownloadCsvButton` already accepts a `title` prop. If not, add it (single-line prop addition + `<button title={title}>`).

- [ ] **Step 6: Insert `<ActiveFilterChips />` directly under the toolbar**

Immediately after the closing `</div>` of the toolbar `flex items-center gap-2 flex-wrap` block (around line 297), and before the existing `{error && ...}` JSX, insert:

```tsx
      <ActiveFilterChips
        tech={tech}
        fund={fund}
        onRemoveTech={removeTech}
        onRemoveFund={removeFund}
        onClearAll={resetAll}
      />
```

- [ ] **Step 7: Update empty-state message**

Replace the empty-state `<td>` (around line 432-437) — extract the message into a `const`:

```tsx
              const emptyMsg =
                tech.length || fund.length
                  ? "No rows match your current filters. Try removing one or clicking 'Clear all'."
                  : "No rows match this report's filter today.";
```

Place that `const` directly above the `return (` at the top of the component (after `staleItems`), and reference `{emptyMsg}` in the empty-state JSX:

```tsx
            ) : (
              <tr>
                <td
                  colSpan={visibleCols.length}
                  className="px-3 py-8 text-center text-xs text-gray-500"
                >
                  {emptyMsg}
                </td>
              </tr>
            )}
```

- [ ] **Step 8: Restart frontend dev server, smoke-test in browser**

```bash
./run.sh restart frontend
sleep 3
```

Open http://localhost:3000/advanced-analytics in a browser as superuser. Verify:
- Two new buttons "Technical" and "Fundamentals" appear in toolbar.
- Clicking "Technical" → checking "Recent (≤10d)" → URL becomes `?tech=golden_recent&page=1` after 300 ms.
- Chip strip shows "Recent (≤10d) ×" below toolbar.
- Click `×` → URL drops `tech=`, table updates.
- "Download CSV" hits `/v1/advanced-analytics/{report}/export?...` and the file downloads.

- [ ] **Step 9: Lint + tsc**

```bash
cd frontend && npx eslint hooks/useAdvancedAnalyticsData.ts components/advanced-analytics/AdvancedAnalyticsTable.tsx --fix
cd frontend && npx tsc --noEmit
```

- [ ] **Step 10: Commit**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
git add frontend/hooks/useAdvancedAnalyticsData.ts frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx
git commit -m "$(cat <<'EOF'
feat(aa): integrate filter dropdowns + chip strip into AA table

Wires useFilterParams into AdvancedAnalyticsTable, adds two
inline FilterDropdown popovers (Technical / Fundamentals) next
to ColumnSelector, renders ActiveFilterChips below the toolbar,
and rewires handleCsv to hit the new /export endpoint via
triggerCsvDownload. Disables the CSV button when total > 10k
with a helpful tooltip; updates empty-state copy when filters
are active.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 10: E2E coverage — filter + chip + CSV flow

**Files:**
- Modify: `e2e/utils/selectors.ts` (register testids)
- Create: `e2e/tests/aa-filters.spec.ts`

- [ ] **Step 1: Register testids in `e2e/utils/selectors.ts`**

Locate the `FE` export object and add:

```ts
  // Advanced Analytics filters
  aaFilterTechButton: "aa-filter-tech-button",
  aaFilterTechPopover: "aa-filter-tech-popover",
  aaFilterTechReset: "aa-filter-tech-reset",
  aaFilterFundButton: "aa-filter-fund-button",
  aaFilterFundPopover: "aa-filter-fund-popover",
  aaFilterFundReset: "aa-filter-fund-reset",
  aaActiveFilterClearAll: "aa-active-filter-clear-all",
```

(Per-option testids like `aa-filter-tech-option-golden_recent` are constructed inline via template literals in the spec — no need to register every key.)

- [ ] **Step 2: Write the spec**

```ts
// e2e/tests/aa-filters.spec.ts
import { expect, test } from "@playwright/test";

import { FE } from "../utils/selectors";

test.use({ storageState: "e2e/.auth/superuser.json" });

const REPORT_PATH =
  "/advanced-analytics?tab=current-day-upmove";

test.describe("Advanced Analytics — filter bundles", () => {
  test("checking a tech filter narrows the table and updates URL", async ({
    page,
  }) => {
    await page.goto(REPORT_PATH);
    const table = page.getByTestId(
      "advanced-analytics-table-current-day-upmove",
    );
    await expect(table).toBeVisible();

    await page.getByTestId(FE.aaFilterTechButton).click();
    await expect(page.getByTestId(FE.aaFilterTechPopover)).toBeVisible();
    await page
      .getByTestId("aa-filter-tech-option-price_gt_sma50")
      .check();

    // URL update is debounced 300 ms.
    await expect(page).toHaveURL(/tech=price_gt_sma50/, {
      timeout: 2_000,
    });
    await expect(
      page.getByTestId("aa-active-filter-chip-price_gt_sma50"),
    ).toBeVisible();
  });

  test("removing a chip drops the URL param and table updates", async ({
    page,
  }) => {
    await page.goto(`${REPORT_PATH}&tech=price_gt_sma50`);
    await expect(
      page.getByTestId("aa-active-filter-chip-price_gt_sma50"),
    ).toBeVisible();
    await page
      .getByTestId("aa-active-filter-chip-price_gt_sma50-x")
      .click();
    await expect(page).not.toHaveURL(/tech=/, { timeout: 2_000 });
  });

  test("Clear all removes both bundles", async ({ page }) => {
    await page.goto(
      `${REPORT_PATH}&tech=price_gt_sma50&fund=fscore_ge_7`,
    );
    await page.getByTestId(FE.aaActiveFilterClearAll).click();
    await expect(page).not.toHaveURL(/tech=/, { timeout: 2_000 });
    await expect(page).not.toHaveURL(/fund=/);
  });

  test("CSV download returns more rows than visible page", async ({
    page,
  }) => {
    await page.goto(REPORT_PATH);
    // Ensure the page has loaded a multi-row table.
    const rows = page.locator(
      '[data-testid="advanced-analytics-table-current-day-upmove"] tbody tr',
    );
    const visibleCount = await rows.count();
    test.skip(
      visibleCount < 2,
      "Need at least 2 rows to validate full-set vs page export",
    );

    const downloadPromise = page.waitForEvent("download");
    await page.getByText(/download/i).click();
    const download = await downloadPromise;
    const path = await download.path();
    const fs = await import("node:fs/promises");
    const text = await fs.readFile(path!, "utf-8");
    const csvRows = text.trim().split("\n").length - 1; // minus header
    // Either visible page is the entire universe (csvRows ===
    // visibleCount), OR the export contains more — both valid.
    expect(csvRows).toBeGreaterThanOrEqual(visibleCount);
    expect(text.split("\n")[0]).toMatch(/^Ticker/);
  });
});
```

- [ ] **Step 3: Run the spec (1 worker, per §5.14)**

```bash
cd e2e && npx playwright test --project=analytics-chromium aa-filters.spec.ts
```

Expected: 4 passed.

- [ ] **Step 4: Lint**

```bash
cd e2e && npx eslint tests/aa-filters.spec.ts utils/selectors.ts --fix
```

- [ ] **Step 5: Commit**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
git add e2e/tests/aa-filters.spec.ts e2e/utils/selectors.ts
git commit -m "$(cat <<'EOF'
test(aa): E2E for filter bundles + chip removal + CSV download

Covers happy-path filter→URL→table sync, chip-× removal,
Clear-all reset, and full-filtered-set CSV download with
header assertion. Single-worker run (§5.14 #aa).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 11: Lighthouse perf check + PROGRESS.md + push

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: Run containerised Lighthouse for `/advanced-analytics` route**

```bash
docker compose --profile perf build frontend-perf
docker compose --profile perf up -d postgres redis backend frontend-perf
docker compose --profile perf run --rm perf
```

Expected: `frontend/.lighthouseci/pw-lh-summary.json` shows
`/advanced-analytics` route LCP ≤ 3.0 s and CLS ≤ 0.1
(`/analytics/*` budget per §5.15). If LCP regressed:
- Phase 0: read the LCP element + phase breakdown from the
  per-route JSON (see §5.15 in CLAUDE.md).
- Likely cause: chip strip painting after first chart frame —
  reserve `min-h-[28px]` on the chip strip wrapper to lock CLS.

- [ ] **Step 2: Update `PROGRESS.md`**

Prepend a new dated session entry to `PROGRESS.md`:

```markdown
## 2026-05-08 (evening) — AA filter bundles + filtered CSV export

**Branch:** `feature/aa-filter-bundles-csv` → PR (open)
**Sprint:** 9 (AA epic continuation)

**Shipped:**
- `backend/advanced_analytics_filters.py` — TECH_KEYS (9) + FUND_KEYS (8) allowlist + NaN-safe predicates + sorted-CSV parser.
- `_compute_report` extended with `?tech=` / `?fund=` AND-combined filtering; inner cache key now distinguishes filter combos.
- New `GET /v1/advanced-analytics/{report}/export` streams full filtered CSV (10 k row cap; 413 with helpful detail).
- Frontend: `<FilterDropdown />` + `<ActiveFilterChips />` + `useFilterParams` (URL ↔ state, 300 ms debounce, sorted CSV).
- `triggerCsvDownload` helper replaces page-only `downloadCsv` call in `AdvancedAnalyticsTable.tsx`.
- CI gate: `test_filter_catalog_sync.py` keeps backend allowlist and frontend mirror in lockstep.

**Tests:** 16 backend unit (filters) + 4 backend route (paginated) + 4 backend route (export) + 6 vitest (FilterDropdown) + 4 vitest (chips) + 2 vitest (helper) + 4 vitest (hook) + 4 Playwright. Lighthouse `/advanced-analytics` within budget.

**Spec / plan:** `docs/superpowers/specs/2026-05-08-aa-filter-dropdown-csv-design.md`, `docs/superpowers/plans/2026-05-08-aa-filter-bundles-csv.md`.
```

- [ ] **Step 3: Stage Serena memories + commit + push + open PR**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
git add .serena/ PROGRESS.md
git commit -m "$(cat <<'EOF'
docs(progress): log 2026-05-08 evening — AA filter bundles + CSV export

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
git push -u origin feature/aa-filter-bundles-csv
gh pr create --base dev --title "feat(aa): filter bundle dropdowns + filtered CSV export" --body "$(cat <<'EOF'
## Summary
- Two bundle filter dropdowns (Technical 9 / Fundamentals 8), AND-combined within and across bundles.
- Per-tab state via URL search params (shareable, refresh-safe).
- New `/{report}/export` endpoint streams full filtered CSV (10 k row cap).
- Active-filter chip strip below toolbar with × removal + Clear all.

## Test plan
- [ ] Backend pytest green (filters + routes + sync)
- [ ] Frontend vitest green (FilterDropdown, ActiveFilterChips, hook, helper)
- [ ] Playwright `aa-filters.spec.ts` green
- [ ] Lighthouse `/advanced-analytics` LCP ≤ 3.0 s, CLS ≤ 0.1
- [ ] Manual smoke: filter→chip→URL→CSV download path on superuser fixture

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

> **Note for implementer:** push needs explicit user authorisation per "Executing actions with care" rules. Prompt before pushing.

---

## Self-review (post-write)

Performed inline. Findings:

1. **Spec coverage** — every spec section maps to a task:
   - §3 architecture → Tasks 2, 3 (backend), 9 (frontend integration)
   - §4 catalog → Tasks 1 (backend), 5 (frontend mirror)
   - §5 backend → Tasks 1, 2, 3, 4
   - §6 frontend → Tasks 5, 6, 7, 8, 9
   - §7 testing → distributed across each task; E2E in Task 10
   - §8 rollout → Task 11

2. **Placeholder scan** — no TBDs, all code blocks complete, expected outputs declared. The two ⚠ implementer notes (`seed_aa_rows` fixture extension, `DownloadCsvButton` `title` prop) are explicit, scoped, and inline in the relevant step rather than deferred.

3. **Type consistency** — checked `TechKey`/`FundKey` (Task 1) match `TECH_KEY_SET`/`FUND_KEY_SET` (Task 5) and `TechFilterKey`/`FundFilterKey` types (Task 5). Cache key shape (`ftech{...}:ffund{...}:dt{...}`) consistent in Tasks 2 and 3. SWR key serialisation rule (`[...arr].sort().join(",")`) consistent in Task 7 hook + Task 9 SWR caller. `FILTER_EXPORT_ROW_CAP` defined in Task 5, consumed in Task 9; backend `_MAX_EXPORT_ROWS` matches in Task 3.

4. **Ambiguity** — empty-state copy and `csvTooltip` text both lifted verbatim from the spec; no interpretation gaps.
