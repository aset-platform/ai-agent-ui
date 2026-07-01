# GTT Trailing Stop — Architecture

## Overview

Kite GTT trailing stop for live strategies. The `TrailingStopManager` is a
shared pure state machine used across all three runtimes.

## Three phases

| Phase | Trigger | Stop |
|---|---|---|
| `HARD_STOP` (1) | entry | `entry × (1 − stop_loss_pct/100)` |
| `RATCHETED` (15) | gain ≥ `phase1_ratchet_trigger_pct` | `entry × (1 − phase1_ratchet_new_stop_pct/100)` |
| `ATR_TRAIL` (2) | gain ≥ `trailing_trigger_pct` | `hwm − atr × trailing_atr_multiplier` |

Exit reasons: `phase1_stop`, `phase1_ratchet`, `trail_stop`.
Cooldown applies only to `phase1_stop` and `phase1_ratchet`, not `trail_stop`.

## State machine

`backend/algo/backtest/trailing_stop_manager.py`:
- `TrailingStopManager(risk, entry_price, atr, ticker)`
- `on_price_update(price) → TrailingEvent | None`
  - Returns `TrailingEvent(event_type="STOP_UPDATED")` when stop ratchets up
  - Returns `TrailingEvent(event_type="STOP_HIT")` when price ≤ current_stop
- `to_dict()` / `from_dict(d, risk)` — for Redis serialisation (48h TTL)
- Key: `trailing:{user_id}:{strategy_id}:{ticker}`

## Runtime wiring

### Backtest (`backend/algo/backtest/runner.py`)
Per-bar: LOW first (stop check), then HIGH (HWM advance). `_trailing_enabled`
flag gates the path (falls back to flat stop-loss if False).

### Paper (`backend/algo/paper/runtime.py`)
Same LOW/HIGH ordering per 15m bar. Dry-run logs GTT intents without calling Kite.

### Live (`backend/algo/live/runtime.py`)

**Startup sequence in `run()` (order matters):**
```
1. open_positions() → _ticker_locked      # hydrated positions already locked
2. _restore_ticker_locks_from_redis()
3. _restore_ticker_locks_from_pg()        # durable: prev-run locked_tickers column
4. _recover_unhydrated_positions()        # re-inject locked-but-not-hydrated fills
5. _load_trailing_state_from_redis()      # restores open_positions() ∪ _ticker_locked
6. _ensure_gtts_for_hydrated_positions()  # places GTTs for unmanaged positions
```

**Key methods:**
- `on_buy_fill_trailing(ticker, fill_price, qty)` — called from postback on COMPLETE BUY;
  builds manager from factor-cache ATR, places GTT, saves state to Redis, emits `gtt_placed`
- `_recover_unhydrated_positions()` — finds tickers in `_ticker_locked` NOT in
  `open_positions()`, reads filled BUY data from up to 10 previous runs'
  `live_orders_in_flight`, re-injects as synthetic fills so `_ensure_gtts` can protect them.
  Handles the postback-during-shutdown scenario: `get_live_runtime` returned None →
  no GTT/Redis saved → fill IS in PG in_flight → recovered on next restart.
- `_load_trailing_state_from_redis()` — iterates `open_positions() ∪ _ticker_locked`
  (NOT only `open_positions()`) so tickers missed by the Kite `positions()` API timing
  race still have their managers restored from Redis.
- `_ensure_gtts_for_hydrated_positions()` — called AFTER `_load_trailing_state_from_redis`;
  covers Redis-miss cases (first promotion, accidentally deleted GTTs, recovered fills).
  For every open position NOT in `_trailing_managers`:
  - Batch-fetches `kite.get_gtts()` → `kite_gtt_map[bare_symbol] → (gtt_id, trigger_price)`
  - Batch-queries `algo.events` for `order_filled_live` → `source` (algo vs manual)
  - ATR: weekday-aware `range(8)` factor-cache lookback; fallback Wilder ATR from daily bars;
    last resort 2% of entry price
  - GTT exists on Kite → register + emit `trailing_stop_recovered`
  - No GTT → place via `kite.place_gtt(...)` + emit `gtt_placed` with `source=hydrated_algo|hydrated_manual`
- `_trailing_ratchet_loop()` — asyncio task at 15m boundaries 09:15–15:25 IST
- `_ratchet_all_gtts()` — see section below
- `_on_sell_fill_trailing(ticker, reason)` — clears trailing state + Redis on COMPLETE SELL.
  `reason="set_target_weight"` → rebalancing trim, GTT preserved. All other reasons → full close.
- `_apply_gtt_triggered_sell_fill(ticker, fill_price, qty)` — applies synthetic SELL fill to
  `_positions`. Safe to call twice (SELL on already-closed position is no-op in PositionTracker).
  Called from postback GTT fallback (Piece B).
- WS tick loop: lightweight `_ws_hwm` update per tick
- Time-stop path: cancels GTT before placing LIMIT SELL; emits `gtt_cancelled_for_time_stop`

### `_ratchet_all_gtts()` — dual role

**HWM evaluation (existing):** STOP_UPDATED → delete+replace GTT; STOP_HIT → emergency limit SELL.

**GTT-triggered detection (Piece A — added 2026-07-01):** Once per 15-min tick, BEFORE the
HWM loop, calls `kite.get_gtts()` and builds the active-ID set. Any tracked `gtt_id > 0`
absent from the active set is treated as triggered:
- Applies synthetic SELL fill to `_positions`
- Pops `_trailing_managers` / `_gtt_ids` / `_ws_hwm`; releases `_ticker_locked`; syncs Redis
- Emits `gtt_triggered` with `source="gtt_poll"`
- Issues `continue` to skip HWM eval for that ticker (already handled)

Guard: `if not self._dry_run and self._gtt_ids:` — skipped in dry-run (all gtt_ids=0) and
when no GTTs tracked. `get_gtts()` failure caught+logged; detection defers to the next tick.

### Postback routing (`backend/algo/routes/webhooks.py`)

`_reconcile_terminal_with_in_flight` returns **`(matched_strategy_id, matched_entry)`** tuple.
Callers MUST unpack both. Using `matched_entry` as a free variable was a silent `NameError`
(swallowed by `except Exception`) that prevented `_on_sell_fill_trailing` from ever firing via
the webhook before this was fixed.

- **COMPLETE BUY + matched strategy** → `rt.on_buy_fill_trailing(...)`
- **COMPLETE SELL + matched strategy** → `rt._on_sell_fill_trailing(ticker, reason=matched_entry.get("reason"))`
- **COMPLETE SELL + no match (`matched_strategy_id=None`)** → GTT-triggered fallback (Piece B):
  1. `get_supervisor().find_live_runtime_with_gtt(user_id, ticker)` — walks active live
     entries in `PaperSupervisor._runs`, returns `(runtime, strategy_id)` for the entry
     whose `_gtt_ids` contains the ticker
  2. Captures `gtt_id` from `rt._gtt_ids.get(ticker, 0)` BEFORE cleanup
  3. `rt._apply_gtt_triggered_sell_fill(ticker, fill_price, qty)`
  4. `rt._on_sell_fill_trailing(ticker, reason="gtt_triggered")` — deletes GTT on Kite,
     releases ticker lock, clears Redis
  5. Emits `gtt_triggered` with `source="postback"` attributed to the correct strategy
- Bare Kite symbol (e.g. "INFY") → append ".NS" before all runtime lookups
- **If runtime is stopping** (task.done()): `get_live_runtime` → None → GTT not placed.
  `_reconcile_terminal_with_in_flight` still saves fill to PG in_flight.
  `_recover_unhydrated_positions` on next restart re-injects it.

### Piece A vs Piece B — idempotency

Both paths clear the same state. Whichever runs first wins; the second is a no-op:
- If Piece B (postback) runs first: `_gtt_ids` becomes empty → Piece A's `if self._gtt_ids:`
  guard is falsy → detection skipped entirely next ratchet tick
- If Piece A (poll) runs first: `_trailing_managers` is empty → outer `for ticker, mgr in
  list(self._trailing_managers.items()):` has nothing to iterate → Piece B's
  `find_live_runtime_with_gtt` still finds the runtime but `_on_sell_fill_trailing` is a
  no-op (state already cleared)

Piece B is the primary handler (postback arrives within seconds). Piece A is the 15-min backup.

### PaperSupervisor
- `get_supervisor().get_live_runtime(user_id, strategy_id)` → active LiveRuntime or None
  (mode≠live or task.done())
- `get_supervisor().find_live_runtime_with_gtt(user_id, ticker)` → `(LiveRuntime, strategy_id)`
  or `(None, None)`. Duck-typed via `getattr(rt, "_gtt_ids", {})`.

## GTT fire + runtime restart recovery

After a GTT fires and the runtime is restarted: `kite.positions()` shows the ticker at
qty=0 (fill already executed by Kite). Hydration skips it → ticker is NOT in
`_trailing_managers` or `_gtt_ids` after startup. No stale state → no double-SELL risk.
The accounting gap (missing `gtt_triggered` event for the pre-restart session) is acceptable
— the exit P&L is correct on Kite's ledger. Do not attempt to backfill synthetically.

## KiteClient GTT methods

`backend/algo/broker/kite_client.py`:
- `place_gtt(ticker, trigger_price, limit_price, qty, transaction_type="SELL") → int`
  strips `.NS`/`.BO`, uses `GTT_TYPE_SINGLE`, dry-run returns 0
- `delete_gtt(gtt_id)` — swallows all exceptions
- `get_gtts() → list[dict]` — each dict has `"id"` (int) and `"status"` ("active"|"triggered"|…)

GTT limit = `trigger_price × (1 − gtt_limit_headroom_pct)`.
`gtt_limit_headroom_pct` in `algo.live_caps` (NUMERIC(5,4), default 0.01).
Configurable via Live Trading → Settings → "GTT limit buffer %".

## CapsRepo helpers

`get_filled_buys_from_previous_runs(user_id, strategy_id, current_run_id, look_back=10)`:
- Scans up to 10 prior live runs newest-first for status='filled' BUY entries with fill_price > 0
- Returns `dict[TICKER.NS → {fill_price, qty}]` — newest-first dedup
- Multi-hop: an intermediate run with empty in_flight does not hide older fills

**Known gap — fill_price overwrite:** `_reconcile_terminal_with_in_flight` saves fill_price
to PG in_flight. But `_sync_fills_from_pg` only copies `status="filled"` into the in-memory
entry (not fill_price). Next `update_in_flight` call overwrites PG with the incomplete memory
state → fill_price lost. Only fills that arrive in the final ~30s of a session (before the
next update_in_flight fires) reliably retain fill_price in PG.

## Event vocabulary

| Event type | Emitted by | Payload keys |
|---|---|---|
| `gtt_placed` | `on_buy_fill_trailing` / `_ensure_gtts_for_hydrated_positions` | ticker, phase, entry_price, stop_price, limit_price, gtt_id, atr, source |
| `gtt_ratcheted` | `_ratchet_all_gtts` | ticker, phase, old_stop, new_stop, hwm, gtt_id_old, gtt_id_new |
| `gtt_cancelled_for_time_stop` | time-stop path | ticker, holding_days, gtt_id |
| `trailing_stop_recovered` | `_load_trailing_state_from_redis` / `_ensure_gtts_for_hydrated_positions` | ticker, phase, hwm, current_stop, gtt_id, source |
| `gtt_triggered` | `_ratchet_all_gtts` (source=gtt_poll) or postback fallback (source=postback) | ticker, qty, stop_price, gtt_id, dry_run, source |

`gtt_triggered` feeds `LiveEventsPanel` (amber GTT-HIT badge) and `RecentFillsTape`
(merged alongside `order_filled_live` via two `usePaperEvents` SWR calls).

## Testing gotcha — `kite.dry_run` vs `kite._dry_run`

`LiveRuntime.__init__` sets `self._dry_run = bool(getattr(kite, "dry_run", False))` (no
underscore). `MagicMock().dry_run` returns a truthy MagicMock, so `self._dry_run = True`
by default. Tests that exercise the GTT detection block (gated by `if not self._dry_run`)
must set `kite.dry_run = False` (no underscore) or `rt._dry_run = False` directly after
construction. Setting `kite._dry_run = False` (underscore) has no effect.

## AST fields (RiskPerTrade)

```python
phase1_ratchet_trigger_pct: float | None = None
phase1_ratchet_new_stop_pct: float | None = None
trailing_trigger_pct: float | None = None
trailing_atr_multiplier: float | None = None
```

All four None → trailing disabled. Both `trailing_trigger_pct` AND `trailing_atr_multiplier`
must be set for trailing to be active (`_trailing_enabled = True`).
