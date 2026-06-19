# Kite LIMIT order rejected: tick size mismatch

## Symptom
`rejection_reason: "Tick size for this script is 0.10. Kindly enter price in the multiple of tick size for this script"`

## Root cause
`runtime.py _submit_order` hardcoded `tick = Decimal("0.05")` for all NSE stocks.
Most largecap stocks use 0.05 but many mid/smallcap scripts (e.g. LAURUSLABS, many pharma names)
use 0.10. Rounding to 0.05 can produce a price like ₹450.05 which is not a multiple of 0.10.

## Fix (2026-06-19)
Added per-symbol tick size cache alongside the existing freeze-qty cache:

- `redis_keys.py`: `build_tick_size_key()` → `kite:tick_size:{date_ist}` Redis hash
- `freeze_cache.py`: `_refresh_freeze_map()` now also populates the tick-size hash from the
  SAME `kite.instruments("NSE")` call (no extra HTTP round-trip). Added `get_tick_size(kc, redis_client, symbol) → Decimal` with same Redis → SDK → default(0.05) fallback pattern as `get_freeze_qty`.
- `runtime.py _submit_order`: replaced `tick = Decimal("0.05")` with
  `tick = await asyncio.to_thread(get_tick_size, kc=self._kite, redis_client=self._kite._get_redis(), symbol=symbol)`

## Important
The `symbol` passed is the tradingsymbol WITHOUT `.NS` suffix (e.g. `LAURUSLABS`, not `LAURUSLABS.NS`).
That matches the key in the Kite instruments response.
