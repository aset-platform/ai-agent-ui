# Live runtime eval-gate bugs (fixed 2026-06-19)

## Bug 1 — Closed-bar path bypassed the time gate

### Symptom
Buy signals generated on `history[:-1]` (yesterday's close RSI) even at 15:21 IST,
when wall-clock was already past `_MIN_EVAL_TIME_IST=09:30`. Running-bar RSI was not
considered despite being past the gate.

### Root cause
The time gate check was nested *inside* the `elif` branch that only ran when
`_eval_entry_on_closed_bar` returned None. If yesterday's RSI ≤ threshold:
1. `closed_entry` fired → `signal = closed_entry`
2. `elif signal.side == "BUY"` never reached
3. Time gate was never evaluated → closed-bar always won

### Fix
Made the time gate the **outer** branch. Before gate → only closed bar path runs.
After gate → `_eval_entry_on_closed_bar` is never called; `signal` from full history
(compute_indicators on full history including running bar) flows straight through.

### Code
`backend/algo/live/runtime.py` — the block starting at
`if daily_realtime and is_flat and last_bar_is_today and not ticker_locked`.

---

## Bug 2 — Locked-ticker "daily entry on CLOSED bar" INFO spam every minute

### Symptom
`"daily entry on last CLOSED bar — acting now (not premature): ticker=SHAILY.NS"`
logged at INFO every single minute even though no order was placed (order already
in-flight, rejected downstream by `_ticker_locked` check).

### Root cause
`_ticker_locked` check (at line ~1789) came AFTER the INFO log at line ~1705. The
entire closed-bar eval block ran on every tick for locked tickers, producing noise
and wasted computation (though `_closed_entry_cache` did deduplicate the indicator calc).

### Fix
Added `bar.ticker not in self._ticker_locked` to the outer guard condition:
```python
if (
    daily_realtime
    and is_flat
    and last_bar_is_today
    and bar.ticker not in self._ticker_locked  # ← added
):
```
Locked tickers skip the entire block. No log, no eval, no rejected event.

---

## Bug 3 — dashboard_routes.py missing pandas import

### Symptom
500 error on `GET /v1/dashboard/chart/indicators` after rewriting `get_chart_indicators`
to splice today's live Kite bar before computing indicators.

### Error
`NameError: name 'pd' is not defined` at `dashboard_routes.py:1304`

### Root cause
`dashboard_routes.py` never imported `pandas` — the old code delegated all DataFrame
work to `compute_indicators()` internally. After the rewrite, the endpoint manipulated
DataFrames directly.

### Fix
Added `import pandas as pd` to module-level imports in `dashboard_routes.py`.

---

## Config correction
`ALGO_DAILY_MIN_EVAL_TIME_IST` was `09:30` in both `.env` and code default.
Changed to `14:20` in both to match the intended design (running-bar BUY only
valid in the last 10 min before NSE close).
