# Paper-mode coverage for Epic A/B/C

**Shipped:** 2026-05-26, PR #249, merge SHA `593c1bc` on dev.
Plus the Budget tab move and supervisor logging in the same PR.

## What it does

Extends the Epic A (budget reservations), Epic B (Algo
dashboard tab), and Epic C (universe injection) wiring to
recognize paper-mode trading. Before this, paper bypassed
all three surfaces — validating the wiring required real
Kite money. Now paper trading produces visible audit-only
reservations + dashboard rows + universe injection while
preserving real-money safety semantics.

## Epic A — Paper budget lifecycle (audit-only)

- `backend/algo/paper/runtime.py::_emit_paper_budget_lifecycle`
  fires the full `reserve() → transition(SUBMITTED) →
  transition(FILLED)` chain on every paper fill (3 sites:
  stop-loss, time-stop, signal).
- The helper schedules the budget coroutine via
  `loop.create_task` on the **already-running** uvicorn
  event loop — NOT via `threading.Thread + asyncio.run`.
  Important: see `mem:paper-runtime-sync-async-bridge`
  for why this matters (cached PG factory is loop-bound).
- Reservation metadata stamps `mode="paper"` so:
  - `sum_active_reservations` (Cap 0 input) excludes
    `metadata->>'mode' = 'paper'` rows. Paper does NOT
    deduct from real-money headroom.
  - FE renders the rows with an amber **PAPER badge** in
    the BudgetPanel reservations table.

## Epic B — Paper positions on the dashboard Algo tab

- `AlgoPositionRow` grows `source: Literal["live","paper"]`
  field (default "live", backwards-compat).
- New `_paper_positions_from_events` in
  `backend/algo/routes/portfolio.py` synthesises open paper
  positions by netting BUY/SELL fills from `algo.events`
  (`mode='paper'`). Returns rows with `source="paper"`.
- `_get_algo_positions_impl` pulls paper positions BEFORE
  Kite, so the Algo tab works even when Kite is unreachable.
- FE renders amber **PAPER badge** in
  `AlgoPositionRow.tsx` for `source='paper'` rows.

## Epic C — Paper-aware universe injection

- `open_algo_positions(user_id, modes=("live",))` — the
  `modes` kwarg widens the mode filter. Cache key includes
  the mode tuple to prevent live-only / live+paper read
  cross-poisoning.
- `_scoped_tickers_for_strategy` passes
  `modes=("live", "paper")` so paper strategies iterate to
  their currently-held tickers + can fire exit signals.
- Insights tabs unchanged — they still call
  `_scoped_tickers` directly, no paper injection.

## UI restructure

BudgetPanel got its own **Budget** tab in the Live page tab
strip (between Postbacks and Settings) — was previously
mounted at the top of `LiveDashboard` and dominated the
screen. URL: `/algo-trading/live?tab=budget`.

Changes:
- `frontend/lib/types/algoTrading.ts` — `LiveTabId` union
  + `LIVE_TAB_ORDER` + `LIVE_TAB_LABELS` grew a `budget`
  entry.
- `frontend/app/(authenticated)/algo-trading/live/LiveClient.tsx`
  — new switch case mounts `<BudgetPanel />` under the
  `budget` tab.
- `frontend/components/algo-trading/live/LiveDashboard.tsx`
  — removed the top-of-page `<BudgetPanel />` mount.

## Supervisor observability

`PaperSupervisor.start_run` now adds a done-callback to the
spawned task. Logs on every completion:
- `PaperSupervisor: run completed user=… strat=… fills=N`
  on clean exit
- `PaperSupervisor: run raised user=… strat=…` with full
  traceback on uncaught exception

Before: paper-mode failures got reaped silently by
`list_active` before the operator could see them — making
debugging effectively impossible.

## Known minor leftovers (non-blocking)

- Orphan reservations: when runtime tears down before
  `loop.create_task` chain drains, ~10% of paper
  reservations stay PENDING/SUBMITTED forever (until
  reconciliation TIMEs them out at 120s/5min). Could be
  hardened with `await asyncio.gather(*pending_tasks)`
  before runtime cleanup.
- `Available = ₹0` UX when Kite returns ₹0 wallet — math
  is correct (`min(allocated, kite)`), reads misleadingly.
  Could fall back to internal headroom when Kite is empty.

## Test surfaces

The wiring was end-to-end validated 2026-05-26 against a
68-fill paper run of "CNC RSI Scalper (15m)" on the
`ticks_indian_universe.jsonl` fixture:

- 85 rows written to `algo.budget_reservations` (73 FILLED,
  9 PENDING orphan, 3 SUBMITTED orphan)
- All stamped `metadata.mode='paper'`
- BudgetPanel renders PAPER badge on each row
- `Pending` headroom tile reads ₹0 — paper rows correctly
  excluded from Cap 0 math
- Algo dashboard tab empty because every round-trip
  closed (correct behavior — no `net qty > 0` positions)

## Related

- `mem:algo-budget-reservation-overview` — Epic A (live)
- `mem:algo-portfolio-dashboard-tab` — Epic B (live)
- `mem:watchlist-bulk-ops-universe-binding` — Epic C
- `mem:paper-runtime-sync-async-bridge` — the loop-attach
  gotcha that made the first paper-mode implementation
  fail silently
