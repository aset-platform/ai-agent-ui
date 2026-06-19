# algo-per-ticker-cap

## Purpose
Prevents the LiveRuntime from placing multiple BUY orders on the same
ticker within or across sessions. The weight mechanism (0.2 × budget per
order) sizes individual orders but does not enforce diversification — without
this cap the same stock can be bought on every bullish bar until budget runs
out.

## Runtime state
`LiveRuntime._ticker_locked: set[str]` — internal tickers (e.g. `INFY.NS`)
with an active BUY (in-flight or open position). Held in memory; mirrored to
Redis and PG on every change.

## Gate location
`_on_bar_close` — fires BEFORE `signal_generated`, AFTER the MIS entry-cutoff
gate. Emits `signal_rejected` with `reason: "ticker_already_in_portfolio"` and
returns 0. Payload includes `in_flight` (bool) and `open_qty`.

Applies to BUY signals only. SELL signals always pass through.

## Lock lifecycle
| Event | Action |
|---|---|
| BUY submitted (`_submit_order`) | `_ticker_locked.add(ticker)` |
| SELL submitted (`_submit_order`) | `_ticker_locked.discard(ticker)` |
| Session teardown (finally) | PG final flush |

Note: unlock on SELL *submission* (not fill). The `existing_pos.qty > 0`
check in the gate still blocks premature re-BUY before the SELL fills in Kite.

## Startup restore — three sources (union)
1. **Kite hydration** — `_positions.open_positions()` qty > 0 (overnight / filled)
2. **Redis** — `cache:algo:live:locked:{user_id}:{strategy_id}` (TTL 24h)
3. **PG previous run** — `CapsRepo.get_locked_tickers_from_previous_run()`:
   reads `locked_tickers TEXT[]` + derives submitted (non-terminal) BUY tickers
   from `live_orders_in_flight` JSONB on the most-recent prior run for this
   strategy. Durable fallback when Redis is flushed.

## Persistence
- **Redis** sync: `_sync_ticker_lock_to_redis()` called on every lock/unlock.
  Key: `cache:algo:live:locked:{user_id}:{strategy_id}`. TTL 86400s.
- **PG** flush: `CapsRepo.update_locked_tickers()` — UPDATEs
  `algo.runs.locked_tickers TEXT[]` (migration `2026_06_18_tickers`).
  Runs every 30s via `_periodic_ticker_lock_flush()` task + once at teardown.

## Pyramiding
Binary cap (in/not in). Allowing re-entry based on budget headroom defeats
diversification. Future opt-in: `allow_pyramiding: true` AST flag per strategy.

## Files
- `backend/algo/live/runtime.py` — `_ticker_locked`, gate, helpers, startup
- `backend/algo/live/caps_repo.py` — `update_locked_tickers`,
  `get_locked_tickers_from_previous_run`
- `backend/db/migrations/versions/2026_06_18_add_locked_tickers_to_runs.py`
