# Live Caps Settings — algo.live_caps

Added / updated: 2026-06-23/24 on `feature/rsi2-exit-strategy`.

## Table: `algo.live_caps` (PK: user_id + strategy_id)

| Column | Type | Default | Notes |
|---|---|---|---|
| max_inr | NUMERIC(14,2) | 0 | Max ₹ notional per day |
| max_orders_per_day | INTEGER | 0 | Max orders (0 = no new orders) |
| allowed_tickers | JSONB | [] | At least 1 required to enable live |
| live_orders_enabled | BOOLEAN | false | Flipped by explicit enable/disable call |
| gtt_limit_headroom_pct | NUMERIC(5,4) | 0.01 | GTT limit = trigger × (1 − pct); configurable 0–10% |
| cumulative_inr_today | NUMERIC(14,2) | 0 | Reset at market open |
| orders_count_today | INTEGER | 0 | Reset at market open |

## CapsRepo (`backend/algo/live/caps_repo.py`)

- `get()` — SELECT includes `gtt_limit_headroom_pct`
- `get_or_default()` — default dict includes `gtt_limit_headroom_pct: Decimal("0.01")`
- `upsert(..., gtt_limit_headroom_pct=None)` — kwarg; falls back to 0.01 if None

## Route (`backend/algo/routes/live.py`)

- `UpsertCapsRequest.gtt_limit_headroom_pct: Decimal` — validated 0 ≤ x ≤ 0.10, default 0.01
- `CapsResponse.gtt_limit_headroom_pct: Decimal` — included in GET and PUT responses

## LiveRuntime (`backend/algo/live/runtime.py`)

`self._gtt_limit_headroom_pct = float(caps.get("gtt_limit_headroom_pct", 0.01))`
set in `__init__`. Used in 3 places:
- `_ratchet_all_gtts` (ratchet update)
- `on_buy_fill_trailing` (initial GTT on fill)
- `_ensure_gtts_for_hydrated_positions` (hydration GTT)

## Frontend

- `useLiveCaps.ts`: `LiveCaps.gtt_limit_headroom_pct: number`; `UpsertCapsPayload.gtt_limit_headroom_pct?: number`
- `LiveSafetyBeltsForm.tsx`: 4th column "GTT limit buffer %" input (0–10%, step 0.1%, stored as decimal)
  - Stored: `0.01` = 1%; displayed: `1.0` in input

## Migration

`2026_06_23_gtt_headroom` (`backend/db/migrations/versions/2026_06_23_add_gtt_limit_headroom_to_live_caps.py`)
