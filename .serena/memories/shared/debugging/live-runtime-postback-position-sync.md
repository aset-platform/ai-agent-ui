# Live runtime: duplicate SELL signals after a holding is sold

## Symptom
After a real Kite SELL order fills (CNC holding from previous day), the signal engine
regenerates a SELL on the next bar. Rejected as `signal_rejected` or tick-size error.

## Root cause
The Kite postback webhook (`/webhooks/kite/postback → _reconcile_terminal_with_in_flight`)
updates `algo.runs.live_orders_in_flight` in PG and writes `order_filled_live` to Iceberg —
but it has **no reference to the running `LiveRuntime` instance**. So `self._positions.apply_fill()`
is never called for real fills. Only dry-run fills (`_synthetic_fill`) update `_positions` inline.

On the next bar: `existing_pos = self._positions.open_positions().get(bar.ticker)` still shows
the holding → SELL signal generated again.

## Fix (2026-06-19)
Added `LiveRuntime._sync_fills_from_pg()`:
- Reads `live_orders_in_flight` from PG via `self._caps_repo.get_in_flight(user_id, run_id)`
- For each entry where PG status is `filled` but in-memory `_in_flight` entry is still `submitted`,
  calls `_positions.apply_fill(fill)` with a `Fill` built from the PG entry's qty + fill_price
- For SELL fills: calls `self._ticker_locked.discard(ticker)` so the ticker is re-enterable
- Mirrors status into in-memory `_in_flight` entry so the next sync skips it
- Calls `_sync_ticker_lock_to_redis()` after processing

`_periodic_budget_reconcile` was restructured to run:
- `_sync_fills_from_pg()` every **30 s** (half-tick)
- Full budget reconcile (`budget_reconciliation.reconcile()`) every **60 s** (full-tick)

## Pattern
This is a pull-based reconciliation: the webhook is the write path (PG), the runtime is
the consumer (poll). No new infrastructure needed — same pattern as budget reconcile.

## Currently committed count
`_compute_strategy_commitment` queries Kite's live `kc.holdings()` + `kc.positions()` on every
request (no Redis cache). It self-corrects the moment Kite reflects the fill — no special handling
needed. If shows stale values, it means the sell is not yet through Kite.
