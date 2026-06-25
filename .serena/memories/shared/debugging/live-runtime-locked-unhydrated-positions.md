# Live runtime: locked-but-not-hydrated positions miss their GTTs on restart

## Symptom
After a backend restart, a position that was filled in the previous session has
no GTT and no `TrailingStopManager`. The position IS in `_ticker_locked` (saved
to PG `algo.runs.locked_tickers`) but NOT in `open_positions()`. `ensure_gtts`
never sees it.

## Root cause (two layers)

**Layer 1 — postback-during-shutdown:**
A BUY fills while the running LiveRuntime is stopping. The Kite postback webhook
calls `get_supervisor().get_live_runtime(user_id, strategy_id)` which returns `None`
because `task.done() == True`. So `on_buy_fill_trailing` is never called → no GTT
placed, no Redis state saved. The fill IS written to PG `live_orders_in_flight` via
`_reconcile_terminal_with_in_flight` (reads PG directly, no runtime reference).

**Layer 2 — Kite positions() API timing race:**
On intraday restarts (during market hours), `kite.positions()['net']` can return 0
rows even for recently-filled CNC positions. This causes `hydrate()` to return only
overnight holdings from `kite.holdings()`. Since `_load_trailing_state_from_redis`
and `_ensure_gtts_for_hydrated_positions` both only operated on hydrated positions,
even previously-present GTT Redis keys were not restored.

Combined: a ticker can be in `_ticker_locked` (from PG `locked_tickers` column) but
invisible to all GTT-placing logic.

## Fix (2026-06-24, commit 77a27fd)

**`_recover_unhydrated_positions()`** — new startup method called at step 4 of the
startup sequence (after all lock restoration, before Redis trailing restore):
- Finds tickers in `_ticker_locked` not in `open_positions()`
- Calls `caps_repo.get_filled_buys_from_previous_runs(user_id, strategy_id, run_id, look_back=10)`
- For each match: injects a synthetic BUY `Fill` into `_positions` (fee_rates_version="recovered")
- Logs `"recover_positions: re-injected TICKER.NS qty=N avg=P.PP (locked-but-not-hydrated; prev run in_flight)"`

**`_load_trailing_state_from_redis()`** — now iterates
`open_positions().keys() | _ticker_locked` (was only `open_positions()`). Ensures
tickers with Redis trailing state are restored even when hydration missed them.

**`get_filled_buys_from_previous_runs(look_back=10)`** (CapsRepo):
- Multi-hop scan: an intermediate run with empty `live_orders_in_flight` (e.g. a 38-minute
  run that submitted no orders) does not hide a fill from an earlier run.

## Known gap — fill_price overwrite
`_reconcile_terminal_with_in_flight` saves `fill_price` to PG in_flight on the COMPLETE
postback. But `_sync_fills_from_pg` (runs every 30s in the same session) only copies
`status="filled"` into the in-memory `_in_flight` entry — not fill_price. When
`update_in_flight` then runs, it overwrites PG with the incomplete memory state →
fill_price is lost. Only fills arriving in the final ~30s of a session reliably retain
fill_price. Recovery will NOT work for these tickers next restart.
Workaround: the fill still shows in `algo.events` `order_filled_live` payload; could
use that as a fallback source for fill_price in a future fix.

## How to detect
- `LiveRuntime hydration: ... positions=0 holdings=N` every restart during market hours
  → Kite timing race; watch for the WARNING log added in position_hydration.py
- `LiveRuntime: ticker locks restored: {TICKER.NS, ...}` without a matching
  `position_hydrated` or `gtt_placed` for the same ticker in the same session
- Redis `KEYS trailing:*` missing a ticker that has an open position on Kite

## Startup sequence (for reference)
```
1. open_positions() → _ticker_locked
2. _restore_ticker_locks_from_redis()
3. _restore_ticker_locks_from_pg()
4. _recover_unhydrated_positions()        ← new
5. _load_trailing_state_from_redis()      ← now uses ∪ _ticker_locked
6. _ensure_gtts_for_hydrated_positions()
```
