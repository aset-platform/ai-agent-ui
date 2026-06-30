# Kite LIMIT order rejected: tick size mismatch

## Symptom
`rejection_reason: "Tick size for this script is 0.01/0.10. Kindly enter price in the multiple of tick size for this script"`

## Root cause
`runtime.py _submit_order` hardcoded `tick = Decimal("0.05")` for all NSE stocks.
Most largecap stocks use 0.05 but many mid/smallcap scripts (e.g. LAURUSLABS, many pharma names)
use 0.10. Rounding to 0.05 can produce a price like ₹450.05 which is not a multiple of 0.10.

GTT `place_gtt` call sites computed `limit = stop * (1.0 - headroom_pct)` in raw float
arithmetic — never tick-aligned. Kite rejects both `trigger_price` and `limit_price`
that are not multiples of the script's tick size.

## Fix (2026-06-19) — _submit_order
Added per-symbol tick size cache alongside the existing freeze-qty cache:

- `redis_keys.py`: `build_tick_size_key()` → `kite:tick_size:{date_ist}` Redis hash
- `freeze_cache.py`: `_refresh_freeze_map()` now also populates the tick-size hash from the
  SAME `kite.instruments("NSE")` call (no extra HTTP round-trip). Added `get_tick_size(kc, redis_client, symbol) → Decimal` with same Redis → SDK → default(0.05) fallback pattern as `get_freeze_qty`.
- `runtime.py _submit_order`: replaced `tick = Decimal("0.05")` with
  `tick = await asyncio.to_thread(get_tick_size, kc=self._kite, redis_client=self._kite._get_redis(), symbol=symbol)`

## Fix (2026-06-29) — all three GTT paths
`_submit_order` was fixed but ALL three `place_gtt` call sites were missed:

| Method | sync/async | Notes |
|---|---|---|
| `on_buy_fill_trailing` | sync | initial GTT after BUY fill |
| `_ratchet_all_gtts` | sync (called via `asyncio.to_thread`) | 15-min ratchet loop |
| `ensure_gtts` | async | session-restart re-hydration |

Pattern applied at each site (call `get_tick_size` directly — sync, Redis ≈ 1ms):
```python
from decimal import ROUND_DOWN, Decimal
_tick = get_tick_size(kc=self._kite, redis_client=self._kite._get_redis(), symbol=ticker)
stop = float(
    (Decimal(str(mgr.current_stop)) / _tick)
    .quantize(Decimal("1"), rounding=ROUND_DOWN) * _tick
)
limit = float(
    (Decimal(str(mgr.current_stop))
     * (1 - Decimal(str(self._gtt_limit_headroom_pct)))
     / _tick
    ).quantize(Decimal("1"), rounding=ROUND_DOWN) * _tick
)
```

Round **DOWN** for both: trigger fires slightly earlier (conservative), limit ensures fill.

## Important
- The `symbol` passed is the tradingsymbol WITHOUT `.NS` suffix (e.g. `LAURUSLABS`, not `LAURUSLABS.NS`).
  That matches the key in the Kite instruments response.
- Default fallback is `0.05` — orders for 0.01-tick stocks will still fail if Redis/SDK
  unavailable, but won't silently corrupt.
- `get_tick_size` is sync; call directly in sync methods, or via `asyncio.to_thread` in async
  if the call is on a hot path (GTT sites call it directly — acceptable latency).
