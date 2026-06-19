# Budget reservation stuck in SUBMITTED after Kite fill

## Symptom
`algo.budget_reservations` BUY reservation stays in state `SUBMITTED` even after
the Kite order fills. `Budget` panel shows wrong `open_pos_cost` (too low) because
FILLED BUY rows are missing from the ledger. If the backend restarts before the
60-second reconcile poll runs, the reservation is later TIMEOUT'd (losing the fill),
or (after the fix) correctly FILLED via the `filled_inr > 0` guard.

## Root cause (2026-06-19 investigation)
Three-layer gap:

1. **Postback webhook** (`routes/webhooks.py` `_reconcile_terminal_with_in_flight`):
   updates `algo.runs.live_orders_in_flight` to `status="filled"` and writes
   `order_filled_live` to Iceberg — but **never calls `budget_transition(FILLED)`**.

2. **`_sync_fills_from_pg`** (`runtime.py`, runs every 30 s): reads the in-flight
   JSONB and applies fills to the in-memory position tracker — but **also never called
   `budget_transition`** because `reservation_id` wasn't stored in the in-flight entry.

3. **Only path to FILLED was `reconcile_one()`** (budget reconciler, every 60 s),
   which polls Kite API. Kite drops order history after 1 trading day, so if the
   runtime restarted after a fill the reservation was TIMEOUT'd next day.

## Fix (runtime.py)
**Part 1** — store `reservation_id` in the in-flight entry at submit time
(in `_submit_order`, the `in_flight_entry` dict):
```python
"reservation_id": str(reservation_id) if reservation_id else None,
```

**Part 2** — `_sync_fills_from_pg`: after applying a fill, read `reservation_id`
from `pg_entry` and immediately call `budget_transition(FILLED, filled_qty,
filled_inr)`. Wrapped in try/except so reconciler acts as safety net.

## Fix (budget_reconciliation.py `reconcile_one`)
Safety net: if `status_row is None` (Kite history gone) **and** `filled_inr > 0`,
transition to `FILLED` instead of `TIMEOUT`. Prevents data loss on day-old orders.

## DB backfill (2026-06-18 incident)
GRANULES.NS (`1f368122`) and LAURUSLABS.NS (`e82019d7`) had `filled_inr > 0` but
state=`TIMEOUT`. Manually inserted FILLED rows → `open_pos_cost` corrected from
₹2,697 to ₹5,555.

## Key invariant
`sum_open_position_cost = FILLED BUY filled_inr − FILLED SELL filled_inr` (live
mode, floored at 0). Any BUY stuck in SUBMITTED/TIMEOUT with `filled_inr > 0`
understates open exposure.
