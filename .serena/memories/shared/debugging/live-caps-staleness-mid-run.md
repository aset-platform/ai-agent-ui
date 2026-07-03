# LiveRuntime caps values cached at __init__ go stale for the run

## Symptom
Editing a strategy's live caps mid-run via
`PUT /algo/live/caps/{strategy_id}` has no effect until the runtime
restarts. Concretely: adding a ticker to `allowed_tickers` while
live still produces `signal_rejected reason=ticker_not_allowed` for
that ticker; changing `gtt_limit_headroom_pct` doesn't change the
limit price on the next GTT placed.

## Root cause
`LiveRuntime.__init__` receives a `caps` dict once at startup. Any
code that reads `self._caps` directly, or caches a scalar derived
from it into a separate `self._x` attribute at `__init__`, is
reading a frozen snapshot for the runtime's entire lifetime — there
is no periodic caps re-poll independent of what a caller explicitly
triggers.

Two fields hit this independently:
- The allow-list gate read `self._caps.get("allowed_tickers")`
  directly.
- `self._gtt_limit_headroom_pct` was computed once from
  `caps.get(...)` at `__init__` and read by two sync GTT-placement
  call sites (`on_buy_fill_trailing`, `_ratchet_all_gtts` — both
  deliberately synchronous / no `await`, callable from a worker
  thread).

Notably, `_on_bar_close` *already* did a fresh per-signal PG read
(`current_caps = await caps_repo.get(...)`) for `max_inr`/
`max_orders_per_day` — those two correctly picked up mid-run edits
the whole time. The fresh read just ran too late in the function
(after the allow-list gate had already returned) to help the
allow-list case, and wasn't reused anywhere for the sync GTT paths.

## Fix
- Moved the fresh-caps read earlier in `_on_bar_close` (before the
  allow-list gate, before `signal_generated` is emitted — preserving
  "a disallowed ticker never appears as a generated signal") and
  pointed the allow-list check at it.
- Assigned the fresh read back onto the shared instance attribute
  (`self._caps = current_caps`), not just a local variable — this is
  what makes the sync GTT call sites' direct reads of
  `self._caps.get("gtt_limit_headroom_pct", ...)` see an up-to-date
  value without needing their own I/O.
- The 15-min GTT-ratchet loop (which doesn't run through
  `_on_bar_close`) refreshes `self._caps` from a fresh PG read
  immediately before each tick, since that's its only cadence.

## General rule
Any new caps field read from a `LiveRuntime` hot path must either:
1. Read `self._caps` directly (never cache into a separate
   `self._x` attribute at `__init__`), or
2. If the read site is synchronous and genuinely can't await, make
   sure it also reads `self._caps` (not a private cache) and confirm
   `self._caps` itself is refreshed somewhere on a cadence tight
   enough for the field in question — wire into one of the two
   existing refresh points (`_on_bar_close`'s fresh-caps read, the
   ratchet loop's pre-tick refresh) rather than adding a third,
   easy-to-forget refresh path.
