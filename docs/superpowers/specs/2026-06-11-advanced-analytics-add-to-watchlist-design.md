# Advanced Analytics → "Add to Watchlist" — Design

**Date:** 2026-06-11
**Status:** Approved
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
  — `backend/algo/routes/paper.py:122-137`.
- RSI(2) Connors v3 only enters on **RSI(2) ≤ 5** + market-health
  gates. With ~37 watched tickers instead of the 701-strong liquid
  universe, an entry almost never fires → 0 paper fills.

Confirmed at runtime: WS flowing (785 `cache:ltp` keys); a paper run
on 2026-06-11 12:48 completed with `fills=0`.

**Goal:** let the user grow the paper pool by appending tickers that
match their own advanced-analytics filters to their watchlist — the
exact set paper live-WS subscribes to.

## 2. Scope

In scope:
- A reusable **"Add to Watchlist"** button on every Advanced
  Analytics result-table tab.
- It appends **all rows matching the current filter** (not just the
  visible page) to the user's watchlist, **deduped server-side**.
- A JSON-list variant of the bulk-add endpoint.
- A JSON tickers endpoint on advanced-analytics (the button's source
  of the full filtered ticker list).

Out of scope:
- No strategy-specific candidate store; no change to how paper
  resolves its universe (still watchlist ∪ holdings).
- No RSI(2)-specific ranking — the user's per-tab filters define the
  candidates.
- No change to the discovery universe (`stocks.universe_snapshot`).

## 3. Approach (chosen)

Two thin endpoints + one shared button:

1. The advanced-analytics CSV export is a **server-side file
   download** (`triggerCsvDownload`), and the JSON report endpoint
   caps `page_size ≤ 200` — so the client cannot get the full
   filtered ticker list from existing JSON APIs. Add a sibling
   **`GET /advanced-analytics/{report}/tickers`** that reuses the
   export's full-set filter pipeline and returns the ticker list as
   JSON (capped at `FILTER_EXPORT_ROW_CAP`).
2. Add a **`POST /users/me/tickers/bulk-add`** JSON variant of the
   existing CSV bulk-add — same server-side dedupe via the shared
   `_bulk_link_tickers` core.
3. A shared button in `AdvancedAnalyticsTable` (→ all tabs) calls
   `GET …/tickers` then `POST …/bulk-add`, and shows inline
   added/skipped feedback.

Rejected: a single `POST …/{report}/add-to-watchlist` (Approach B) —
couples the watchlist module to advanced-analytics. Keeping the
watchlist endpoint generic ("add these tickers") is cleaner.

## 4. Backend design

### 4.1 `auth/endpoints/ticker_routes.py` — JSON bulk-add

1. **Refactor for DRY.** Extract the validate/dedupe loop and the
   repo-link/cache/report tail (currently inside `_bulk_link_impl`,
   lines 389-439) into a shared core:
   ```
   async def _bulk_link_tickers(
       *, user_id: str, rows: list[tuple[int, str]],
       source: str, total_rows: int,
   ) -> BulkTickerResponse
   ```
   `rows` is `(row_number, raw_ticker)` pairs. It normalises
   (`.upper()`), validates (`validate_ticker`), drops in-batch
   duplicates (`reason="duplicate in batch"`), calls
   `repo.bulk_link_tickers(user_id, valid, source=source)`,
   `_invalidate_watchlist_cache(user_id)`, logs, and returns
   `BulkTickerResponse`. `_bulk_link_impl` (CSV) builds
   `rows=[(i, raw) ...]` (1-based incl. header → data starts at 2)
   then calls this core — **CSV behaviour unchanged**.

2. **Request model + route:**
   ```
   class BulkAddRequest(BaseModel):
       model_config = ConfigDict(extra="forbid")
       tickers: list[str]

   @router.post("/tickers/bulk-add", response_model=BulkTickerResponse)
   async def bulk_add_tickers(
       body: BulkAddRequest,
       user: UserContext = Depends(get_current_user),
   ) -> BulkTickerResponse:
       if not body.tickers:
           raise HTTPException(400, "tickers list is empty")
       if len(body.tickers) > _BULK_ROW_CAP:
           raise HTTPException(413, f"exceeds {_BULK_ROW_CAP}-row limit")
       rows = list(enumerate(body.tickers, start=1))
       return await _bulk_link_tickers(
           user_id=user.user_id, rows=rows,
           source="bulk_json", total_rows=len(body.tickers),
       )
   ```

3. **Hoist:** add `/tickers/bulk-add` to the `bulk_suffixes` tuple
   in `_hoist_bulk_routes()` so it sits above `/{ticker}` (§#248).

### 4.2 `backend/advanced_analytics_routes.py` — JSON tickers endpoint

Add `_export_tickers(...)` mirroring `_stream_export` up to the
filtered `rows` list (reuse `_cached_full_rows`, `_filter_tickers`,
`passes_bundle_filters`, `_passes_filter`, search-needle, sort), but
return JSON instead of CSV:
```
class ReportTickersResponse(BaseModel):
    tickers: list[str]
    total: int
    capped: bool

# in create_router(): for report in REPORTS:
router.add_api_route(
    path=f"/{report}/tickers",
    endpoint=_make_tickers_endpoint(report),  # pro_or_superuser, same filter Query params as _make_endpoint MINUS page/page_size/columns
    methods=["GET"],
    response_model=ReportTickersResponse,
    name=f"advanced_analytics_{report.replace('-','_')}_tickers",
)
```
`_export_tickers` caps at `FILTER_EXPORT_ROW_CAP`: `capped =
len(rows) > CAP`; `tickers = [r.ticker for r in rows][:CAP]`;
`total = len(rows)`.

## 5. Frontend design

`frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx`:

- New `AddFilteredToWatchlistButton` placed beside the existing
  `DownloadCsvButton`, gated by the same `csvDisabled` /
  `FILTER_EXPORT_ROW_CAP` guard. `data-testid="aa-add-to-watchlist"`.
- A `handleAddToWatchlist` callback mirrors `handleCsv` (same filter
  params) but hits `GET …/{report}/tickers`, then passes the ticker
  list to the hook.
- New hook `frontend/hooks/useAddToWatchlist.ts`:
  ```
  function useAddToWatchlist(): {
    submit: (tickers: string[]) => Promise<BulkTickerResponse>;
    submitting: boolean;
    result: BulkTickerResponse | null;
    error: string | null;
    reset: () => void;
  }
  ```
  `apiFetch` POST `${API_URL}/users/me/tickers/bulk-add` with
  `{tickers}`. Reuses `BulkTickerResponse` from
  `@/lib/types/bulkTickers`.
- **Inline feedback (no global toast — none exists):** after submit,
  show a transient inline message next to the button —
  `Added {added} · {skipped} already in watchlist` (errors count if
  > 0) — `data-testid="aa-add-to-watchlist-result"`, auto-clears
  after ~6s. Mirrors `BulkAddTickersModal`'s result display.
- On success, revalidate the watchlist via SWR (mutate the
  dashboard-home / watchlist key) so the dashboard reflects the new
  tickers.

Shared table → button appears on all tabs (current-day-upmove,
previous-day-breakout, mom/wow-volume-delivery, two/three-day-scan,
top-50-delivery, swing-setups).

## 6. Data flow

```
[Advanced Analytics tab + filters] click "Add to Watchlist"
   │
   ▼  GET /v1/advanced-analytics/{report}/tickers?<filters>
ReportTickersResponse { tickers, total, capped }
   │  tickers[]
   ▼  POST /v1/users/me/tickers/bulk-add { tickers }
_bulk_link_tickers → repo.bulk_link_tickers (dedupe)
   │  invalidate cache:dash:watchlist:{user_id}
   ▼
BulkTickerResponse { added, skipped_already_linked, errors }
   │  inline "Added N · M already in watchlist" + watchlist revalidate
   ▼
watchlist ∪ holdings grows → paper live-WS subscribes to more
tickers → RSI(2) Connors v3 has more candidates to trigger on
```

## 7. Error handling

- Empty filtered set / over cap → button disabled, reuse the
  export's `csvDisabled` + tooltip.
- `GET …/tickers` returns `capped=true` → still proceed with the
  capped list; inline note "(capped at N)".
- Backend bulk-add: empty `tickers` → 400; > 5000 → 413; invalid
  tickers → per-row `errors` (not a hard failure).
- Network/5xx → inline error message; no partial client state.

## 8. Testing

Backend (`tests/backend/`):
- `_bulk_link_tickers` unit: dedupe vs existing, in-batch dups,
  invalid ticker → errors, cap.
- `POST /tickers/bulk-add`: happy (added), all-already-linked
  (skipped), empty → 400, invalidates `cache:dash:watchlist`.
- `GET /advanced-analytics/{report}/tickers`: returns filtered
  ticker list; respects filters; caps at `FILTER_EXPORT_ROW_CAP`
  with `capped=true`.

Frontend:
- vitest for `useAddToWatchlist` (success → counts; error path).
- E2E (POM, §5.14): click `aa-add-to-watchlist` on a tab, assert the
  inline result. Reuse `superuser` storage-state (advanced-analytics
  is pro/superuser-gated).

## 9. Files touched

- `auth/endpoints/ticker_routes.py` — refactor + `BulkAddRequest` +
  route + hoist.
- `backend/advanced_analytics_routes.py` — `ReportTickersResponse` +
  `_export_tickers` + `/{report}/tickers` routes.
- `tests/backend/test_ticker_routes.py` (or nearest) + advanced-
  analytics route test — backend tests.
- `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx`
  — button + handler.
- `frontend/components/advanced-analytics/AddFilteredToWatchlistButton.tsx`
  — new component.
- `frontend/hooks/useAddToWatchlist.ts` — new hook.
- `frontend/lib/types/advancedAnalytics.ts` — `ReportTickersResponse`.
- `e2e/utils/selectors.ts` + a POM + spec.

## 10. Risks / notes

- `/tickers/bulk-add` MUST be hoisted above `/{ticker}` or it 404s
  (regression #248). §4.1 item 3.
- `/{report}/tickers` route name must not collide with the existing
  `/{report}` and `/{report}/export` — distinct suffix, fine.
- Watchlist 5000-cap kept; `FILTER_EXPORT_ROW_CAP` caps the source
  list too.
- This grows the *paper* pool; a paper run must still be (re)started
  after appending for new tickers to be subscribed.
