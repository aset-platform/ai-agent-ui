# Advanced Analytics → "Add to Watchlist" — Design

**Date:** 2026-06-11
**Status:** Approved (pending spec review)
**Author:** Abhay Kumar Singh

## 1. Problem & context

Paper trading for **RSI(2) Connors Daily v3** (strategy
`0b267c76-2ae9-4057-ae33-aafe3f7a96f5`, `mode=paper`) produces **0
fills**. Root cause is the candidate pool, not promotion or
connectivity:

- The strategy's AST universe is `scope=discovery` (india stock,
  `min_adtv_inr=50M`) → ~701 candidate tickers.
- But **paper live-WS deliberately ignores `discovery`** and only
  subscribes to the user's **watchlist ∪ holdings** (~37 tickers)
  — `backend/algo/routes/paper.py:122-137` ("subscribing to
  thousands of NSE tokens is impractical").
- RSI(2) Connors v3 only enters on **RSI(2) ≤ 5** + market-health
  gates. With ~37 watched tickers instead of the 701-strong liquid
  universe, an entry almost never fires → 0 paper fills.

Confirmed at runtime: WS is flowing (785 `cache:ltp` keys); a paper
run on 2026-06-11 12:48 completed with `fills=0`.

**Goal:** let the user grow the paper pool by appending tickers that
match their own advanced-analytics filters to their watchlist —
which is exactly the set paper live-WS subscribes to.

## 2. Scope

In scope:
- A reusable **"Add to Watchlist"** button on every Advanced
  Analytics result-table tab.
- It appends **all rows matching the current filter** (not just the
  visible page) to the user's watchlist, **deduped server-side**.
- A JSON-list variant of the existing bulk-add endpoint.

Out of scope:
- No strategy-specific candidate store; no change to how paper
  resolves its universe (it keeps using watchlist ∪ holdings).
- No RSI(2)-specific ranking — the user curates via the existing
  per-tab filters. (Whichever tab/filter they choose defines
  "most probable candidates".)
- No change to the discovery universe (`stocks.universe_snapshot`).

## 3. Approach (chosen)

**Client reuses the export's full-filtered fetch → new JSON bulk
endpoint.** The button lives beside the existing
`DownloadCsvButton` in the shared `AdvancedAnalyticsTable`. On click
it runs the same full-filtered fetch the CSV export already performs
(report endpoint + current filters, capped at
`FILTER_EXPORT_ROW_CAP`), extracts the `ticker` column, and POSTs
the list to a new JSON variant of `/tickers/bulk`. Reuses two proven
paths (export fetch + `_bulk_link_impl` dedupe), one shared
component → appears on all tabs, minimal coupling.

Rejected: a server-side `{report, filters}` endpoint — couples
`auth/ticker_routes` to the advanced-analytics report module and
duplicates filter plumbing for no real benefit.

## 4. Backend design

`auth/endpoints/ticker_routes.py`:

1. **Refactor** `_bulk_link_impl` to extract a ticker-list core:
   ```
   async def _bulk_link_tickers(
       *, user_id: str, tickers: list[str],
   ) -> BulkTickerResponse
   ```
   It performs normalize → validate → `repo.bulk_link_tickers` →
   per-row report (added / skipped_already_linked / in-batch dup /
   errors), enforcing the existing 5000-row cap. The current CSV
   route (`bulk_link_tickers`, `POST /tickers/bulk`) parses the CSV
   then calls this core — **behavior unchanged**.

2. **New route**:
   ```
   POST /v1/users/me/tickers/bulk-add
   body: { "tickers": ["RELIANCE.NS", ...] }   # BulkAddRequest
   resp: BulkTickerResponse                     # added/skipped/errors
   ```
   Validates non-empty + <= 5000, calls `_bulk_link_tickers`,
   invalidates `cache:dash:watchlist:{user_id}` (mirrors the CSV
   route's invalidation).

3. **Route hoist:** add `/tickers/bulk-add` to the
   `bulk_suffixes` tuple in `_hoist_bulk_routes()` so it sits above
   the `/{ticker}` wildcard (§#248 regression guard).

Dedupe semantics (already implemented in the impl/repo): tickers
already linked → `skipped_already_linked`; duplicates within the
batch → dropped (`reason="duplicate in batch"`); invalid/unknown →
`errors`.

## 5. Frontend design

`frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx`
(shared by all tabs):

- New `AddFilteredToWatchlistButton`, placed next to
  `DownloadCsvButton`, gated by the same row-cap / empty-result
  guard the export uses. `data-testid="aa-add-to-watchlist"`
  (§5.14). Disabled (with tooltip) when the filtered set is empty
  or exceeds the cap.
- New hook `frontend/hooks/useAddToWatchlist.ts`:
  `addToWatchlist(tickers: string[]) => Promise<{added, skipped}>`
  via `apiFetch` POST to `/users/me/tickers/bulk-add` (API_URL,
  versioned). On success: toast
  `Added {added} · skipped {skipped} already in watchlist`, then
  revalidate the watchlist SWR key (`useDashboardHome` /
  `cache:dash:watchlist`).
- The button fetches the full filtered ticker list using the same
  report query + filters the export uses (reuse the export helper /
  full-fetch path), maps `row.ticker`, dedupes client-side for
  payload hygiene, and sends to the hook.

Because the button is in the shared table, it appears on all tabs
(current-day-upmove, previous-day-breakout, mom/wow-volume-delivery,
two/three-day-scan, top-50-delivery, swing-setups) automatically.

## 6. Data flow

```
[Advanced Analytics tab + user filters]
        |  click "Add to Watchlist"
        v
full-filtered fetch (report + filters, capped)
        |  ticker[]
        v
POST /v1/users/me/tickers/bulk-add { tickers }
        |  _bulk_link_tickers -> repo.bulk_link_tickers (dedupe)
        v
BulkTickerResponse { added, skipped_already_linked, errors }
        |  invalidate cache:dash:watchlist:{user_id}
        v
toast (added / skipped) + watchlist SWR revalidate
        |
        v
watchlist U holdings grows -> paper live-WS subscribes to more
tickers -> RSI(2) Connors v3 has more candidates to trigger on
```

## 7. Error handling

- Empty filtered set → button disabled, tooltip "No rows match the
  current filter".
- Over `FILTER_EXPORT_ROW_CAP` → reuse the existing "tighten
  filters" disabled-state/message.
- Backend: empty `tickers` → 400; > 5000 → 400; invalid tickers →
  reported per-row in `errors` (not a hard failure).
- Network/5xx → error toast; no partial client state.

## 8. Testing

Backend (`tests/`):
- `_bulk_link_tickers` unit: dedupe vs existing, in-batch dups,
  invalid ticker → errors, cap enforcement.
- `POST /tickers/bulk-add` route: happy (added), all-already-linked
  (skipped), empty (400), and confirms `cache:dash:watchlist`
  invalidation.

Frontend:
- vitest for `useAddToWatchlist` (success → counts; error path).
- E2E (POM, §5.14): click `aa-add-to-watchlist` on one tab, assert
  the success toast. Reuse `general-user` storage-state fixture.

## 9. Files touched

- `auth/endpoints/ticker_routes.py` — refactor + new route + hoist.
- `tests/backend/test_ticker_routes*.py` (or nearest existing) —
  backend tests.
- `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx`
  — button mount.
- `frontend/components/advanced-analytics/AddFilteredToWatchlistButton.tsx`
  — new component.
- `frontend/hooks/useAddToWatchlist.ts` — new hook.
- `e2e/` — POM + spec; `e2e/utils/selectors.ts` testid registry.
- `frontend/lib/types*` — `BulkAddRequest` / response types if not
  already present.

## 10. Risks / notes

- The new route MUST be hoisted above `/{ticker}` or it 404s
  (regression #248). Covered in §4, item 3.
- Keep the watchlist 5000-cap; large filtered sets are capped by
  `FILTER_EXPORT_ROW_CAP` client-side anyway.
- This grows the *paper* pool; a paper run must still be (re)started
  after appending for the new tickers to be subscribed.
