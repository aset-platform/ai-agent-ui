# Live-Trading Path Code Review — 2026-06-24

Scope: real-money path only — `backend/algo/live`, `algo/broker`, `algo/paper`,
`algo/stream`, `algo/runtime`, `algo/sizing`, `_iceberg_retry.py`, `fees.py`
(~16k LOC). Dimensions: quality, logical gaps, memory leaks, scale. Reviewed by
8 parallel agents; findings deduplicated below. Confidence labels carried from
the reviewers (confirmed / likely / suspected).

**Counts (deduped): 5 Critical · 15 High · ~14 Medium · ~12 Low.**

---

## Cross-cutting themes (the patterns to fix at the root)

- **A. Safety gates fail OPEN, not CLOSED.** Kill switch (Redis down → not
  armed), broker-cash cap (Kite error → `Decimal("inf")`), `pre_trade_check`
  unguarded I/O, `dry_run_flag` write failure → env default, dedup gate
  fail-open, risk NaN → comparison False. For real money every gate must fail
  closed. **Most systemic + most dangerous.**
- **B. Non-atomic / TOCTOU money accounting.** Reserve vs check not atomic +
  5s stale cache; `transition()` orders by wall-clock with no terminal guard;
  `committed_inr` ignores in-flight reservations; global cost-basis netting.
  → capital over-deploys beyond the allocated pool.
- **C. Paper ≠ Live semantics.** Unrealised P&L = 0, equity excludes open
  positions, zero-slippage full fills, qty=0 silent drops. → a strategy looks
  safe in paper but behaves differently live; corrupts the promotion gate.
- **D. Silent failures / swallowed exceptions.** qty=0 drops with no event,
  `except (CancelledError, Exception): pass`, WS thread death on one bad tick,
  missing `exc_info=True`. Repeated §4.2 #10 violations.
- **E. Restart / hydration gaps.** GTT-less BUY via fill-sync, `opened_at`
  reset → time-stop never fires, zero entry-price GTT, reconciliation
  loop-binding, kill-switch / risk-state not refreshed post-restart.
- **F. Blocking I/O on event loops / scale.** `kite.get_positions()` on the
  loop, per-bar sync Iceberg reads, per-tick sync Redis writes on the WS
  thread, per-order serial reconciliation, throttle `sleep` under a lock.
- **G. Unbounded memory in long-lived daemons.** runtime caches, paper caches,
  resampler open-bars, WS backpressure dicts, untracked gap-fill tasks.
- **H. Idempotency / dedup.** Duplicate real orders (chunk loop), duplicate
  bars (append-only writer), tick-size rejections.

---

## CRITICAL — block live promotion until fixed

1. **Multi-chunk order partial-failure → duplicate real orders.**
   `broker/kite_client.py:1045-1071`. Freeze-split loop has no try/except; if
   chunk N raises after chunks 0..N-1 are live, the exception propagates and the
   caller retries the full qty → double exposure. Dedup gate is qty-keyed +
   fail-open so it doesn't save you. *confirmed.*
2. **No tick-size rounding on order/GTT prices** despite a ready `get_tick_size`
   helper. `kite_client.py:1137-1138, 1819, 1836-1846`. Sub-tick LIMIT prices →
   NSE rejects → missed entries; sub-tick GTT trigger → protective stop never
   placed → naked position. *confirmed.*
3. **Kill switch fails OPEN when Redis unavailable.**
   `paper/kill_switch_repo.py:30-41`. `is_active()` returns `False` on Redis
   error or `None` client; durable PG `algo.kill_switch.active` never consulted
   at trade time. A deliberately-halted strategy resumes placing real orders.
   *confirmed.*
4. **Budget reserve/check is non-atomic (TOCTOU) + 5s stale cache.**
   `safety.py:177-220` reads headroom; reservation row not written until
   `runtime.py:2970`. No lock / FOR UPDATE / compare-and-insert. Concurrent
   BUYs (same user, multiple strategies/tickers) both pass → over-deploy beyond
   `allocated_inr`. `committed_inr_now` also ignores in-flight reservations
   (`runtime.py:2718-2725`). *confirmed.*
5. **`_sync_fills_from_pg` applies a BUY fill but never places the protective
   GTT / trailing manager.** `runtime.py:738-816`. The only GTT placer on a
   fresh BUY is the webhook postback (best-effort). If a BUY is synced here
   (postback miss / mid-session), the position is naked with no stop for the
   rest of the session. *confirmed.*

---

## HIGH

6. **`fetch_kite_available_cash` fails OPEN to `Decimal("inf")`** on any Kite
   error → broker-cash ceiling silently removed. Also `live_balance or cash`
   masks a true zero balance. `budget.py:200-218`. *confirmed.*
7. **`place_order` empty-string `order_id` = phantom success.**
   `kite_client.py:1149-1153`. Ambiguous SDK response → recorded as submitted
   with `kite_order_id=""`, untrackable/uncancellable. *confirmed.*
8. **Reconciliation uses loop-bound `get_session_factory()` under per-tick
   `asyncio.run()`** → "Future attached to a different loop" on first tick after
   restart; whole reconcile dies (swallowed to WARNING).
   `reconciliation.py:106,146`. *confirmed.*
9. **Blocking sync `kite.get_positions()` on the event loop** (no `to_thread`,
   no timeout) in reconciliation. `reconciliation.py:168`. *confirmed.*
10. **Order-timeout fill-during-cancel race** → emits `order_cancelled_timeout`
    for an order that actually filled; position becomes invisible to both
    cancel- and fill-accounting. `order_timeout.py:255-322`. *confirmed race.*
11. **Zero-price tick raises uncaught `ValidationError` → kills the WS callback
    thread**; all market data goes dark with no `ws_disconnected`.
    `ws_multiplexer.py:419-431` (`Tick.ltp` is `Field(gt=0)`). Common at
    pre-open / thin midcaps. *confirmed.*
12. **`transition()` latest-insert-wins by wall-clock, no terminal-state
    guard** → a later-timestamped TIMEOUT can overwrite FILLED (frees deployed
    capital) or resurrect CANCELLED. `budget.py:260-310`. *confirmed.*
13. **Restart-injected positions get `opened_at = today`** → `max_holding_days`
    time-stop never fires for any position that survived a restart.
    `runtime.py:1496-1507, 757-767`. *likely.*
14. **Ticker-lock released on SELL submission, not fill** → on a partial /
    rejected SELL the lock is gone while qty is still partly open → possible
    duplicate / oversized re-entry. `runtime.py:3120-3129`. *likely.*
15. **Unbounded per-ticker caches in the live runtime** (`_closed_entry_cache`,
    `_bars_by_ticker`, …) never pruned → slow leak in a multi-day daemon.
    `runtime.py:233,243,351,371`. *likely.*
16. **Paper daily-loss cap disabled (`daily_unrealised_pnl_inr=0`)** +
    **equity excludes open-position MV** + **zero-slippage full fills** →
    paper diverges from live and corrupts the promotion track record.
    `paper/runtime.py:1302-1306`, `paper/broker.py:37-58`. *confirmed.*
17. **Paper qty=0 silent drop with no event** (`set_target_weight`/composer →
    `None`) → promotion gate sees zero fills, undebuggable.
    `paper/runtime.py:1166-1167,1224-1248,1289`. *confirmed.*
18. **Paper `_events` flushed only at shutdown** → a crash loses every
    gate-critical fill event. `paper/runtime.py:198` + finally-only flush.
    *confirmed.*
19. **Dedup gate fail-open + qty-keyed** → the duplicate-order backstop
    disengages under exactly the Redis degradation when it's needed.
    `kite_client.py:1244-1273`. *confirmed.*
20. **Paper `rebuild_all` swallows per-user replay failures** (no `exc_info`) →
    a user can start trading on a stale/zeroed daily-loss counter.
    `replay_rebuilder.py:149-160`. *confirmed.*

---

## MEDIUM (selected)

- Per-bar synchronous Iceberg/DuckDB reads on the event loop at 15:25 bar-close
  (`runtime.py:2219-2244`) — stalls the loop / FastAPI under fan-out.
- Reconciliation does per-reservation `order_history` serially; should fetch
  `orders()` once/user. `budget_reconciliation.py:362-379`.
- `sum_open_position_cost` nets SELL proceeds globally + floors at 0 →
  under-counts deployed capital after a profitable close. `budget_repo.py:194-239`.
- STOP_HIT emergency SELL bypasses `_in_flight`/budget/lock/position update.
  `runtime.py:1217-1245`.
- MIS auto-square SELL uses stale `avg_price` as LTP and doesn't cancel the GTT
  first (double-exit risk). `runtime.py:1061-1093`.
- Gap-fill tasks `create_task`'d but never tracked/cancelled; overlap on
  reconnect flaps → Kite historical hammering. `ws_multiplexer.py:601-604`.
- Per-tick synchronous Redis `SET` on the WS thread at universe scale (no
  pipeline). `ws_multiplexer.py:431-439`.
- Gap-fill only resubscribes tokens with a prior `_last_tick_ns` → tokens that
  never ticked are never backfilled. `ws_multiplexer.py:606-618`.
- Vol-target / Kelly accept near-zero vol → oversizing to the cap (no vol
  floor). `sizing/vol_target.py:48-52`, `composer.py:71-81`.
- DD-throttle `int(capped * mult)` floors small positions to 0 during moderate
  drawdowns (silent). `sizing/composer.py:112-115`.
- Cash-floor cap (`% of NAV` compared to cash) over-truncates entries to 0 late
  in a deployment cycle (silent). `sizing/caps.py:46-53`.
- `bars_writer` append-only with no dedup → duplicate bars on restart/overlap.
  `stream/bars_writer.py:64-76`.
- Resampler never time-flushes or evicts open bars → quiet tickers' bars stall;
  per-key state never reclaimed. `stream/resampler.py:44-72`.
- Risk `gate()` NaN-poisoning → loss/exposure/concentration checks silently
  pass. `paper/risk_engine.py:75-79,122,150`.
- Intraday warmup reader: one NULL `bar_open_ts_ns` fails the WHOLE universe to
  per-ticker Kite fallback. `intraday_bar_warmup.py:233-246`.
- No Kite token-expiry (`TokenException`) handling; `get_gtts`/`delete_gtt`
  swallow all errors → "no stops" / silent no-op after daily token rollover.
  `kite_client.py:1860-1886`.
- `safety.py` `pre_trade_check` budget I/O unguarded → fail-open depends on
  caller. `safety.py:177-220`.
- Reconciliation is alert-only with no direction-aware escalation
  (broker>0 / ours=0 = untracked naked long treated as benign).
  `reconciliation.py:182-286`.
- Paper supervisor never reaps completed/crashed runs (`_on_done` doesn't pop)
  → memory retained + crashed run blocks re-arm + shows "completed".
  `paper/supervisor.py:84-123,215-228`.
- Paper `_emit_paper_budget_lifecycle` writes 3 PG rows/fill on the loop;
  paper reservation rows never pruned. `paper/runtime.py:74-165`.

---

## LOW (selected)

- `retry_iceberg_op` holds a process-wide lock across `time.sleep` backoff →
  serializes all writers under contention. `_iceberg_retry.py:58-77`.
- STT intraday-sell rate stale (0.025% vs 0.02% post-2024-10-01) → fee preview
  overstates intraday-sell STT. `fees.py` + `fee_rates.yaml`.
- `quote()` hardcodes `NSE:` → BSE `.BO` symbols silently dropped.
  `kite_client.py:597-600`.
- DD-throttle `peak <= 0` returns 0% DD → max aggression on a blown-up account.
  `sizing/drawdown_throttle.py:41-45`.
- Order-timeout 8-char strategy-id tag prefix → cross-strategy cancel collision
  risk. `order_timeout.py:173,259`.
- `is_market_open_ist` ignores NSE trading holidays. `reconciliation.py:293-308`.
- Pre-1980 epoch-date filter not explicit in warmup readers (mitigated by
  narrow windows today). `daily_bar_warmup.py`, `intraday_bar_warmup.py`.
- WS `_bp_last_emit_ns` not pruned on unsubscribe (slow leak).
  `ws_multiplexer.py:122-123,759`.
- Various `except (CancelledError, Exception): pass` swallows + missing
  `exc_info` in teardown paths (`runtime.py:1926-2024`, `ws_multiplexer.py:277`).
- `_NSE_DEFAULTS` module-level mutable dict (§4.2 #12). `freeze_cache.py:41-48`.

---

## What looked solid

- Known **qty-cap-uses-Kite-balance bug is FIXED** (`runtime.py:2727-2742`
  uses `max_inr − committed_inr_now`); live qty=0 now emits an explicit
  `signal_rejected` (the paper side still doesn't — finding 17).
- Mutable state correctly in PG (budgets, caps, drift, risk_state, kill_switch);
  Iceberg used append-only for events/bars.
- Budget lifecycle uses atomic `reserve()`/`transition()` API and
  `disposable_pg_session` (NullPool) — the gap is the *check/reserve* gap (4),
  not the primitive.
- Most order-path Kite calls correctly wrapped in `asyncio.to_thread`; event
  flush offloaded + re-buffered on failure.
- `order_timeout.py` loop hygiene (to_thread + per-tick exc_info + no-poison) is
  the model the reconciliation module should copy.
- Hydration T+1 handling, drift dedup, backpressure aggregation, WS reconnect
  backoff-with-stability-gate, fee model structure, resampler IST bucket
  alignment, fixture IDOR guards — all sound.

---

## Suggested remediation order (feeds the implementation plan)

1. **Fail-closed sweep** (theme A) — kill switch, broker cash, pre_trade_check,
   dry_run_flag, dedup, risk NaN guards. Cheap, high safety payoff.
2. **Order integrity** (criticals 1,2 + 7,19) — chunk-loop partial-failure +
   tick-size rounding + phantom-order-id + idempotency-keyed dedup.
3. **Budget atomicity** (critical 4 + 6,12 + medium netting) — single atomic,
   uncached, headroom-aware reserve transaction; terminal-state guard.
4. **Restart/GTT protection** (criticals 5 + 13, hydration zero-price) — place
   GTT/trailing on every BUY source; carry real `opened_at`; refuse zero-entry
   GTT.
5. **Reconciliation robustness** (8,9,10 + escalation) — disposable_pg_session,
   to_thread+timeout, fill-during-cancel handling, direction-aware escalation.
6. **WS resiliency** (11 + gap-fill, per-tick Redis) — guard bad ticks, track
   gap-fill tasks, batch LTP writes.
7. **Paper↔live parity** (16,17,18,20 + supervisor reap) — mark-to-market
   equity/unrealised, slippage model, qty=0 events, periodic event flush.
8. **Scale/memory cleanup** (per-bar reads, caches, resampler) — bound caches,
   thread blocking reads, time-flush bars.
