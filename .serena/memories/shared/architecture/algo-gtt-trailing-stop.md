# GTT Trailing Stop — v5 Architecture

## Overview

`rsi2_connors_daily_v5` replaces the SMA5 bar-close exit with a three-phase
Kite GTT trailing stop. The `TrailingStopManager` is a shared pure state machine
used across all three runtimes.

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
- `_recover_unhydrated_positions()` — added 2026-06-24; finds tickers in `_ticker_locked`
  NOT in `open_positions()`, reads filled BUY data from up to 10 previous runs'
  `live_orders_in_flight`, re-injects as synthetic fills so `_ensure_gtts` can protect them.
  Handles the postback-during-shutdown scenario: `get_live_runtime` returned None →
  no GTT/Redis saved → fill IS in PG in_flight → recovered on next restart.
- `_load_trailing_state_from_redis()` — iterates `open_positions() ∪ _ticker_locked`
  (NOT only `open_positions()` — fixed 2026-06-24) so tickers missed by the Kite
  `positions()` API timing race still have their managers restored from Redis.
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
- `_ratchet_all_gtts()` — evaluates WS HWM; STOP_UPDATED → delete+replace GTT; STOP_HIT → emergency limit SELL
- `_on_sell_fill_trailing(ticker)` — clears trailing state + Redis on COMPLETE SELL
- WS tick loop: lightweight `_ws_hwm` update per tick
- Time-stop path: cancels GTT before placing LIMIT SELL; emits `gtt_cancelled_for_time_stop`

### Postback routing (`backend/algo/routes/webhooks.py`)
- COMPLETE BUY + matched strategy → `rt.on_buy_fill_trailing(...)`
- COMPLETE SELL + matched strategy → `rt._on_sell_fill_trailing(ticker)`
- Bare Kite symbol (e.g. "INFY") → append ".NS"
- **If runtime is stopping** (task.done()): `get_live_runtime` → None → GTT not placed.
  `_reconcile_terminal_with_in_flight` still saves fill to PG in_flight.
  `_recover_unhydrated_positions` on next restart re-injects it. See `mem:shared/debugging/live-runtime-locked-unhydrated-positions`.

### PaperSupervisor
`get_supervisor().get_live_runtime(user_id, strategy_id)` → active LiveRuntime or None (mode≠live or task.done()).

## KiteClient GTT methods

`backend/algo/broker/kite_client.py`:
- `place_gtt(ticker, trigger_price, limit_price, qty, transaction_type="SELL") → int`
  strips `.NS`/`.BO`, uses `GTT_TYPE_SINGLE`, dry-run returns 0
- `delete_gtt(gtt_id)` — swallows all exceptions
- `get_gtts() → list[dict]`

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

## AST fields (RiskPerTrade)

```python
phase1_ratchet_trigger_pct: float | None = None
phase1_ratchet_new_stop_pct: float | None = None
trailing_trigger_pct: float | None = None
trailing_atr_multiplier: float | None = None
```

All four None → trailing disabled. Both `trailing_trigger_pct` AND `trailing_atr_multiplier`
must be set for trailing to be active (`_trailing_enabled = True`).

## v5 template defaults

`stop_loss_pct=5.0`, `phase1_ratchet_trigger_pct=2.0`,
`phase1_ratchet_new_stop_pct=3.0`, `trailing_trigger_pct=5.0`,
`trailing_atr_multiplier=1.5`, `max_holding_days=5`,
`cooldown_after_failed_exit_days=7`
