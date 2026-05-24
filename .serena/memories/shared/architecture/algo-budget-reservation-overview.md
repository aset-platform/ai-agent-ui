# Algo Order Budget Reservation (Epic A)

**Shipped:** 2026-05-24, PR #242, merge SHA `f734863` on dev.

## What it does

User-pool budget reservation ("ticketing") layer for live algo
trading. Every BUY order reserves notional against the user's
allocated INR; reservations stack in an append-only event log;
the pre-trade gate uses `min(internal_headroom,
kite_available_cash)` so manual trades + T+1 settlement holds +
MIS auto-square-off lag naturally reduce algo headroom.

## New surface

### PG tables
- `algo.user_budget` — mutable, one row per user. Columns:
  `user_id` (UUID, PK), `allocated_inr` (Numeric(14,2)),
  `enabled` (bool), `updated_at`, `updated_by`.
- `algo.budget_reservations` — append-only event log, one row
  per state transition per `reservation_id`. Partial index on
  active states `(user_id) WHERE state IN ('PENDING',
  'SUBMITTED', 'PARTIAL')`; secondary index on
  `(user_id, transitioned_at DESC)`.

### Reservation states
`PENDING → SUBMITTED → FILLED / REJECTED / CANCELLED / PARTIAL
/ PARTIAL_CANCELLED / TIMEOUT`. Current state = latest row by
`transitioned_at` for a given `reservation_id`.

### Cap 0 in `pre_trade_check`
New cap runs AFTER the kill-switch check but BEFORE the
existing per-strategy caps in
`backend/algo/live/safety.py::pre_trade_check`. SELL bypasses
(closes a position, releases capital). Computes:
```
internal_headroom = allocated_inr - open_pos_cost - active_reserved
headroom = min(internal_headroom, kite_available_cash)
reject LIVE_BUDGET_CAP if order_cost > headroom
```
- Fail-open on Kite (`fetch_kite_available_cash` returns
  `Decimal("inf")` on Kite-API failure so internal becomes the
  binding cap).
- Fail-closed on internal PG (any PG error rejects, doesn't
  silently approve).

`pre_trade_check` is now `async def` (was `def`); only call
site is `LiveRuntime._handle_signal`, updated with `await` +
`user_id=self._user_id`.

### HTTP routes
`/v1/algo/budget/*` — `GET` (current headroom + 5 fields),
`PUT /allocation` (sets allocated INR, warns if below
committed), `GET /reservations` (active by default,
`?include_history=true` returns full event log up to 500 rows
with metadata as dict), `POST /reservations/{id}/force-release`
(owner-only, 404 if not found, 403 if not owner).

### Frontend
`BudgetPanel` mounted at top of `LiveDashboard` (rose accent).
4 tiles: Allocated / Open positions / Pending / Available
(emerald accent). Kite wallet strip (amber when Kite
unreachable). Active reservations table with per-row
force-release inline confirm + history modal. Empty-state CTA
prompts allocation when `allocated_inr=0`.

## Key files

- `backend/algo/live/budget_types.py` — Pydantic types,
  `ReservationState` enum, `ACTIVE_STATES` /
  `TERMINAL_STATES` frozensets (`mem:iceberg-table-design-checklist`
  partial-index pattern).
- `backend/algo/live/budget_repo.py` — async PG CRUD.
  `sum_active_reservations(user_id)` filters `side='BUY'`
  (SELLs are audit-only, don't deduct from BUY headroom).
- `backend/algo/live/budget.py` — 4 cached helpers
  (`load_user_budget`, `sum_open_position_cost`,
  `sum_active_reservations`, `fetch_kite_available_cash` — 5s
  Redis TTL each); `reserve()` / `transition()` API.
- `backend/algo/live/safety.py` — Cap 0 block + module-level
  imports of the 4 helpers so tests can patch at
  `backend.algo.live.safety.<helper>`.
- `backend/algo/live/runtime.py::_submit_order` — calls
  `budget_reserve` before Kite `place_order`, transitions to
  SUBMITTED on success (try/except wrapped so a DB blip doesn't
  leave the ledger PENDING while a real order is in-flight) or
  REJECTED on failure (try/except wrapped — never masks the
  original Kite error).
- `backend/algo/live/budget_reconciliation.py` — periodic
  sweep: 120s PENDING timeout, 5min hard SUBMITTED timeout,
  state machine on Kite `order_history`.
- `backend/algo/jobs/algo_reconciliation.py` — drives
  `budget_reconcile()` UNCONDITIONALLY (before the
  market-hours and no-users early returns) so PENDING /
  SUBMITTED timeouts release capital even off-RTH.
- `backend/algo/routes/budget.py` — HTTP routes,
  lift-to-module-level `_impl` pattern (testable without
  HTTP harness). `_serialize_history_row` keeps metadata
  JSONB as a dict (NOT stringified via `str(dict)`).
- `backend/algo/paper/types.py` — `RejectReason.LIVE_BUDGET_CAP`
  enum value + `metadata: dict[str, Any]` field on
  `RiskDecision`.

## Tests

35 tests across `test_budget_types.py`, `test_budget_repo.py`,
`test_budget.py`, `test_budget_gate.py`,
`test_budget_reconciliation.py`, `test_budget_routes.py`.

29 pre-existing tests in `test_live_pre_trade_check.py` (27) +
`test_live_kill_switch.py` (2) updated for async + `user_id`
kwarg. Autouse fixture in `test_live_pre_trade_check.py` mocks
all 4 budget helpers with infinite headroom so existing
caps 1-9 stay isolated. `backend/algo/live/tests/conftest.py`
autouse mocks `runtime.budget_reserve` /
`runtime.budget_transition` for non-budget tests.

## Caching

5s Redis TTL on 3 helpers; cache keys:
- `cache:budget:user:{user_id}:open_pos_cost`
- `cache:budget:user:{user_id}:active_reserved`
- `cache:budget:user:{user_id}:kite_available`

All invalidated via `_invalidate_cache(user_id)` on every
`reserve()` / `transition()` write (after the PG commit).

## Out of scope (v1, deferred)

BO/CO orders, order modifications, per-strategy sub-pools, F&O
margin estimation, multi-currency, WebSocket push for
reservations.

## Spec / plan

- `docs/superpowers/specs/2026-05-24-algo-order-budget-reservation-design.md`
- `docs/superpowers/plans/2026-05-24-algo-order-budget-reservation.md`
