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
- `on_buy_fill_trailing(ticker, fill_price, qty)` — called from postback on COMPLETE BUY;
  builds manager from factor-cache ATR, places GTT, saves state to Redis, emits `gtt_placed`
- `_load_trailing_state_from_redis()` — restart recovery from Redis; called in `run()` after ticker-lock restore
- `_ensure_gtts_for_hydrated_positions()` — called in `run()` AFTER `_load_trailing_state_from_redis()`; covers Redis-miss cases (first promotion after deploy, accidentally deleted GTTs). For every open position NOT already in `_trailing_managers`:
  - Batch-fetches `kite.get_gtts()` and builds `kite_gtt_map[bare_symbol] → (gtt_id, trigger_price)`
  - Batch-queries `algo.events` for `order_filled_live` to determine `source` (algo vs manual)
  - Computes ATR via weekday-aware `range(8)` lookback; creates `TrailingStopManager`
  - If GTT exists on Kite: registers id, emits `trailing_stop_recovered` with `source` field
  - If no GTT: places via `kite.place_gtt(...)`, emits `gtt_placed` with `source=hydrated_algo|hydrated_manual`
- `_trailing_ratchet_loop()` — asyncio task aligned to 15m boundaries 09:15–15:25 IST;
  calls `_ratchet_all_gtts()` via `asyncio.to_thread`
- `_ratchet_all_gtts()` — evaluates WS HWM; STOP_UPDATED → delete+replace GTT; STOP_HIT → emergency limit SELL
- `_on_sell_fill_trailing(ticker)` — clears all trailing state + Redis on COMPLETE SELL
- WS tick loop: lightweight `_ws_hwm` update per tick (no GTT calls)
- Time-stop path: cancels GTT before placing LIMIT SELL; emits `gtt_cancelled_for_time_stop`

### Postback routing (`backend/algo/routes/webhooks.py`)
- COMPLETE BUY + matched strategy → `get_supervisor().get_live_runtime(uid, sid).on_buy_fill_trailing(...)`
- COMPLETE SELL + matched strategy → `rt._on_sell_fill_trailing(ticker)`
- Ticker in Kite postbacks is bare (e.g. "INFY") → append ".NS" when routing

### PaperSupervisor
`get_supervisor().get_live_runtime(user_id, strategy_id)` — returns active
LiveRuntime or None (mode≠live or task done → None).

## KiteClient GTT methods

`backend/algo/broker/kite_client.py`:
- `place_gtt(ticker, trigger_price, limit_price, qty, transaction_type="SELL") → int`
  - strips `.NS`/`.BO` suffix, uses `GTT_TYPE_SINGLE`, dry-run returns 0
- `delete_gtt(gtt_id)` — swallows all exceptions (already-triggered is fine)
- `get_gtts() → list[dict]`

GTT limit order = `trigger_price × (1 − gtt_limit_headroom_pct)` to absorb gap-downs.
`gtt_limit_headroom_pct` is stored in `algo.live_caps` (NUMERIC(5,4), default 0.01)
and read into `self._gtt_limit_headroom_pct` at `LiveRuntime.__init__`.
Configurable via Live Trading → Settings → "GTT limit buffer %" input (displayed as %, stored as decimal 0–0.10).

## Event vocabulary

| Event type | Emitted by | Payload keys |
|---|---|---|
| `gtt_placed` | `on_buy_fill_trailing` or `_ensure_gtts_for_hydrated_positions` | ticker, phase, entry_price, stop_price, limit_price, gtt_id, atr, source |
| `gtt_ratcheted` | `_ratchet_all_gtts` | ticker, phase, old_stop, new_stop, hwm, gtt_id_old, gtt_id_new |
| `gtt_cancelled_for_time_stop` | time-stop path | ticker, holding_days, gtt_id |
| `trailing_stop_recovered` | `_load_trailing_state_from_redis` or `_ensure_gtts_for_hydrated_positions` | ticker, phase, hwm, current_stop, gtt_id, source |

## AST fields (RiskPerTrade)

```python
phase1_ratchet_trigger_pct: float | None = None
phase1_ratchet_new_stop_pct: float | None = None
trailing_trigger_pct: float | None = None
trailing_atr_multiplier: float | None = None
```

All four None → trailing disabled, v3 flat-stop path used. Both
`trailing_trigger_pct` AND `trailing_atr_multiplier` must be set for trailing.

## v5 template defaults

`stop_loss_pct=5.0`, `phase1_ratchet_trigger_pct=2.0`,
`phase1_ratchet_new_stop_pct=3.0`, `trailing_trigger_pct=5.0`,
`trailing_atr_multiplier=1.5`, `max_holding_days=5`,
`cooldown_after_failed_exit_days=7`
