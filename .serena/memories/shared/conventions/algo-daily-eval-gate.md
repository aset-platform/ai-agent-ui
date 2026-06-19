# Daily strategy eval-time gate (ALGO_DAILY_MIN_EVAL_TIME_IST)

## What it is
IST wall-clock cutoff that decides **which history slice** drives BUY entry evaluation.
Exits (stop-loss, time-stop, discretionary SELL) always fire on full history — never gated.

## Production default: 14:20
History:
- Originally 14:30, changed to 09:30 on 2026-06-17 (theory: RSI settled by open bar)
- Changed back to **14:20** on 2026-06-19 after discovering the gate was broken anyway
  (see bug below). 14:20 = 10 min before NSE close; lets the day's trend fully establish.

## Env var
```
ALGO_DAILY_MIN_EVAL_TIME_IST=14:20   # .env + code default
```

## Correct semantics (as of 2026-06-19 fix)

| Wall-clock | BUY evaluation | SELL evaluation |
|---|---|---|
| Before 14:20 IST | `history[:-1]` — yesterday's closed bar only | Full history always |
| At/after 14:20 IST | Full history — today's running bar included | Full history always |

## ⚠️ Critical bug fixed 2026-06-19
**Old (broken) implementation** — the time gate was *nested inside* the closed-bar branch:
```python
closed_entry = _eval_entry_on_closed_bar(history[:-1])  # always called first
if closed_entry.side == "BUY":
    signal = closed_entry   # fired regardless of wall-clock
elif signal.side == "BUY":
    if wall_clock < gate:   # gate only reached when closed_entry = None
        defer
```
Result: if yesterday's RSI ≤ threshold, it bought on `history[:-1]` even at 15:21 IST —
the time gate was completely bypassed.

**New (correct) implementation** — gate is the outer decision:
```python
if wall_clock < gate:
    # Only closed bar can trigger BUY
    closed_entry = _eval_entry_on_closed_bar(history[:-1])
    if closed_entry.side == "BUY":
        signal = closed_entry
    elif signal.side == "BUY":
        defer (return 0)
# After gate: signal from full history flows through unchanged
```

## Second bug fixed 2026-06-19 — locked-ticker log spam
Before, if a ticker was in `_ticker_locked` (order already in-flight), the closed-bar
eval block ran on every incoming tick, emitting `"daily entry on last CLOSED bar"` at INFO
every minute. The actual order was rejected downstream by the lock check — purely noise.

Fix: added `bar.ticker not in self._ticker_locked` to the outer guard condition so the
entire block is skipped when the ticker already has an order in-flight.

## Code location
`backend/algo/live/runtime.py`:
- `_parse_ist_time()` — parses env var, fallback `time(14, 20)`
- `_MIN_EVAL_TIME_IST` — module-level constant (~line 124)
- `_eval_entry_on_closed_bar()` — closed-bar entry eval with `_closed_entry_cache`
- Outer gate block — `if daily_realtime and is_flat and last_bar_is_today and not ticker_locked`
