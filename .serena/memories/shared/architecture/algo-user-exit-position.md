# Manual Position Exit (user_exit_position)

User-initiated exit of a single live holding from the Holdings tab.
Cancels the active GTT, clears trailing state, places a LIMIT SELL
via the normal `_submit_order` path so budget/event/in-flight
accounting is fully preserved.

## Flow

```
POST /v1/algo/live/positions/exit
  → normalize ticker (.NS suffix)
  → get_supervisor().get_live_runtime(user_id, strategy_id)
  → await rt.user_exit_position(ticker, qty_override, price_hint)
      1. Resolve qty: _positions.open_positions().get(ticker) → else qty_override
      2. Capture ws_hwm + gtt_id BEFORE clearing state
      3. Cancel GTT via asyncio.to_thread(_kite.delete_gtt, gtt_id) — best-effort
      4. Pop _trailing_managers, _gtt_ids, _ws_hwm; invalidate Redis trailing cache
      5. Fetch LTP via asyncio.to_thread(_kite._kc.ltp, ["NSE:{bare}"])
      6. Price fallback chain: kite LTP → ws_hwm → price_hint (UI value)
      7. Build Signal(side="SELL", reason="user_exit") + await _submit_order(signal, last_price)
      8. Emit user_exit_initiated event + _flush_events_now()
  → invalidate cache:algo:live:holdings/positions/dashboard:{user_id}
  → return UserExitResponse
```

## Key design decisions

- **`reason="user_exit"` bypasses churn guard** — `"exit" in reason` → `is_protective=True` in `_submit_order`; the anti-churn guard is skipped.
- **`_ticker_locked` NOT manually cleared** — `_submit_order` with `side="SELL"` calls `_ticker_locked.discard(ticker)` on submission (not fill). Lock releases immediately when the SELL order is placed.
- **Trailing state cleared BEFORE `_submit_order`** — prevents the 15-min ratchet loop from re-placing the GTT between the delete and the SELL placement.
- **`_on_sell_fill_trailing` still fires via postback** — all pops are no-ops (state already cleared); only `_ticker_locked.discard` and `_sync_ticker_lock_to_redis` do real work on the postback path.
- **Idempotent with Piece A/B** — if GTT fires simultaneously, `_positions` is already empty → `user_exit_position` raises ValueError (no position to exit); or if exit runs first, ratchet sees no GTT → no-op.
- **`async def`** — called with `await` directly from the FastAPI route handler (same uvicorn event loop). No `asyncio.to_thread` or `run_coroutine_threadsafe` needed.

## Ticker normalization (critical)

Kite holdings/positions API returns bare tradingsymbols (`"EQUITASBNK"`).
Runtime state dicts (`_ws_hwm`, `_gtt_ids`, `_positions`) key on `"EQUITASBNK.NS"`.
Normalize at the route boundary — NOT inside `user_exit_position`:

```python
if "." not in ticker:
    ticker = ticker + ".NS"
```

## Price fallback chain

1. `_kite._kc.ltp(["NSE:{bare}"])` — freshest price (note: `KiteClient` has no `ltp()` wrapper; use raw `_kc`)
2. `ws_hwm` captured before state clear — last WS high-water mark
3. `price_hint` from request body — UI-displayed LTP sent by frontend as last resort
4. Raise `ValueError` if all three are None/0 → 400 from route

## Event types emitted

| Event | When |
|---|---|
| `user_exit_initiated` | Immediately after `_submit_order`, flushed at once |
| `order_submitted_live` | Inside `_submit_order` / `KiteClient.place_order` |
| `order_filled_live` | Via Kite postback when SELL fills, `reason="user_exit"` |

## UI

- Exit button visible only in HoldingsTab, only during NSE market hours (9:15–15:30 IST, Mon–Fri)
- Inline confirm popup shows ticker, qty, ≈price before sending
- `price_hint` (UI LTP) included in POST body as backend fallback
- `user_exit_initiated` badge (rose `USR-EXIT`) in LiveEventsPanel; `USR` micro-label in RecentFillsTape

## Files

- `backend/algo/live/runtime.py` — `user_exit_position()` (~L1860)
- `backend/algo/routes/live.py` — `POST /positions/exit`, `UserExitRequest`, `UserExitResponse`
- `frontend/components/algo-trading/live/UserExitButton.tsx`
- `frontend/components/algo-trading/live/HoldingsTab.tsx`
- `backend/algo/live/tests/test_user_exit.py` — 7 tests
