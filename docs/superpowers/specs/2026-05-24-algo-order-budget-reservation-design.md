# Algo Order Budget Reservation — Design

**Date:** 2026-05-24
**Author:** Abhay (with Claude pair-programming)
**Status:** Spec — pending implementation plan
**Part of:** Live algo trading hardening epic. This is **Epic A**; Epic B (Algo Portfolio tab on the dashboard) and Epic C (Watchlist bulk ops) are separate specs in this same series.

## Goal

Add an order-level budget reservation ("ticketing") system to the live algo trading engine so that:

- When an order is submitted to Kite, the order's notional is **locked** against a per-user algo budget pool.
- The next order only sees the **remaining** unlocked headroom.
- When Kite reports an order is rejected, cancelled, or partially cancelled, the unfilled portion is **released** back to the pool.
- Open positions (today's + prior days') count against the pool until they're sold.
- Kite's actual broker-side available cash is used as a **second** gate so manual trades on Kite, T+1 settlement holds, MIS auto-square-off lag, and freeze margin all naturally reduce the algo's headroom.

The single number the user cares about — "how much can my algo place RIGHT NOW?" — has a clear, auditable computation.

## Background — what exists today

- `backend/algo/broker/kite_client.py` wraps the Kite Connect API (`place_order`, `margins`, `positions`).
- `backend/algo/live/safety.py:check_pre_trade()` runs 9 caps (Cap 1 = live_orders_enabled, ..., Cap 4 = `max_inr` per-strategy notional cap, Cap 5-9 = risk-engine logic).
- `algo.live_caps` (per (user, strategy)) carries `max_inr`, `max_orders_per_day`, `allowed_tickers`, `live_orders_enabled`.
- `backend/algo/live/reconciliation.py` already runs a periodic Kite-status reconciliation.
- `backend/algo/live/order_timeout.py` exists for dead-order detection.
- `backend/algo/live/position_hydration.py` rebuilds open-position state from `algo.events` on restart.
- The existing `max_inr` cap is **exposure-based** (sums committed notional from THIS strategy's open positions). Different strategies are isolated; a user with two strategies can over-allocate beyond the real Kite wallet.

What's missing — the gap this spec closes:

1. **No user-pool view.** Two strategies can each have ₹50k cap and both fire orders even if the Kite wallet only has ₹70k.
2. **No reservation lifecycle.** A submitted order doesn't lock its notional until the gate runs again — concurrent orders can both pass the gate and over-commit.
3. **No Kite-side reconciliation in the gate.** Manual trades on Kite, T+1 cash holds, MIS settlement lag are all invisible to the algo's pre-trade check.
4. **No release-on-cancel.** A cancelled order's notional stays committed in the per-strategy `cumulative_inr_today` until end-of-day reset.

## Decisions matrix

| Decision | Choice | Alternatives considered |
|---|---|---|
| Allocation model | **A2 — Single user pool** with per-strategy `max_inr` retained as a secondary cap | A1 per-strategy-only (over-allocates), A3 hybrid (deferred to v2) |
| Reconciliation with Kite | **B3 — `min(internal_headroom, kite_available)`** at gate time | B1 internal-only (over-promises), B2 Kite-only (loses internal forecasting) |
| Position valuation | **C1 — Entry cost basis** for open-position commitment | C2 MTM (UI flicker), C3 max-of-both (over-conservative) |
| Persistence shape | **Append-only event log** in `algo.budget_reservations` | Mutable rows (race-prone with reconciliation + webhook concurrency) |
| Reservation timeout | **120s PENDING → TIMEOUT**, **5min SUBMITTED with no Kite update → TIMEOUT** | Single hard timeout (loses retry capacity) |
| SELL handling | **SELL bypasses Cap 0** (releases capital, doesn't commit) | Gate SELL too (would deadlock square-offs) |
| Kite-down behaviour | **Fail-open on Kite, fail-closed on internal** | Fail-closed on both (halts all algo trading when Kite has a blip) |

## Out of scope

| Deferred | Why |
|---|---|
| Bracket Orders (BO) + Cover Orders (CO) | Multi-leg semantics; existing `kite_client.place_order` already defers these to v3. Budget Cap 0 inherits the limitation. |
| Order modifications (modify_order) | Modifying qty/price changes reservation amount; needs atomic release/re-reserve. Not in v1. |
| Per-strategy budget sub-pools (A3) | User-pool only in v1. A3 hybrid is a v2 if A2 proves too coarse. |
| F&O / derivatives margin estimation | v1 treats notional = `qty × ltp` (correct for equity delivery, wrong for SPAN/exposure on F&O). F&O budget gating is a follow-up. |
| Multi-currency / non-INR | Kite is INR-only. US equities via a different broker = different epic. |
| User-driven "cancel my SUBMITTED order" button | Existing `kite_client.cancel_order` path is shipped. A row-level cancel UI is a small follow-up after v1. |
| Per-strategy allocation UI consolidation | Live caps UI for `max_inr` stays where it is. Budget Panel is the user-pool view above it. |
| Headroom forecasting / predictive analytics | "You'll exhaust budget by 14:30 IST" — pure observability. Not v1. |
| Auto-rebalance allocation | Explicit user action only in v1. Never auto-set. |
| WebSocket push for reservation updates | v1 uses 3s SWR polling. WebSocket push is a v2 optimisation. |

## Architecture overview

```
User sets ALLOCATED_INR (e.g., ₹1,00,000)
            │
            ▼
   algo.user_budget table (1 row per user)
            │
            ▼   ┌─────────────────────────────────────────┐
            │   │  Order gate (safety.py — new Cap 0)     │
            ├──►│                                         │
   open     │   │  approved iff order_cost ≤ min(         │
   positions│   │     allocated_inr                       │
   (algo)   │   │       - Σ(open_position_cost)           │ ← C1
            │   │       - Σ(active_reservations),         │ ← new
            │   │     kite.margins.equity.available.cash  │ ← B3
            │   │  )                                      │
            ▼   └─────────────────────────────────────────┘
   algo.budget_reservations table (event log)
   one row per state transition per reservation_id
            │
            ▼
   Kite call → reconciliation loop updates state:
     PENDING → SUBMITTED → FILLED / REJECTED /
                          CANCELLED / TIMEOUT
            │
            ▼
   On FILLED → reservation closed, position created
   On REJECTED/CANCELLED/TIMEOUT → reservation released
```

**Two existing scaffolds we leverage:**
- `backend/algo/live/order_timeout.py` — already handles dead-order detection
- `backend/algo/live/reconciliation.py` — already polls Kite for status updates

**Two new PG tables:**
- `algo.user_budget` — one row per user; mutable; carries `allocated_inr` + `enabled` flag
- `algo.budget_reservations` — append-only; one row per state transition

**One new safety check** added to `safety.py:check_pre_trade()` as Cap 0 (runs BEFORE the existing per-strategy `max_inr` cap). Both gates fire — an order must pass user-pool AND per-strategy.

## Reservation lifecycle

### States and transitions

```
                  ┌────────────┐
                  │  PENDING   │  (in-process; before Kite call)
                  └─────┬──────┘
                        │  kite_client.place_order() returns
                        ▼
              ┌────────────────────┐
              │      SUBMITTED      │  (Kite acknowledged; has kite_order_id)
              └─────────┬──────────┘
                        │ Kite reports status via reconciliation/webhook
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   ┌─────────┐   ┌───────────┐   ┌───────────┐
   │ FILLED  │   │ REJECTED  │   │ CANCELLED │
   └─────────┘   └───────────┘   └───────────┘
   reservation   reservation     reservation
   becomes a    released         released
   position
   (and is      ┌───────────┐
   archived)    │  PARTIAL  │ → if remainder fills: FILLED
                └───────────┘   if remainder cancels: PARTIAL_CANCELLED
                                (unfilled qty released)

                  ┌────────────┐
                  │  TIMEOUT   │  (reconciliation loop forces this)
                  └────────────┘
                  released after N seconds no Kite update
```

### Event-log persistence

`algo.budget_reservations` is **append-only** — one row per state transition. The "current state" of a reservation is the row with the latest `transitioned_at` for a given `reservation_id`.

```sql
-- One row per STATE TRANSITION (not per order). The same
-- reservation_id appears in multiple rows; current state =
-- row with latest transitioned_at for that reservation_id.
--
-- reserved_inr is the ORIGINAL commitment (₹ at order time);
-- carried verbatim across all state rows for the same
-- reservation_id, so the column reads cleanly as "this
-- reservation locked ₹X in total."
--
-- filled_inr is the cumulative executed value at this state
-- (0 on PENDING/SUBMITTED, partial on PARTIAL, == reserved_inr
-- on FILLED, 0 on REJECTED/CANCELLED/TIMEOUT).
--
-- The "released amount" on terminal states is implicit:
-- released = reserved_inr - filled_inr (returned to the pool).
--
-- Active reservations (still locking capital) are those whose
-- CURRENT state ∈ {PENDING, SUBMITTED, PARTIAL}; their
-- contribution to active_reserved is reserved_inr - filled_inr.
-- Terminal states (FILLED/REJECTED/CANCELLED/TIMEOUT/
-- PARTIAL_CANCELLED) contribute 0; rows are NEVER deleted —
-- they stay for audit. The "archived" reference in the FILLED
-- branch of the state machine means "no longer queried by the
-- active-reservations gate; the row stays for history."
CREATE TABLE algo.budget_reservations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reservation_id  UUID NOT NULL,          -- groups state changes
    user_id         UUID NOT NULL,
    strategy_id     UUID NOT NULL,
    state           TEXT NOT NULL,          -- PENDING/SUBMITTED/FILLED/...
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,          -- BUY / SELL
    qty             INT  NOT NULL,
    reserved_inr    NUMERIC(14,2) NOT NULL, -- original lock (constant per reservation_id)
    filled_qty      INT NOT NULL DEFAULT 0, -- cumulative executed
    filled_inr      NUMERIC(14,2) NOT NULL DEFAULT 0,
    kite_order_id   TEXT,
    transitioned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB DEFAULT '{}'::jsonb,
    error_text      TEXT
);

CREATE INDEX idx_budget_res_active
    ON algo.budget_reservations (user_id, reservation_id)
    WHERE state IN ('PENDING', 'SUBMITTED', 'PARTIAL');

CREATE INDEX idx_budget_res_user_time
    ON algo.budget_reservations (user_id, transitioned_at DESC);
```

Active reservations queried via:

```sql
SELECT DISTINCT ON (reservation_id) *
FROM algo.budget_reservations
WHERE user_id = :uid
ORDER BY reservation_id, transitioned_at DESC;
-- Then filter the result to state IN (PENDING, SUBMITTED, PARTIAL).
```

`algo.user_budget` is a small mutable row per user:

```sql
CREATE TABLE algo.user_budget (
    user_id        UUID PRIMARY KEY,
    allocated_inr  NUMERIC(14,2) NOT NULL DEFAULT 0,
    enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by     UUID
);
```

Default (no row) is treated as `allocated_inr=0, enabled=False` → all BUY orders rejected by Cap 0.

### Timeout policy

Two-layer:
- **Hard timeout = 120 seconds** for PENDING (no Kite ack). After 120s, append a TIMEOUT row, release the reserved INR.
- **Reconciliation loop = every 30 seconds** for SUBMITTED. Query Kite for order status; if Kite says complete/cancelled/rejected, transition accordingly. If Kite says nothing for **5 minutes** continuously, force TIMEOUT.

Reconciliation reuses the existing `backend/algo/live/reconciliation.py` job; we just extend it to also write reservation transitions.

### Partial fill handling

- Order BUY 100 @ ₹100 → reservation reserves ₹10,000.
- Kite reports filled_qty=80 → append PARTIAL row with `filled_qty=80, filled_inr=8000`. Reserved INR for the unfilled 20 stays committed in case Kite fills it.
- Kite reports rest CANCELLED → append PARTIAL_CANCELLED with `filled_qty=80, filled_inr=8000, reserved_inr=10000` (unchanged from earlier rows). The implicit released amount is `reserved_inr − filled_inr = 2000`. The 80 filled becomes a position (cost basis = ₹8000); the ₹2000 goes back to the pool because PARTIAL_CANCELLED is a terminal state and `sum_active_reservations` no longer counts this row.
- Kite reports rest FILLED → append FILLED row with `filled_qty=100, filled_inr=10000`. Position created at cost basis ₹10,000.

### Why event-log not mutable rows

- **Audit-friendly** — you can replay any reservation's history for debugging or compliance.
- **Concurrent-safe** — multiple writers (place-order path + reconciliation loop + webhook) can INSERT without conflicting. A mutable row would need a WHERE clause on expected current state, which races.
- **Iceberg-style "append + read latest"** matches how the rest of this codebase tracks state changes (`algo.events`, `algo.strategy_mode_transitions`).

## Gate logic

A new **Cap 0** is inserted at the top of `backend/algo/live/safety.py:check_pre_trade()`, BEFORE the existing per-strategy `max_inr` cap.

```python
# Cap 0 — user-pool budget reservation (NEW)
if signal.side != "SELL":
    user_budget = await load_user_budget(user_id)
    open_pos_cost = await sum_open_position_cost(user_id)
    active_reserved = await sum_active_reservations(user_id)

    internal_headroom = (
        user_budget.allocated_inr
        - open_pos_cost
        - active_reserved
    )
    kite_available = await fetch_kite_available_cash(user_id)

    order_cost = Decimal(signal.qty) * last_price

    headroom = min(internal_headroom, kite_available)
    if order_cost > headroom:
        return _reject_live(
            RejectReason.LIVE_BUDGET_CAP,
            threshold=headroom,
            observed=order_cost,
            metadata={
                "internal_headroom": str(internal_headroom),
                "kite_available": str(kite_available),
                "user_allocated_inr": str(user_budget.allocated_inr),
                "open_pos_cost": str(open_pos_cost),
                "active_reserved": str(active_reserved),
            },
        )

# Existing Cap 1-9 below unchanged
```

### Reject reason

Add `LIVE_BUDGET_CAP = "live_budget_cap"` to the `RejectReason` enum. Surfaces in admin reject logs + frontend Live tab error UI like every other reject reason.

### SELLs bypass the gate

A SELL releases capital. Gating SELL on budget would be self-defeating. Cap 0 mirrors the existing Cap 4 `if signal.side != "SELL":` guard.

### Sub-helpers

Four new helpers in a new file `backend/algo/live/budget.py`:

- `load_user_budget(user_id) -> UserBudget` — reads `algo.user_budget`. Default (no row) → `UserBudget(allocated_inr=0, enabled=False)`.
- `sum_open_position_cost(user_id) -> Decimal` — sums cost basis of OPEN positions across all algo strategies. Cached 5s per user. Reads from `algo.events` FILL-without-matching-SELL log via existing `position_hydration.py` derivation logic.
- `sum_active_reservations(user_id) -> Decimal` — sums `reserved_inr − filled_inr` across reservations whose CURRENT state ∈ {PENDING, SUBMITTED, PARTIAL}. Uses `DISTINCT ON (reservation_id) ORDER BY transitioned_at DESC` to find current state.
- `fetch_kite_available_cash(user_id) -> Decimal` — reads `kite.margins['equity']['available']['cash']`. Cached 5s per user. On Kite error: returns `Decimal('inf')` (fail-open), logs WARNING with `exc_info=True`.

### Kite-side failure handling

- **Kite API down** → `fetch_kite_available_cash` returns `Decimal('inf')`. `min(internal_headroom, inf) = internal_headroom`. We still gate on the internal ledger. Logged at WARNING. **Fail-open on Kite, fail-closed on internal.** Rationale: internal ledger is authoritative for what we've committed; Kite is the defensive second opinion. If Kite is down, we don't want to halt all algo trading — we degrade to internal-only mode.
- **Internal PG down** → safety check fails entirely → order rejected via existing fail-closed semantics in `safety.py`. No degradation.

### Reservation creation order

The gate APPROVES → reservation must exist BEFORE the Kite call so a concurrent second order sees the lock.

```python
1. Pre-trade gate approves
2. INSERT algo.budget_reservations (state=PENDING, reserved_inr=order_cost)
   - PG COMMIT
3. kite_client.place_order(...)
   - On success: INSERT (state=SUBMITTED, kite_order_id=...)
   - On Kite-side rejection: INSERT (state=REJECTED, error_text=...)
   - On network failure: stay PENDING; timeout job releases after 120s
```

The PENDING insert is the **lock acquisition**. Any concurrent order from the same user sees this row via `sum_active_reservations` and gets less headroom.

### Caching

5-second cache (Redis-backed, per-user keys):
- `cache:budget:user:{user_id}:open_pos_cost`
- `cache:budget:user:{user_id}:active_reserved`
- `cache:budget:user:{user_id}:kite_available`

Cache invalidation: every INSERT into `algo.budget_reservations` triggers `cache.invalidate_exact()` on the three keys above. Pattern matches the `_CACHE_INVALIDATION_MAP` per CLAUDE.md §5.13.

### Observability

Every approval / rejection writes a structured log line:

```
[budget-gate] user=<u> strategy=<s> side=<BUY/SELL> ticker=<t>
  qty=<q> order_cost=<n> headroom=<h>
  internal=<i> kite=<k> decision=<approved/rejected>
  reservation_id=<r>
```

Plus existing `algo.events` row written by the safety belt (already in place — just add `LIVE_BUDGET_CAP` to the existing reject_reason list).

## Component layout

### New files

| Path | Responsibility |
|---|---|
| `backend/db/migrations/versions/2026_05_24_add_budget_tables.py` | Alembic migration — creates `algo.user_budget` + `algo.budget_reservations` + indices |
| `backend/algo/live/budget_types.py` | Pydantic types — `UserBudget`, `BudgetReservation`, `ReservationState` enum |
| `backend/algo/live/budget_repo.py` | Async PG repo — load/upsert `algo.user_budget`; insert/query `algo.budget_reservations` event log |
| `backend/algo/live/budget.py` | The 4 helper functions from the gate-logic section + high-level `reserve()` / `transition()` / `release()` API |
| `backend/algo/live/budget_reconciliation.py` | Periodic job: 120s PENDING timeout, 30s SUBMITTED reconcile, 5min hard SUBMITTED timeout |
| `backend/algo/routes/budget.py` | HTTP routes — `GET /v1/algo/budget`, `PUT /v1/algo/budget/allocation`, `GET /v1/algo/budget/reservations`, `POST /v1/algo/budget/reservations/{id}/force-release` |
| `frontend/lib/types/algoBudget.ts` | TS shapes mirroring backend Pydantic |
| `frontend/hooks/useBudget.ts` | SWR hooks — `useUserBudget()`, `useActiveReservations()` (3s poll), `setAllocation()` |
| `frontend/components/algo-trading/BudgetPanel.tsx` | New panel in the Live tab — tiles + reservations table |
| `frontend/components/algo-trading/BudgetAllocationModal.tsx` | Set / change `allocated_inr` |
| `frontend/components/algo-trading/BudgetReservationHistoryModal.tsx` | Full event log view |

### Modified files

| Path | Change |
|---|---|
| `backend/algo/live/safety.py` | Insert **Cap 0** budget check before existing Cap 1-9 |
| `backend/algo/live/safety_types.py` (or wherever `RejectReason` lives) | Add `LIVE_BUDGET_CAP` enum value |
| `backend/algo/broker/kite_client.py` | Call `budget.reserve()` immediately after gate approval AND before the Kite REST call, then `budget.transition()` on Kite response |
| `backend/algo/live/reconciliation.py` | Extend existing periodic job to also drive `budget_reconciliation.reconcile()` |
| `backend/algo/routes/__init__.py` | Mount the new budget router |
| `backend/routes.py` | `app.include_router(create_budget_router(), prefix="/v1")` |
| `frontend/components/algo-trading/LiveTab.tsx` | Mount `<BudgetPanel />` at top of Live tab content |

### Existing types we reuse

- `KiteClient.margins(segment="equity")` — already returns the dict we read for `available.cash`.
- `algo.events` table — positions derivable from FILL events; existing `position_hydration.py` already does this on restart. `sum_open_position_cost` reuses that derivation.
- `RejectReason` enum + `_reject_live` helper — already feeds reject events log + frontend display.

### Dependency wiring

```
Order signal arrives at safety.check_pre_trade()
                  │
                  ▼
   Cap 0: budget.py.check_headroom()  ← NEW
        ├─ load_user_budget(user_id)           [PG: algo.user_budget]
        ├─ sum_open_position_cost(user_id)     [PG: algo.events]
        ├─ sum_active_reservations(user_id)    [PG: algo.budget_reservations]
        └─ fetch_kite_available_cash(user_id)  [Kite, 5s cached]
                  │
                  ▼ approves
   Cap 1..9 (existing) — strategy_id-scoped
                  │
                  ▼ approves
   budget.reserve()  ← writes state=PENDING
                  │
                  ▼
   kite_client.place_order()
                  │
                  ├─ success → budget.transition(SUBMITTED)
                  ├─ Kite rejects → budget.transition(REJECTED)
                  └─ network fail → stay PENDING; timeout job picks up

   Reconciliation loop (every 30s)
                  │
                  ▼
   For each SUBMITTED reservation:
        ├─ Kite says COMPLETE → transition(FILLED) + position created
        ├─ Kite says CANCELLED → transition(CANCELLED)
        └─ Kite silent > 5min → transition(TIMEOUT)
```

## UI surface

### Where it lives

**Top of the Live tab** in the Algo Trading page (`frontend/components/algo-trading/LiveTab.tsx`). A new `<BudgetPanel />` sits above the existing live controls (Active runs, Kill switch, etc.) — first thing the user sees on Live because it's the foundational constraint.

### Budget Panel layout

```
┌─ Budget ──────────────────────────────────────────────────────────┐
│                                                                   │
│  Allocated         Open positions    Pending          Available   │
│  ₹1,00,000          ₹35,000           ₹8,500           ₹56,500    │
│  [Edit ✎]          (5 holdings)      (2 orders)       internal    │
│                                                                   │
│  ─ Kite wallet ─────────────────────────────────────              │
│  Available cash: ₹78,200  (broker side, refreshed 4s ago)         │
│  ⓘ Live gate uses min(internal, Kite) = ₹56,500                   │
│                                                                   │
│  ┌─ Active reservations (2) ───────────────────────────────────┐  │
│  │ TICKER       SIDE  QTY  RESERVED ₹  STATE      AGE          │  │
│  │ INFY.NS      BUY   50   ₹7,500     SUBMITTED  12s   ✖      │  │
│  │ HDFCBANK.NS  BUY   10   ₹1,000     PENDING    3s            │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  [View reservation history →]                                     │
└───────────────────────────────────────────────────────────────────┘
```

### Four tiles

| Tile | Source | Notes |
|---|---|---|
| **Allocated** | `algo.user_budget.allocated_inr` | User-editable via ✎ |
| **Open positions** | `sum_open_position_cost(user_id)` | Hover shows tickers + cost basis. Click opens existing positions modal. |
| **Pending** | `sum_active_reservations(user_id)` | Sum of `reserved_inr − filled_inr` across active reservations |
| **Available** | `internal_headroom` (= Allocated − Open − Pending) | Bold; primary number. Hover shows breakdown formula + Kite figure. |

### Kite wallet strip

Shows `kite.margins.equity.available.cash` separately with refresh-age stamp. Includes the **"Live gate uses min(internal, Kite)"** explainer with the actual min highlighted. Makes it visible when Kite is the binding constraint (e.g., manual trades reduced wallet below internal headroom).

### Active reservations table

- Auto-refreshes every 3s while reservations are active (SWR `refreshInterval`).
- Stops polling when count drops to 0.
- Per-row "✖" = manual force-release (admin/owner only). Inline confirm: "Force-release ₹X reservation? This will not cancel the Kite order if it has already been submitted."
- Color coding: blue (PENDING), indigo (SUBMITTED), amber (PARTIAL).

### Edit Allocation modal

```
┌─ Edit Algo Budget Allocation ─────────────────────────┐
│ Total Kite wallet:  ₹2,15,000  (read-only)            │
│ Algo allocation:    [₹1,00,000     ▲▼]                │
│                      (max ₹2,15,000)                  │
│                                                       │
│ ⚠ Algo will not place orders that would exceed this   │
│   limit. The remainder ₹1,15,000 stays free for       │
│   manual trading on Kite.                             │
│                                       [Cancel] [Save] │
└───────────────────────────────────────────────────────┘
```

- Input in ₹, free-form numeric. Max = Kite wallet (no over-allocation).
- Decreasing below committed shows red warning: "You currently have ₹Y committed; reducing below this means no new orders until existing positions close. Continue?"
- Saves call `PUT /v1/algo/budget/allocation` with the new value.

### Empty / first-time state

If `algo.user_budget` has no row OR `allocated_inr=0`:

```
┌─ Budget (not configured) ─────────────────────────────┐
│  ⚠ Algo trading is paused — no budget allocated.      │
│  Set an algo allocation before enabling any strategy  │
│  for live trading.                                    │
│                       [Allocate budget]               │
└───────────────────────────────────────────────────────┘
```

Until `allocated_inr > 0`, all live BUY orders are rejected with `LIVE_BUDGET_CAP` (headroom = 0).

### Reservation history view

"View reservation history →" link opens a modal (preserves parent state). Columns: timestamp, ticker, side, qty, state transition, reserved_inr, filled_inr, kite_order_id, error_text. Filterable by state, ticker, date range. CSV export per CLAUDE.md §5.4's tabular-page pattern.

### Error states

| Condition | UI |
|---|---|
| Kite API down | "Kite wallet" row shows "—" with amber "Kite unreachable; using internal headroom only" badge |
| Internal PG down | Whole panel shows red "Budget unavailable" banner; falls back to existing Live tab without budget gating |
| Reservation timed out | Row disappears from "Active" table; surfaces in history with red badge |
| New rejection due to budget cap | Existing reject-event toast in Live tab shows "Order rejected: budget cap — needed ₹X, headroom ₹Y" |

### Toast at critical headroom

When **Available** drops to ≤ 10% of Allocated, a one-time toast: *"Algo budget headroom below 10%. Future orders may be rejected until positions close."* Stored in localStorage so it doesn't repeat once dismissed for the session.

### Mobile / responsive

Tiles stack vertically below 640px. Reservations table becomes vertical card list. Edit modal goes full-screen per CLAUDE.md §5.6 modal patterns.

### Testids (E2E)

```
budget-panel
budget-tile-allocated
budget-tile-open-positions
budget-tile-pending
budget-tile-available
budget-tile-edit-button
budget-kite-wallet-row
budget-active-reservations-table
budget-reservation-row-<reservation_id>
budget-force-release-button-<reservation_id>
budget-allocation-modal
budget-allocation-input
budget-allocation-save-button
budget-reservation-history-link
```

## Testing strategy

### Backend unit tests

| Module | Tests |
|---|---|
| `budget_types.py` | Pydantic round-trip; `ReservationState` enum; rejects negative `allocated_inr` |
| `budget_repo.py` | Upsert user_budget; insert reservation event; `current_state_for(reservation_id)` returns latest; `sum_active_reservations` excludes terminal states |
| `budget.py` (4 helpers) | `load_user_budget` returns `(0, False)` default; `sum_open_position_cost` from algo.events; `fetch_kite_available_cash` returns Decimal('inf') on Kite error; cache invalidation after every reservation insert |

### Backend safety-gate tests

`backend/algo/live/tests/test_budget_gate.py`:

- `test_cap0_approves_under_headroom`
- `test_cap0_rejects_when_internal_exhausted`
- `test_cap0_rejects_when_kite_exhausted`
- `test_cap0_min_uses_kite_when_lower`
- `test_cap0_min_uses_internal_when_lower`
- `test_cap0_sell_bypasses_gate`
- `test_cap0_fail_open_when_kite_down`
- `test_cap0_blocks_when_allocation_zero`
- `test_cap0_metadata_in_reject` (carries all 5 fields)

### Backend reservation lifecycle tests

`backend/algo/live/tests/test_budget_reservation_lifecycle.py`:

- `test_pending_then_submitted_then_filled` — happy path; position created
- `test_pending_timeout_at_120s` — reconciliation forces TIMEOUT, releases reserved_inr
- `test_submitted_then_cancelled` — Kite reports cancellation → state=CANCELLED
- `test_partial_fill_then_full_fill`
- `test_partial_fill_then_remainder_cancelled`
- `test_concurrent_reservations_see_each_other` — two parallel `reserve()` calls; second sees first
- `test_force_release_admin_override`

### Backend HTTP route tests

`backend/algo/tests/test_budget_routes.py`:

- `test_get_budget_returns_pending_shape`
- `test_put_allocation_creates_row`
- `test_put_allocation_enforces_kite_max`
- `test_put_allocation_warning_when_below_committed`
- `test_get_reservations_active_only_default`
- `test_get_reservations_with_history`
- `test_force_release_requires_admin_or_owner`

### Frontend Vitest tests

`frontend/components/algo-trading/__tests__/BudgetPanel.test.tsx`:

- `renders four tiles with correct math`
- `renders empty state when allocated_inr=0`
- `kite wallet row shows refresh age`
- `kite wallet row badge when kite unreachable`
- `active reservations table renders rows`
- `force-release confirmation flow`
- `edit allocation modal opens with current value`
- `edit allocation modal warns when reducing below committed`
- `edit allocation modal rejects negative + non-numeric input`

`frontend/components/algo-trading/__tests__/useBudget.test.ts`:

- `setAllocation POSTs PUT and refreshes the hook`
- `useActiveReservations stops polling when count is 0`

### E2E (Playwright) smoke

`e2e/tests/frontend/algo-trading-budget.spec.ts`:

```
1. Superuser fixture, fresh PG (no algo.user_budget row)
2. Navigate to Algo Trading > Live tab
3. Assert empty state: "Algo trading is paused" + "Allocate budget" CTA visible
4. Click "Allocate budget" → modal opens
5. Type 50000 → Save
6. Modal closes; tiles show Allocated ₹50,000 / Open ₹0 / Pending ₹0 / Available ₹50,000
7. Trigger a budget reservation via direct route POST (test fixture)
8. Active reservations table renders 1 row
9. Click force-release ✖ → confirm → row removes
10. Available returns to ₹50,000
```

### Manual integration (first live day)

Pre-flight checklist documented in `docs/operational/algo-budget-pre-flight.md` (new):

- [ ] `algo.user_budget` row exists with `allocated_inr > 0`
- [ ] Kite wallet shows expected balance
- [ ] One small test order (₹1000) submitted manually via UI
- [ ] Budget Panel updates within 5s: Pending tile shows ₹1000
- [ ] Order fills → Open positions tile shows ₹1000, Pending drops to ₹0
- [ ] Available drops by ₹1000 (Allocated unchanged)
- [ ] Reconciliation log shows the lifecycle transitions

## Process / git

- Branch off `dev`; squash merge per CLAUDE.md §4.4 #27.
- Co-Authored-By: Abhay (mandatory per §4.4 #24).
- One PR per slice — likely 8 slices (migration + types, repo, helpers + reserve/release API, safety-gate Cap 0, reconciliation extension, HTTP routes, frontend types + hooks + panel shell, frontend allocation modal + reservations table + history modal + E2E).

## Open questions answered

| Decision | Choice |
|---|---|
| Allocation model | A2 — single user pool |
| Reconciliation with Kite | B3 — min of internal + Kite |
| Position valuation | C1 — entry cost basis |
| Persistence | Append-only event log |
| Reservation timeout | 120s PENDING / 30s reconcile / 5min hard SUBMITTED |
| SELL handling | Bypasses Cap 0 |
| Kite-down behaviour | Fail-open on Kite, fail-closed on internal |
