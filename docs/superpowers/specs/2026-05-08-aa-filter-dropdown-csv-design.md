# Advanced Analytics — Filter Dropdowns + Filtered CSV Export

**Date:** 2026-05-08
**Author:** Abhay Kumar Singh
**Status:** Draft (awaiting user approval)
**Sprint:** 9 (Advanced Analytics epic continuation)
**Predecessors:**
- PR #135 — Sprint 9 AA epic + AA-Followup items 1 & 5
- PR #138 — RSI/SMA on-demand compute + golden cross feature
- §5.4 tabular-page-pattern (CLAUDE.md)
- §5.5 stale-data transparency chip
- §5.13 Redis caching strategy

---

## 1. Problem

The 7 Advanced Analytics tabs render rich superset rows (50 fields:
indicators, price/volume/delivery, fundamentals, promoter holdings,
golden-cross flags). Today the toolbar exposes only `search` ·
`market` · `ticker_type` · `ColumnSelector` · `DownloadCsvButton`.

Users cannot:
1. **Narrow the universe by analytical criteria** — e.g. "show me
   only stocks where SMA 50 > SMA 200 AND F-Score ≥ 7 AND
   Pledged < 5%". Today they must eyeball-scan or sort one column
   at a time.
2. **Export the narrowed list** — the CSV button only ships
   `value.rows` (≤25 visible). For a 1500-row universe filtered to
   80 matches, only the first 25 ever leave the browser.

This spec adds **two parallel filter dropdowns** ("Technical" +
"Fundamentals") with **AND-combined predicates** and a **full-set
CSV export endpoint** that returns every matching row.

---

## 2. Goals & Non-Goals

### Goals

- Two filter bundles — **9 technical predicates** (incl. a 3-way
  RSI radio: oversold / neutral / overbought) and **8 fundamentals
  predicates** (incl. a 2-way F-Score radio) — each exposed as an
  inline popover next to `ColumnSelector`.
- AND semantics within and across bundles. Mutually-exclusive
  predicates (RSI bands, F-Score directionality) rendered as radio
  groups so the UI cannot generate empty-by-design selections.
- Per-tab state (each report remembers its own filter selection) via
  URL search params (`?tech=…&fund=…`) — shareable, refresh-safe,
  SSR-coordinated.
- CSV button exports the **full filtered set** (≤10 000 hard cap)
  via a new `/{report}/export` endpoint, not just the visible page.
- Active filters surfaced as removable chips below the toolbar so
  the current screen is self-describing.

### Non-Goals

- No per-tab filter catalog tuning — all 7 tabs share the same 16
  filters (the row model is already a superset). Per-tab curation
  can come later if usage data shows certain filters are noise on
  certain tabs.
- No fundamentals/technicals UNION — strictly AND. Users wanting OR
  semantics can run two separate exports and concat externally.
- No backend column projection — `columns=` only re-orders the CSV
  output; backend still computes the full superset row (already
  cached at the outer layer, no compute saved by projecting).
- No new sentiment/forecast/recommendation filters in this slice —
  those fields aren't on `AdvancedRow` today and dragging them in
  bloats the change. Possible follow-up.

---

## 3. Architecture

```
URL search params (per-route, per-tab)
        │
        ▼
useAdvancedAnalyticsReport(report, …, tech, fund)  ── SWR key includes tech+fund
        │
        ▼
GET /v1/advanced-analytics/{report}?tech=…&fund=…&page=…&sort=…
        │
        ▼
_compute_report() ──► outer cache (full rows, 24h, unchanged)
                  ──► market/ticker_type/search filter (existing)
                  ──► **NEW: tech/fund filter (in-memory)**
                  ──► report-specific _passes_filter (existing)
                  ──► sort + paginate ──► 200 OK
        │
        ▼
GET /v1/advanced-analytics/{report}/export?tech=…&fund=…&columns=…
        │
        ▼
Same pipeline minus pagination ──► streamed CSV (rows ≤ 10 000 cap)
```

**Ownership boundary**

| Layer | Responsibility |
|---|---|
| Backend | Filter parsing, validation against allowlist, predicate matching, full-set CSV streaming, single source of truth for the wire vocabulary. |
| Frontend | Popover UI, radio/checkbox state, active-filter chips, URL marshalling, SWR cache key, CSV download trigger. |

**Cache key extension** — inner cache key in `_compute_report`:
```
cache:advanced_analytics:{report}:{user_id}:m{market}:t{ticker_type}:q{needle}
  :ftech{tech_csv_sorted}:ffund{fund_csv_sorted}
  :dt{as_of}:p{page}:s{sort}:{dir}:ps{page_size}
```
Outer cache (`_cached_full_rows(user, as_of)`) is **unchanged** —
filtering is a cheap in-memory predicate pass over a list that's
already in the process. Inner-cache footprint grows by the cardinality
of distinct filter combos used, but each entry is small (≤25 rows
serialised).

---

## 4. Filter Catalog

Single allowlist module: `backend/advanced_analytics_filters.py`
(canonical Python literal for `TECH_KEYS`, `FUND_KEYS`, and
predicate functions). Frontend mirror in
`frontend/components/advanced-analytics/filterCatalogs.ts`
(hardcoded TS literal of the same keys + UI labels). A
**CI sync test** (`backend/tests/test_filter_catalog_sync.py`)
loads the TS file as text, regex-extracts the key strings, and
asserts equality with `TECH_KEYS ∪ FUND_KEYS`. Either side adding
or removing a key without the other fails CI before merge.

### 4.1 Technical bundle (8 predicates, 1 radio group)

| Key | Predicate | Source field | UI control |
|---|---|---|---|
| `golden_recent` | `0 ≤ golden_cross_days_ago ≤ 10` | `golden_cross_days_ago` | checkbox |
| `golden_established` | `golden_cross_days_ago > 10` | `golden_cross_days_ago` | checkbox |
| `price_gt_sma50` | `today_ltp > sma_50` | `today_ltp`, `sma_50` | checkbox |
| `price_gt_sma200` | `today_ltp > sma_200` | `today_ltp`, `sma_200` | checkbox |
| `rsi_oversold` | `rsi < 30` | `rsi` | radio (group: `rsi_band`) |
| `rsi_neutral` | `30 ≤ rsi ≤ 70` | `rsi` | radio (group: `rsi_band`) |
| `rsi_overbought` | `rsi > 70` | `rsi` | radio (group: `rsi_band`) |
| `vol_surge` | `today_x_vol ≥ 2` | `today_x_vol` | checkbox |
| `near_52w_high` | `away_from_52week_high ≥ -5` | `away_from_52week_high` | checkbox |

### 4.2 Fundamentals bundle (8 predicates, 1 radio group)

| Key | Predicate | Source field | UI control |
|---|---|---|---|
| `fscore_ge_7` | `pscore ≥ 7` | `pscore` | radio (group: `fscore_band`) |
| `fscore_le_3` | `pscore ≤ 3` | `pscore` | radio (group: `fscore_band`) |
| `debt_lt_0_5` | `debt_to_eq < 0.5` | `debt_to_eq` | checkbox |
| `roce_gt_20` | `roce > 20` | `roce` | checkbox |
| `sales_3y_gt_15` | `sales_growth_3yrs > 15` | `sales_growth_3yrs` | checkbox |
| `profit_3y_gt_15` | `prft_growth_3yrs > 15` | `prft_growth_3yrs` | checkbox |
| `prom_hld_gt_50` | `prom_hld > 50` | `prom_hld` | checkbox |
| `pledged_lt_5` | `pledged < 5` | `pledged` | checkbox |

### 4.3 NaN policy

A row missing the source field for any selected predicate is
**excluded** from the result. Predicates are positive assertions
("show me stocks WITH this property"); silently passing NaN rows
violates user expectation. This avoids the `NaN > 0 == False` /
`val or default` footguns in §6.1 — predicate functions guard with
`pd.notna()` / `math.isnan` before comparison.

The existing stale-ticker chip already tells users *why* upstream
data is missing for a ticker — no new transparency surface needed.

---

## 5. Backend Changes

### 5.1 New module — `backend/advanced_analytics_filters.py`

```python
"""Filter catalog + predicates for Advanced Analytics bundles.

Single source of truth for the technical + fundamentals filter
allowlist used by the /v1/advanced-analytics/{report} and
/{report}/export endpoints. Mirrored by
frontend/components/advanced-analytics/filterCatalogs.ts;
sync verified by tests.
"""
from __future__ import annotations

import math
from typing import Callable, Literal

from backend.advanced_analytics_models import AdvancedRow

TechKey = Literal[
    "golden_recent", "golden_established",
    "price_gt_sma50", "price_gt_sma200",
    "rsi_oversold", "rsi_neutral", "rsi_overbought",
    "vol_surge", "near_52w_high",
]
FundKey = Literal[
    "fscore_ge_7", "fscore_le_3",
    "debt_lt_0_5", "roce_gt_20",
    "sales_3y_gt_15", "profit_3y_gt_15",
    "prom_hld_gt_50", "pledged_lt_5",
]

# Each predicate returns True iff the row matches; NaN/None → False.
# Concrete predicate bodies are 1-liners derived from the catalog
# table in §4 — e.g. ``"price_gt_sma50": lambda r:
# _safe_gt(r.today_ltp, r.sma_50)``. ``_safe_gt`` rejects NaN/None
# operands per §4.3.
TECH_PREDICATES: dict[str, Callable[[AdvancedRow], bool]] = {...}
FUND_PREDICATES: dict[str, Callable[[AdvancedRow], bool]] = {...}

TECH_KEYS: frozenset[str] = frozenset(TECH_PREDICATES)
FUND_KEYS: frozenset[str] = frozenset(FUND_PREDICATES)

def parse_filter_csv(
    raw: str, allowed: frozenset[str], bundle: str,
) -> list[str]:
    """Split, dedupe, validate. Raise HTTPException(400) on unknown keys."""

def passes_bundle_filters(
    row: AdvancedRow, tech: list[str], fund: list[str],
) -> bool:
    """AND across both bundles; predicate-NaN → reject."""
```

Helper used by both the paginated read and the export.

### 5.2 Endpoint signature changes

`backend/advanced_analytics_routes.py`:

```python
# Existing _make_endpoint() handler — add 2 params:
tech: str = Query("", max_length=200, pattern="^[a-z0-9_,]*$"),
fund: str = Query("", max_length=200, pattern="^[a-z0-9_,]*$"),

# Pass through to _compute_report which:
#   1. parses + validates tech/fund via parse_filter_csv()
#   2. inserts passes_bundle_filters() between
#      the search-filter step and _passes_filter(report)
#   3. extends inner cache key with sorted CSVs
```

### 5.3 New endpoint — `/{report}/export`

```python
async def _export_handler(
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
    ...
```

- Reuses `_cached_full_rows()` → market/ticker_type/search →
  `passes_bundle_filters` → `_passes_filter(report)` → sort.
  **Skips pagination.**
- Validates `columns` against `ALL_VALID_KEYS` (column-selector
  allowlist). If empty, defaults to the report's `defaults` list.
- Hard cap: if filtered count > 10 000 → `HTTPException(413,
  "Export exceeds 10 000 rows; tighten filters")`.
- Streams `text/csv` via `StreamingResponse(generator)` — header
  row first, then rows; no full-buffer materialisation.
- `Content-Disposition: attachment;
  filename="advanced-analytics-{report}-{YYYYMMDD}.csv"`.
- Cache: same key shape with `:export` suffix, no page params,
  TTL = `TTL_STABLE` (300s). Hit returns the buffered CSV string.

### 5.4 Cache invalidation

No new entries in `_CACHE_INVALIDATION_MAP` — bundle filter cache
keys live under the existing `cache:advanced_analytics:*` prefix
which is already invalidated when any of the underlying Iceberg
tables (ohlcv, fundamentals_snapshot, etc.) writes through
`_retry_commit()`.

---

## 6. Frontend Changes

### 6.1 New component — `<FilterDropdown />`

`frontend/components/advanced-analytics/FilterDropdown.tsx`:

```tsx
interface FilterOption {
  key: string;          // wire key
  label: string;        // UI text
  group?: string;       // radio group id (undefined = checkbox)
  tooltip?: string;
}
interface FilterDropdownProps {
  bundleId: "tech" | "fund";
  bundleLabel: string;
  catalog: FilterOption[];
  selected: string[];   // ordered, deduped, allowlist-validated
  onChange: (next: string[]) => void;
  onReset: () => void;
}
```

- Trigger button: `[bundleLabel ⌄]` with active-count badge when
  `selected.length > 0`.
- Popover content groups options by `group` field (radio group
  rendered as `role="radiogroup"`); ungrouped options are checkboxes.
- "Reset" link clears just this bundle.
- Reuses Radix `<Popover>` patterns from existing `ColumnSelector`.

### 6.2 New hook — `useFilterParams`

`frontend/hooks/useFilterParams.ts`:

```ts
function useFilterParams(report: AdvancedReportName): {
  tech: string[];
  fund: string[];
  setTech: (next: string[]) => void;
  setFund: (next: string[]) => void;
  resetAll: () => void;
}
```

- Reads URL via Next 16 `useSearchParams` (RSC-safe).
- Writes via `router.replace()` (`scroll: false`), debounced 300ms
  to absorb checkbox spam.
- Each setter resets `?page=1` at the call-site (matches existing
  market/ticker_type setter pattern, lines 132-142 of
  `AdvancedAnalyticsTable.tsx`).
- Validates incoming URL tokens against the catalog; unknown keys
  silently dropped (don't break the page on stale shared links).

### 6.3 Toolbar layout — `AdvancedAnalyticsTable.tsx`

```
[search] [market ⌄] [ticker_type ⌄] [Tech ⌄] [Fund ⌄] [Cols ⌄] [⬇ CSV]
```

Insert the two `<FilterDropdown />` instances between
`ticker_type` select and `<ColumnSelector />`. Existing
`flex-wrap` already handles mobile reflow.

### 6.4 Active-filter chip strip

Render directly under the toolbar when
`tech.length + fund.length > 0`:

```
Active: [✦ Recent ×] [Price > SMA 50 ×] [RSI Neutral ×] [F-Score ≥ 7 ×]   Clear all
```

- Each chip click on `×` removes that key from its bundle.
- "Clear all" calls `resetAll()` from `useFilterParams`.
- Reuses `<StaleTickerChip>` styling primitives (amber/green/neutral).

### 6.5 Empty-state messaging

```ts
const emptyMsg =
  tech.length || fund.length
    ? "No rows match your current filters. Try removing one or clicking 'Clear all'."
    : "No rows match this report's filter today.";
```

### 6.6 SWR key extension

`useAdvancedAnalyticsReport` signature gains two params:
`(report, page, page_size, sort_key, sort_dir, market, ticker_type, search, tech, fund, initialData)`.
SWR key serialises bundles as **sorted CSV** so equivalent combos
(`tech=a,b` ≡ `tech=b,a`) hit the same cache slot.

### 6.7 CSV download — `triggerCsvDownload` helper

`frontend/lib/triggerCsvDownload.ts` (~25 LOC):

```ts
export async function triggerCsvDownload(url: string): Promise<void> {
  const res = await apiFetch(url);  // auto-refreshes JWT
  if (!res.ok) throw new Error(`CSV export failed: ${res.status}`);
  const blob = await res.blob();
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = (
    res.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1]
    ?? "export.csv"
  );
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objUrl);
}
```

`handleCsv` in `AdvancedAnalyticsTable.tsx` is rewritten to build
the export URL with all current filters + visible column order, then
calls `triggerCsvDownload`. The local `downloadCsv()` import is
removed from this file (the helper stays in the codebase for other
pages — Insights still uses it).

### 6.8 CSV button disabled-state

```ts
const csvDisabled =
  !value || value.rows.length === 0 || value.total > 10_000;
const csvTooltip =
  value && value.total > 10_000
    ? "Export exceeds 10 000 rows; tighten filters"
    : undefined;
```

`value.total` is already in the paginated response — no extra
round-trip to know whether the export will succeed.

### 6.9 Test IDs (per §5.14)

```
data-testid="aa-filter-{bundleId}-button"        // tech | fund
data-testid="aa-filter-{bundleId}-popover"
data-testid="aa-filter-{bundleId}-option-{key}"
data-testid="aa-filter-{bundleId}-reset"
data-testid="aa-active-filter-chip-{key}"
data-testid="aa-active-filter-clear-all"
```

Registered in `e2e/utils/selectors.ts` `FE` object.

---

## 7. Testing

### 7.1 Backend (pytest)

`backend/tests/test_advanced_analytics_filters.py`:
- For each of the 17 filter keys: build an `AdvancedRow` fixture
  with edge values (NaN, exact-boundary `rsi=30.0`, `pscore=7`,
  `golden_cross_days_ago=10` and `=11`), assert
  `passes_bundle_filters(row, [key], [])` matches expected.
- `parse_filter_csv("golden_recent,unknown", TECH_KEYS, "tech")` →
  `HTTPException(400, detail="Unknown technical filter: unknown")`.
- Allowlist sync test: load the JSON catalog snapshot used by the
  frontend and assert every key is in `TECH_KEYS ∪ FUND_KEYS` and
  vice-versa. Fails CI if either side adds a key without the other.

`backend/tests/test_advanced_analytics_routes.py` (additions):
- `GET /current-day-upmove?tech=golden_recent&fund=fscore_ge_7`
  with seeded data → response only contains rows where both
  predicates hold.
- `GET /current-day-upmove?tech=unknown_key` → 400.
- `GET /current-day-upmove/export?tech=…` → 200, `Content-Type:
  text/csv`, header row matches `columns=`, row count > 25.
- Export with synthetic 10 001 matching rows → 413.

### 7.2 Frontend (vitest)

- `FilterDropdown.test.tsx` — checkbox toggle, radio mutual-exclusion,
  reset, badge count.
- `useFilterParams.test.ts` — URL→state hydration on mount,
  state→URL debounced write, sorted-CSV serialisation, unknown-key
  drop on hydrate.

### 7.3 E2E (Playwright)

`e2e/tests/aa-filters.spec.ts`:
- Superuser fixture → /advanced-analytics/current-day-upmove.
- Click `aa-filter-tech-button` → check `golden_recent` option →
  assert URL contains `?tech=golden_recent` → assert ≥1 table row.
- Click chip `×` → URL param drops → table updates.
- Click CSV button → assert downloaded file row count > visible
  page size, header row contains expected columns.

### 7.4 Manual smoke

- Mobile (DevTools 375px): toolbar wraps cleanly to 2-3 rows.
- 5-min Redis TTL: re-fire same filter combo within 5 min →
  observe cache hit in backend logs.
- Share-link test: copy URL with active filters from Chrome →
  paste in Firefox → state hydrates correctly.

---

## 8. Rollout

1. Branch off `dev`: `feature/aa-filter-bundles-csv`.
2. Backend filter module + endpoint changes + tests (PR 1).
3. Frontend `<FilterDropdown />` + `useFilterParams` + toolbar
   integration + chip strip + CSV trigger rewrite + tests (PR 2,
   blocks on PR 1).
4. E2E coverage update (PR 3, can land same time as PR 2).
5. Squash-merge each PR to `dev` (per §4.4 #26).
6. Run containerised Lighthouse on `/advanced-analytics` route —
   verify the extra toolbar controls + chip strip don't push LCP
   above the 3.0s budget for `/analytics/*` (§5.15).
7. Promote dev → qa → release → main when sprint closes.

---

## 9. Open Questions

None at design time. All addressed in brainstorm:

| Topic | Resolution |
|---|---|
| Filter scope | Two bundles (Tech 8 + Fund 8), AND-combined |
| Per-tab vs global | Per-tab |
| Persistence | URL search params |
| CSV scope | Full filtered set via backend |
| Mutually-exclusive predicates | Radio groups (RSI band, F-Score band) |
| NaN handling | Exclude row from result; rely on existing stale chip |
| Export size cap | 10 000 rows; 413 with helpful detail |

---

## 10. Risk & Mitigation

| Risk | Likelihood | Mitigation |
|---|---|---|
| Frontend ↔ backend allowlist drift | M | Sync test in CI; shared JSON catalog |
| Cache key bloat from many filter combos | L | Inner cache TTL is 300s; entries small (~5KB) |
| 10k cap surprises power users | L | Disabled-button tooltip + 413 detail message both name the cap |
| `useSearchParams` SSR fallback hole (§5.3) | L | The page is already wrapped in `<Suspense>` per Sprint 9; new hook reuses pattern |
| Mobile toolbar overflow | L | `flex-wrap` already in place; manual smoke at 375px |
