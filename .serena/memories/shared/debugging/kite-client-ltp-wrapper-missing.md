# KiteClient has no ltp() wrapper

## Problem

`KiteClient` (at `backend/algo/broker/kite_client.py`) wraps the raw
`kite_connect.KiteConnect` instance as `self._kc`, but does NOT expose
an `ltp()` method. Calling `self._kite.ltp(...)` raises `AttributeError`,
which is silently swallowed by `except Exception` in any block with
broad error handling → `last_price = None` → downstream crash or
silent bad-price path.

## Root cause

`kite_client.py` exposes `quote()`, `positions()`, `holdings()`,
`place_order()`, `place_gtt()`, `delete_gtt()`, `get_gtts()`,
`historical_data()` etc., but `ltp()` was never wrapped.

## Fix

Call the raw Kite Connect client directly:

```python
# WRONG — AttributeError, silently caught
ltp_raw = await asyncio.to_thread(self._kite.ltp, ["NSE:INFY"])

# CORRECT
ltp_raw = await asyncio.to_thread(self._kite._kc.ltp, ["NSE:INFY"])
ltp_val = ltp_raw.get("NSE:INFY", {}).get("last_price")
```

Response format: `{"NSE:SYMBOL": {"instrument_token": int, "last_price": float}}`

## Where this bit us

`LiveRuntime.user_exit_position()` fetched LTP to price the limit SELL.
`self._kite.ltp()` raised `AttributeError` → caught → `last_price=None`
→ ws_hwm was also None (ticker not normalized to .NS) → `ValueError:
no valid price` → 400 error shown in the Exit confirm modal.

Both fixes landed together: ticker normalization + `_kc.ltp()`.
See `mem:shared/architecture/algo-user-exit-position` for full context.
