# Watchlist Bulk Ops + Universe Binding (Epic C)

**Shipped:** 2026-05-24, PR #245, merge SHA `1866376` on dev.

## What it does

Two-part epic closing out the A→B→C algo trading dashboard arc:

1. **Watchlist bulk ops** — CSV upload to bulk-add tickers,
   typed-confirm bulk-remove. Surfaced via a new ⋮ overflow
   menu in the dashboard `WatchlistWidget`.

2. **Universe binding tweak** — strategies with
   `universe.scope=watchlist` now also see algo-held Kite
   positions (derived from `algo.events`), so they can iterate
   to (and fire exit signals on) positions they currently
   hold but aren't in the user's watchlist or
   portfolio_transactions.

## New backend surface

### Bulk endpoints

- `POST /v1/users/me/tickers/bulk` (`multipart/form-data`,
  field `file`) — CSV with required `ticker` column, optional
  `source` column. UTF-8, 5,000-row hard cap (HTTP 413).
  Returns `BulkTickerResponse{added, skipped_already_linked,
  errors: [{row, ticker, reason}], total_rows}`. Returns 200
  even when ALL rows error (the diagnostic is the contract);
  400 only when the CSV can't be parsed at all.

- `DELETE /v1/users/me/tickers/all` (JSON body
  `{"confirm": "REMOVE ALL"}`) — literal case-sensitive
  confirmation phrase enforced server-side. Returns
  `UnlinkAllResponse{removed: int}`.

Both invalidate `cache:dash:watchlist:{user_id}` on success.
Both implemented via lift-to-module-level `_impl` pattern
(testable without an `UploadFile` harness).

### Repo helpers

`auth/repo/ticker_repo.py`:
- `bulk_link_tickers(session, user_id, tickers, source) ->
  tuple[list[str], list[str]]` — single round-trip
  `INSERT ... ON CONFLICT (user_id, ticker) DO NOTHING
  RETURNING ticker`. Returns `(added, already_linked)`;
  `already_linked` preserves INPUT order.
- `unlink_all_tickers(session, user_id) -> int` — DELETE,
  returns `result.rowcount or 0`.

### `open_algo_positions(user_id) -> set[str]`

`backend/algo/live/open_positions.py` — reads `algo.events`
Iceberg (`mode='live'`, `type='order_filled_live'`,
`ts_date >= '2024-01-01'`), computes net qty by symbol
(BUY +qty, SELL -qty, IGNORES `payload.dry_run = True`), returns
`{sym for sym, qty in net.items() if qty > 0}`.

- Cache: `cache:algo:open_positions:{user_id}` (60s TTL).
- Fail-open: empty set on any Iceberg/Redis read failure
  (logged at WARN). Strategy still iterates
  `watchlist ∪ holdings`; just loses the algo-held injection
  until the next read succeeds.

### `_scoped_tickers_for_strategy(user, scope)`

`backend/insights_routes.py` — sibling helper to
`_scoped_tickers`. For `scope=watchlist`: returns
`_dedup(_scoped_tickers(user, "watchlist"),
sorted(open_algo_positions(user.user_id)))`. Other scopes
delegate verbatim.

**Only caller switched to the new helper:**
`backend/algo/backtest/universe.py::resolve_universe`.
All 12+ other `_scoped_tickers` callers (insights tabs Risk,
Sectors, Dividends, Piotroski, Targets, Correlation,
Quarterly, Screener, ScreenQL) keep calling
`_scoped_tickers` unchanged — no Kite-latency hit on
dashboard reads.

## New frontend surface

- `frontend/components/widgets/BulkAddTickersModal.tsx` — file
  picker → POST multipart → result view with per-row error
  truncation at 100 + "N more" tail.
- `frontend/components/widgets/RemoveAllTickersModal.tsx` —
  typed-confirm input; destructive button disabled until input
  EXACTLY equals `"REMOVE ALL"` (case-sensitive).
- `frontend/components/widgets/WatchlistOverflowMenu.tsx` —
  ⋮ button + dropdown with click-outside + Escape close.
  Two items: "Bulk add tickers…" + "Remove all…" (rose-600).
- `frontend/components/widgets/WatchlistWidget.tsx` — renders
  the menu next to the existing `+` button when
  `activeTab === "watchlist"`. Mounts both modals at bottom of
  the return; both call `onRefresh()` (parent SWR mutate) on
  success.
- `frontend/lib/types/bulkTickers.ts` — TS shapes mirroring
  the backend Pydantic.

## Locked invariants

- `auth.user_tickers` PG table unchanged — no migration.
- `algo.live_caps.allowed_tickers` (Cap 2 in `pre_trade_check`)
  is INDEPENDENT and unchanged. This epic does NOT touch the
  trade-time whitelist.
- `_scoped_tickers` signature unchanged — strictly additive
  sibling.
- `open_algo_positions` lookback fixed at `"2024-01-01"` (same
  constant as Epic B's `_ATTRIBUTION_SINCE`).
- CSV semantics best-effort: skip invalid rows, return per-row
  diagnostics. Never reject whole batch over one bad ticker.
- `cache:dash:watchlist:*` invalidation added ONLY on the new
  bulk paths (the existing single-ticker `link_ticker` /
  `unlink_ticker` endpoints today do NOT invalidate; revisiting
  is out of scope).

## Tests

- 16 backend tests: 3 repo (`test_ticker_repo_bulk.py`) + 6
  routes (`test_ticker_routes_bulk.py`) + 4 open_positions
  (`test_open_positions.py`) + 3 scoped-for-strategy
  (`test_scoped_tickers_for_strategy.py`).
- 7 vitest tests: 3 BulkAdd + 2 RemoveAll + 2 OverflowMenu.
- 1 E2E smoke at `e2e/tests/frontend/watchlist-bulk-ops.spec.ts`.

## Out of scope (v1, deferred)

CSV bulk-remove, text-area paste, portfolio bulk import,
multi-select table view, dedicated `/watchlist` page, dry-run
preview, async/job-id flow, extension to insights tabs,
consolidation with Epic B's `_get_algo_positions_impl`.

## Spec / plan

- `docs/superpowers/specs/2026-05-24-watchlist-bulk-ops-universe-binding-design.md`
- `docs/superpowers/plans/2026-05-24-watchlist-bulk-ops-universe-binding.md`
