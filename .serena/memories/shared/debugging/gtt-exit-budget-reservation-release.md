# GTT-triggered exits must release their budget reservation

## Symptom
Cap 0 (`LIVE_BUDGET_CAP`, the pool-wide budget gate — see
`mem:algo-budget-reservation-overview`) rejects a BUY with a
`threshold` far below the real, Kite-verified headroom. The
Budget panel's "Open Positions" figure is inflated relative to
the live-Kite-computed exposure shown elsewhere (e.g. a strategy's
"Currently committed" figure, which is computed straight from
`kc.positions()`/`kc.holdings()`).

## Root cause
`sum_open_position_cost()` (in `budget_repo.py`) nets a BUY's cost
basis out of "open" only when a matching `FILLED` SELL reservation
exists in `algo.budget_reservations` for the same ticker:

```
per ticker: open_qty = Σ filled_qty(BUY) − Σ filled_qty(SELL)
            cost = GREATEST(open_qty, 0) × avg_buy_price
```

Both GTT-triggered-exit code paths in `LiveRuntime` apply the fill
to the in-memory position tracker and emit `algo.events`
(`order_filled_live`, `gtt_triggered`) correctly, but neither wrote
a SELL row to the budget ledger:
- **Piece A** — `_ratchet_all_gtts` (15-min poll, detects a tracked
  `gtt_id` no longer in Kite's active set).
- **Piece B** — `_apply_gtt_triggered_sell_fill` (postback
  fallback, called when the Kite webhook can't match an in-flight
  order).

So a position closed via GTT stays "open" in the ledger forever,
silently overstating `open_pos_cost` and understating pool-wide
headroom for every strategy the user runs — not just the one that
closed. This is a distinct gap from
`mem:budget-reservation-filled-state-gap` (BUY reservations stuck
in SUBMITTED, understating cost) — this one is the SELL side never
being written at all, overstating cost.

## Fix
A shared async helper, called from both exit paths right after the
fill is applied:

```python
async def _release_budget_reservation_for_gtt_exit(
    self, *, ticker: str, qty: int, fill_price: float,
) -> None:
    """A GTT fill is detected post-facto -- go straight to FILLED,
    there's no PENDING/SUBMITTED phase to model."""
    try:
        reserved_inr = Decimal(str(qty)) * Decimal(str(fill_price))
        reservation_id = await budget_reserve(
            user_id=self._user_id, strategy_id=self._strategy.id,
            ticker=ticker, side="SELL", qty=qty,
            reserved_inr=reserved_inr,
            metadata={"mode": "live", "source": "gtt_exit"},
        )
        await budget_transition(
            reservation_id=reservation_id,
            new_state=ReservationState.FILLED,
            filled_qty=qty, filled_inr=reserved_inr,
        )
    except Exception:
        # Best-effort, logged loudly: a ledger blip must not
        # shadow the fill, but a missed release silently corrupts
        # Cap 0 for every strategy until manually corrected.
        ...
```

**Piece A is sync** (runs via `asyncio.to_thread`) — dispatch via
`asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=...)`,
the same pattern already used for the STOP_HIT emergency-SELL path
in the same method. Skip (log, don't block) when `self._loop` isn't
set — that only happens in a test/pre-`run()` context, and blocking
would risk a deadlock if accidentally called from the loop thread.

**Piece B's caller is already on the event loop** (the postback
webhook handler) — made the method itself `async def` and `await`
it directly; no threading needed.

## Idempotency
Both paths are gated on `self._gtt_ids`/`self._trailing_managers`
still tracking the ticker — whichever path processes a real fill
first pops that shared state, so the other path's lookup fails and
its whole block is skipped. This existing "whichever runs first
wins" design covers the new budget-release call too; no separate
dedup guard is needed.

## Data repair for already-orphaned reservations
If positions were already closed via GTT before this fix landed,
backfill directly: insert a new reservation row per orphaned
ticker with a fresh `reservation_id`, `state='FILLED'`,
`side='SELL'`, `filled_qty` matching the BUY's `filled_qty`. Source
the real sell price from `algo.events` `gtt_triggered`/
`order_filled_live` rows where available. Note the netting formula
above does NOT use the SELL row's `filled_inr` at all — only
`filled_qty` affects whether a ticker's cost correctly zeroes out —
so an approximate/placeholder sell price is harmless to the repair
itself, only to audit-trail accuracy.
