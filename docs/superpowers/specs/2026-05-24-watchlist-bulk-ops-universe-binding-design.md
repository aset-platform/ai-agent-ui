# Watchlist Bulk Ops + Universe Binding — Design

**Status:** Draft → review
**Date:** 2026-05-24
**Owner:** Abhay Kumar Singh (asequitytrading@gmail.com)
**Epic:** C (closes out the A → B → C arc: budget reservation,
portfolio dashboard tab, watchlist bulk ops + universe binding)
**Branch:** stacked on `feature/algo-portfolio-tab` (Epic B, PR #243)

---

## 1. Goal

Two-part epic:

1. **Watchlist bulk ops** — let users upload a CSV of tickers to
   bulk-add their watchlist, and offer a confirmed "remove all"
   destructive action. Today the watchlist supports only
   single-ticker add (`POST /tickers`) and single-ticker remove
   (`DELETE /tickers/{ticker}`); large lists are tedious to seed.

2. **Universe binding extension** — when a strategy uses
   `universe.scope=watchlist`, extend the iterated ticker set to
   `watchlist ∪ portfolio_holdings ∪ algo_open_positions`. Today
   the binding stops at `watchlist ∪ portfolio_holdings`. If an
   algo opens a Kite position on a ticker the user never added to
   their watchlist, the strategy can never iterate to it on the
   next bar — meaning it can't fire an exit signal. That's a
   silent footgun.

## 2. Background

Watchlist storage lives in PG `auth.tickers` with the existing
repository pattern at `auth/repo/repository.py`. The dashboard
exposes one tab (`watchlist`) inside the `WatchlistWidget` shell
that already hosts the Portfolio + Algo tabs.

`_scoped_tickers(user, scope)` at `backend/insights_routes.py:173`
is the single source of truth for "which tickers does the user
see on a tab?" — used by 8+ insights endpoints and by the algo
runtime (`backend/algo/backtest/universe.py::resolve_universe`).

`algo.live_caps.allowed_tickers` is independent. It's the
trade-time Cap 2 in `pre_trade_check` (`backend/algo/live/safety
.py:194`) — a user-managed whitelist that rejects orders on
tickers not in the allowlist. This epic does NOT touch it.

After Epic B shipped (`/v1/algo/portfolio/positions` joins
`algo.events` attribution to surface Kite-held positions on the
dashboard), the gap between "algo holds X" and "strategy can
iterate X" became visible.

## 3. Decisions matrix

| # | Question | Decision |
|---|---|---|
| 1 | Universe extension semantics | **Extend** `_scoped_tickers_for_strategy(scope=watchlist)` to `watchlist ∪ portfolio_holdings ∪ algo_open_positions`. No per-strategy override. |
| 2 | Bulk-add input format | **CSV file upload** (multipart/form-data). Single `ticker` column required, optional `source` column. |
| 3 | Validation policy | **Skip invalid rows; per-row diagnostic** — `added[]`, `skipped_already_linked[]`, `errors[]` with row index + reason. |
| 4 | Remove-all confirmation | **Typed phrase** — user types literal `"REMOVE ALL"` to enable the destructive button. |
| 5 | UI placement | **⋮ overflow menu** in the existing `WatchlistWidget` next to the `+` add button. Only on the `watchlist` tab. |
| 6 | Backend endpoint shape | **New `POST /v1/tickers/bulk`** (multipart), **new `DELETE /v1/tickers/all`** (JSON confirmation body). Existing single-ticker endpoints stay. |
| 7 | Universe extension scope | **Algo runtime only** — `resolve_universe` switches to the new helper. Insights tabs keep calling `_scoped_tickers`. |
| 8 | Algo positions source | **`algo.events` Iceberg** — BUY fills minus matching SELLs, net qty > 0. No Kite call. |

## 4. Out of scope (v1)

1. CSV bulk-remove (uploading a CSV of tickers to unlink).
2. Bulk-add via text-area paste (rejected in favor of CSV-only).
3. Bulk-add of portfolio holdings with quantity / price — separate future epic.
4. Multi-select rows + per-ticker bulk delete from a table view.
5. Dedicated `/watchlist` full-page editor.
6. CSV preview / dry-run endpoint before commit.
7. Async bulk import with job-id + progress polling.
8. Universe extension to insights tabs (Risk, Sectors, Dividends, Piotroski, Targets).
9. Consolidating `open_algo_positions` with Epic B's `_get_algo_positions_impl`.
10. Replacing the existing single-ticker endpoints — they stay; bulk is additive.
11. CSV > 5,000 rows (HTTP 413; split and retry).
12. Per-strategy override of the universe extension (`universe.include_algo_open`).
13. Live Kite fallback when `algo.events` is slow / empty — accepted lag.

## 5. Architecture

```
┌────────────────────────────────────────────────────────────┐
│ Dashboard — WatchlistWidget (existing)                     │
│   ⋮ overflow menu (NEW) next to the + button               │
│   ├─ "Bulk add tickers…" → BulkAddTickersModal             │
│   └─ "Remove all…"        → RemoveAllTickersModal          │
└────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┴────────────────────┐
        ▼                                        ▼
┌────────────────────────┐         ┌────────────────────────────┐
│ POST /v1/tickers/bulk  │         │ DELETE /v1/tickers/all     │
│ multipart/form-data    │         │ JSON: {"confirm":"…"}      │
│   "file": <csv>        │         │ → repo.unlink_all(user_id) │
│                        │         └────────────────────────────┘
│ Per-row report:        │
│   added[], skipped[],  │
│   errors[]             │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────────────────────────────────────────┐
│ auth.repository.bulk_link_tickers(user_id, tickers)        │
│   - Normalize: upper, trim, drop empties                   │
│   - Validate each via validate_ticker()                    │
│   - Bulk INSERT … ON CONFLICT (user_id, ticker) DO NOTHING │
│   - Return (added, already_linked)                         │
└────────────────────────────────────────────────────────────┘

────────────────────────────────────────────────────────────────

ALGO STRATEGY UNIVERSE BINDING
(resolve_universe() in backend/algo/backtest/universe.py)
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│ _scoped_tickers_for_strategy(user, scope)   [NEW]          │
│   if scope == "watchlist":                                 │
│     return _dedup(                                         │
│       _scoped_tickers(user, "watchlist"),                  │
│       open_algo_positions(user.user_id),                   │
│     )                                                      │
│   else:                                                    │
│     return _scoped_tickers(user, scope)                    │
└──────────┬─────────────────────────────────────────────────┘
           │
           ▼
┌────────────────────────────────────────────────────────────┐
│ open_algo_positions(user_id) → set[str]    [NEW]           │
│   backend/algo/live/open_positions.py                      │
│                                                            │
│   SELECT ts_ns, payload_json                               │
│   FROM algo.events                                         │
│   WHERE user_id = ? AND mode = 'live'                      │
│       AND type = 'order_filled_live'                       │
│       AND ts_date >= '2024-01-01'                          │
│   ORDER BY ts_ns ASC                                       │
│                                                            │
│   Python: net qty by symbol (BUY +qty, SELL -qty);         │
│   ignore dry_run fills;                                    │
│   return {sym for sym, qty in net.items() if qty > 0}      │
│                                                            │
│   Redis cache: cache:algo:open_positions:{user_id}         │
│                TTL 60s, no write-through invalidation      │
│   Fail-open: empty set on any read failure                 │
└────────────────────────────────────────────────────────────┘
```

**Reused parts:**
- `validate_ticker()` from `auth/endpoints/ticker_routes.py` — existing single-ticker validator
- `_scoped_tickers()` from `backend/insights_routes.py` — unchanged; the new helper wraps it
- `query_iceberg_table` (used by Epic B's `_fetch_strategy_attribution`)
- Existing `WatchlistWidget` shell — only adds the ⋮ menu + two new modals

**New parts (backend):**
- `auth/repo/repository.bulk_link_tickers` + `repository.unlink_all_tickers`
- `auth/endpoints/ticker_routes.py:bulk_link_tickers` (POST /v1/tickers/bulk, multipart) + `unlink_all` (DELETE /v1/tickers/all)
- `backend/algo/live/open_positions.py` — `open_algo_positions(user_id)` + Redis cache
- `backend/insights_routes._scoped_tickers_for_strategy` — sibling helper

**New parts (frontend):**
- `frontend/components/widgets/BulkAddTickersModal.tsx`
- `frontend/components/widgets/RemoveAllTickersModal.tsx`
- `frontend/components/widgets/WatchlistOverflowMenu.tsx`
- `WatchlistWidget` patch: render the menu next to the `+` button when `activeTab === "watchlist"`

**Single touchpoint to `resolve_universe`:** swap the
`_scoped_tickers` call for `_scoped_tickers_for_strategy`. The
8+ insights-tab callers stay untouched.

## 6. Data contract

### `POST /v1/tickers/bulk`

Request — `multipart/form-data` with one form field:
- `file`: a CSV file

CSV format:
- One required column: `ticker` (header literal, case-insensitive)
- Optional column: `source` (free text, defaults to `"bulk_csv"`)
- UTF-8, comma-separated, headers in row 1
- Whitespace + blank rows tolerated and ignored
- Hard size cap: 5 000 rows (HTTP 413 if exceeded)

Response (`BulkTickerResponse`, Pydantic):

```python
class BulkTickerErrorRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int               # 1-indexed CSV row number
                           # (header = row 1; data starts at 2)
    ticker: str            # whatever was in the row
    reason: str            # "invalid format" | "empty ticker"
                           # | "duplicate in batch" | etc.


class BulkTickerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    added: list[str]
    skipped_already_linked: list[str]
    errors: list[BulkTickerErrorRow]
    total_rows: int        # = added + skipped + errors
```

Status codes:
- `200` on any partial success (even all-errors — the response
  body carries the per-row diagnostic)
- `400` when the CSV cannot be parsed at all (missing `ticker`
  column, malformed CSV, encoding failure)
- `413` when row count > 5 000
- `422` on missing `file` form field or wrong content-type

### `DELETE /v1/tickers/all`

Request — JSON body:

```json
{ "confirm": "REMOVE ALL" }
```

The `confirm` field MUST equal the literal string `"REMOVE ALL"`
(case-sensitive). Backend gate:

```python
if body.confirm != "REMOVE ALL":
    raise HTTPException(400, "confirmation phrase mismatch")
```

Response:

```python
class UnlinkAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    removed: int            # count of rows deleted
```

### `auth.repository` additions

```python
async def bulk_link_tickers(
    self,
    user_id: UUID,
    tickers: list[str],
    source: str = "bulk_csv",
) -> tuple[list[str], list[str]]:
    """Returns (added, already_linked).

    Single round-trip: INSERT INTO auth.tickers (...)
    VALUES (...), (...), ... ON CONFLICT (user_id, ticker)
    DO NOTHING RETURNING ticker; the returned set is `added`;
    `already_linked = input_set - added_set`.

    Tickers MUST be pre-validated + pre-normalised by the
    caller (upper-case, trimmed).
    """


async def unlink_all_tickers(self, user_id: UUID) -> int:
    """DELETE FROM auth.tickers WHERE user_id = ?;
    returns the row count.
    """
```

### `open_algo_positions(user_id) -> set[str]`

```python
async def open_algo_positions(user_id: UUID) -> set[str]:
    """Tickers with net long qty > 0 across all live algo
    fills since 2024-01-01.

    Reads from algo.events
    (mode='live', type='order_filled_live').
    Net qty per symbol = sum(qty if side=BUY else -qty);
    ignores payload.dry_run = True rows.
    Returns symbols with net > 0.

    Cached in Redis at cache:algo:open_positions:{user_id}
    with 60s TTL; returns empty set on any read failure
    (fail-open — universe simply doesn't include algo-held
    tickers in that case).
    """
```

### `_scoped_tickers_for_strategy(user, scope) -> list[str]`

```python
async def _scoped_tickers_for_strategy(
    user: UserContext,
    scope: TickerScope,
) -> list[str]:
    """Like ``_scoped_tickers`` but injects algo-held
    positions into the ``watchlist`` scope so a strategy
    with ``universe.scope=watchlist`` can always iterate
    over (and exit) positions it currently holds.

    Other scopes delegate verbatim.
    """
    base = await _scoped_tickers(user, scope)
    if scope != "watchlist":
        return base
    from backend.algo.live.open_positions import (
        open_algo_positions,
    )
    algo_held = await open_algo_positions(user.user_id)
    return _dedup(base, sorted(algo_held))
```

The only caller switching from `_scoped_tickers` →
`_scoped_tickers_for_strategy` is `resolve_universe` in
`backend/algo/backtest/universe.py:185`.

## 7. UI surface

### Overflow menu

Rendered only when `activeTab === "watchlist"`. Position: right
next to the existing `+` add button. Click-outside + Escape
close the menu. Z-index `z-[60]` per CLAUDE.md §5.6 (slideover).

```
┌──────────────────────────────────────────────────────────┐
│ [Portfolio]  [Watchlist]  [Algo]            +  ⋮  3 tickers│
│                ─────────                        │         │
│                                                 ▼         │
│                                  ┌──────────────────────┐│
│                                  │ Bulk add tickers…    ││
│                                  │ Remove all…   (rose) ││
│                                  └──────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

testids:
- `dashboard-watchlist-overflow-button`
- `dashboard-watchlist-overflow-menu`
- `dashboard-watchlist-bulk-add-item`
- `dashboard-watchlist-remove-all-item`

### `BulkAddTickersModal`

```
┌─────────────────────────────────────────────┐
│ Bulk add tickers from CSV                ✕  │
│                                             │
│ Format: CSV with a `ticker` column.         │
│ Up to 5,000 rows.                           │
│                                             │
│ ┌─────────────────────────────────────────┐ │
│ │  📄  Drop .csv file here, or click to   │ │
│ │      browse                             │ │
│ └─────────────────────────────────────────┘ │
│ Selected: investments_2025.csv (1,247 rows) │
│                                             │
│                       [Cancel]  [Upload]    │
└─────────────────────────────────────────────┘
```

After upload, the modal switches to a result view:

```
┌─────────────────────────────────────────────┐
│ Bulk add complete                        ✕  │
│                                             │
│  ✓ 1,205 tickers added                      │
│  ⊝   38 already in your watchlist           │
│  ✗    4 errors                              │
│       ┌────────────────────────────────┐    │
│       │ Row 87  · BADTKR$ · invalid    │    │
│       │ Row 134 · ""      · empty      │    │
│       │ Row 891 · 1234    · invalid    │    │
│       │ Row 901 · "AAA-B" · invalid    │    │
│       └────────────────────────────────┘    │
│                                             │
│                                  [Close]    │
└─────────────────────────────────────────────┘
```

- File validation client-side: `.csv` extension only, max 5 MB
- Errors are not paginated — first 100 displayed, `"… N more"`
  tail-truncation if exceeded
- Calls `mutate()` on the SWR watchlist hook after success so
  the parent widget refreshes
- z-index `z-[70]` (modal layer)

testids:
- `bulk-add-tickers-modal`
- `bulk-add-tickers-file-input`
- `bulk-add-tickers-upload-button`
- `bulk-add-tickers-result-added-count`
- `bulk-add-tickers-result-errors-list`

### `RemoveAllTickersModal`

```
┌─────────────────────────────────────────────┐
│ Remove all tickers from watchlist        ✕  │
│                                             │
│ This will remove all 1,243 tickers from     │
│ your watchlist. Holdings (Portfolio) and    │
│ algo positions are NOT affected.            │
│                                             │
│ Type "REMOVE ALL" to confirm:               │
│ ┌─────────────────────────────────────────┐ │
│ │ REMOVE ALL                              │ │
│ └─────────────────────────────────────────┘ │
│                                             │
│           [Cancel]    [Remove all 1,243]    │
│                       └── rose-600 ─────┘   │
└─────────────────────────────────────────────┘
```

- Destructive button disabled until the input EXACTLY equals
  `"REMOVE ALL"` (case-sensitive)
- After success: brief inline banner "1,243 tickers removed",
  modal closes, SWR `mutate()` fires
- z-index `z-[70]`

testids:
- `remove-all-tickers-modal`
- `remove-all-tickers-input`
- `remove-all-tickers-confirm-button`

### Loading / error states

- Modal Upload button shows spinner during the POST
- On network failure: inline rose-coloured error inside the
  modal ("Upload failed: <message>"); modal stays open
- On 413: "CSV exceeds 5,000-row limit; please split it and
  try again."

### Empty state

No change to the watchlist tab's empty state ("No stocks in
your watchlist"). The overflow menu becomes the discovery
path for the bulk flow.

## 8. Caching

| Layer | Key | TTL | Invalidation |
|---|---|---|---|
| Redis (backend) | `cache:algo:open_positions:{user_id}` | 60s | Implicit via TTL only |
| Redis (backend) | `cache:dash:watchlist:{user_id}` | existing | The bulk endpoints MUST call `cache.invalidate(f"cache:dash:watchlist:{user_id}")` on success. (The existing single-ticker `link_ticker` / `unlink_ticker` endpoints today do NOT invalidate this key — the dashboard watchlist relies on the existing TTL. The bulk endpoints write at much higher volume, so the lack of invalidation would surface as stale-watchlist UX. This epic adds the invalidation only on the new bulk paths; revisiting the single-ticker paths is out of scope.) |
| SWR (frontend) | `${API_URL}/dashboard/watchlist` | n/a | `mutate()` called after each bulk-add success + remove-all success |

## 9. Testing strategy

### Backend

**`auth/repo/tests/test_repository_bulk.py` (3)**
- `test_bulk_link_tickers_inserts_new`
- `test_bulk_link_tickers_splits_added_vs_already_linked`
- `test_unlink_all_tickers_returns_row_count`

**`auth/endpoints/tests/test_ticker_routes_bulk.py` (6)**
- `test_bulk_link_happy_path_via_csv_file`
- `test_bulk_link_skips_already_linked`
- `test_bulk_link_reports_invalid_tickers`
- `test_bulk_link_rejects_csv_without_ticker_column`
- `test_bulk_link_rejects_over_5000_rows`
- `test_unlink_all_requires_exact_confirm_phrase`

**`backend/algo/live/tests/test_open_positions.py` (4)**
- `test_empty_when_no_events`
- `test_net_long_only_returned`
- `test_dry_run_fills_ignored`
- `test_cache_hit_skips_iceberg`

**`backend/tests/test_scoped_tickers_for_strategy.py` (3)**
- `test_scope_watchlist_includes_algo_open`
- `test_scope_portfolio_does_not_inject`
- `test_scope_discovery_does_not_inject`

Total backend: 16 unit tests.

### Frontend

**`frontend/components/widgets/__tests__/BulkAddTickersModal.test.tsx` (3)**
- `renders_drop_zone_and_upload_button_disabled_until_file_selected`
- `posts_multipart_form_and_renders_result_view`
- `renders_first_100_errors_with_truncation_tail`

**`frontend/components/widgets/__tests__/RemoveAllTickersModal.test.tsx` (2)**
- `confirm_button_disabled_until_phrase_typed`
- `posts_delete_and_calls_mutate_on_success`

**`frontend/components/widgets/__tests__/WatchlistOverflowMenu.test.tsx` (2)**
- `opens_menu_on_button_click`
- `bulk_add_item_click_invokes_onBulkAdd`

Total frontend: 7 vitest tests.

### E2E

**`e2e/tests/frontend/watchlist-bulk-ops.spec.ts` (1 smoke)** —
verifies the overflow menu opens and the bulk-add modal mounts.
Pre-authenticated superuser storage state. Does not actually
upload a CSV.

### Manual smoke (post-merge)

- Upload a 200-row CSV with 5 invalid tickers → confirm result
  shows `added=195, errors=5` with row indices
- Trigger a live algo BUY on a ticker NOT in the watchlist;
  verify the next backtest/paper iteration of a
  `universe=watchlist` strategy includes that ticker
- Type `"REMOVE ALL"` exactly; confirm wipes all rows;
  confirm portfolio + algo positions untouched
- Verify insights tabs (Risk, Sectors, Targets) do NOT pull
  algo open positions

## 10. Performance budget

- Backend: bulk INSERT of 5,000 rows in a single round-trip
  ≪ 1 s. CSV parse + validate in Python is O(rows) — for
  5,000 rows < 100 ms.
- `open_algo_positions`: single Iceberg scan per user (cached
  60 s). Iceberg query on `algo.events` filtered by user_id +
  mode + type + ts_date is well-indexed; typical user with
  ~50 lifetime fills resolves in < 100 ms uncached.
- Frontend: bulk-add modal adds ~3 KB gzipped to the
  dashboard bundle (modals are lazy-imported via the overflow
  menu's open handler if bundle size becomes a concern; v1
  ships inline).

## 11. Locked invariants

- `auth.tickers` PG table unchanged — no schema migration.
- `algo.live_caps.allowed_tickers` independent and unchanged.
- `_scoped_tickers` signature unchanged. New helper is
  strictly additive.
- Universe extension applies ONLY at algo strategy iteration
  (`resolve_universe`). Insights tabs unchanged.
- `open_algo_positions` lookback fixed at `"2024-01-01"`
  (same constant as Epic B's `_ATTRIBUTION_SINCE`).
- `open_algo_positions` fail-open: empty set on any read
  failure, logged at WARN. Strategy still iterates
  `watchlist ∪ holdings`.
- `open_algo_positions` Redis cache TTL = 60 s.
- CSV semantics best-effort. Skip invalid rows, return
  per-row diagnostics. Never reject whole batch over one bad
  ticker.
- Remove-all confirmation phrase literal `"REMOVE ALL"`
  (case-sensitive). Backend enforces.
- `POST /v1/tickers/bulk` returns 200 even when all rows
  error — the diagnostic is the contract.
- No new env vars.

## 12. References

- Sibling epic: **A — Order Budget Reservation** (PR #242).
- Sibling epic: **B — Algo Portfolio Tab** (PR #243).
- Watchlist storage: `auth/repo/repository.py`,
  `auth/endpoints/ticker_routes.py`.
- Universe binding: `backend/insights_routes._scoped_tickers`,
  `backend/algo/backtest/universe.resolve_universe`.
- Algo event log: `algo.events` Iceberg table; same source as
  Epic B's `_fetch_strategy_attribution`.
- Project conventions: CLAUDE.md §5.3 (SWR), §5.6 (modal
  z-index), §5.13 (Redis caching), §5.7 (pro_or_superuser).
