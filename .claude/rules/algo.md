---
paths:
  - "backend/algo/**"
  - "frontend/components/algo-trading/**"
  - "frontend/components/widgets/algo/**"
  - "frontend/app/**/algo-trading/**"
---

# Algo-trading rules (auto-loads when touching algo code)

> Lazy-loaded via `paths:` frontmatter — only enters context when Claude
> reads files under the globs above. Mirrors what was CLAUDE.md §5.16.

## ★ Change-impact discipline for runtime / live / order / GTT code

Any change touching `live/runtime.py`, `live/*.py`, order placement,
GTT placement, `routes/live.py`, `routes/kill_switch.py`,
`routes/webhooks.py` (postback), or `broker/kite_client.py` MUST be
evaluated for impact across the WHOLE surface, not just the call
site that prompted the change — before calling the fix done:

- **Grep every call site of the changed pattern across the entire
  backend**, not just the reported one. A bug found at one call site
  of a Kite SDK call, a budget-reservation write, or a caps read is
  reason to suspect siblings. (2026-07-03: a reported "₹0 committed"
  display bug traced to `kiteconnect` swallowing a Content-Type
  mismatch — the full sweep found 9 unprotected call sites, not the
  1 originally reported, including the kill-switch's emergency
  flatten-all and the GTT-cleanup fail-safe.)
- **Trace BOTH sides of any dual-path / idempotent mechanism.** This
  codebase's GTT-exit accounting is deliberately two-path (Piece A
  poll + Piece B postback, "whichever runs first wins") — a fix
  applied to one path and not the other silently half-fixes the bug.
  (2026-07-03: neither path released the matching budget reservation
  on a GTT-triggered exit — a gap that existed in both paths for as
  long as GTT-triggered exits have existed, undetected because
  nothing crashed or logged an error.)
- **Check every piece of downstream state the change touches stays
  consistent**, not just the one attribute you're directly editing:
  budget ledger (`algo.budget_reservations`), the in-memory caps
  snapshot (`self._caps`), the position tracker, `_in_flight` orders,
  `algo.events`. A fix that updates the position tracker + emits
  events but forgets the budget ledger looks complete (tests pass,
  the fill shows up in the UI) while silently corrupting a completely
  different subsystem's read of "how much capital is free."
- **The class of failure this discipline exists to catch is silent,
  not loud.** A gap here doesn't crash, doesn't fail a health check,
  and often doesn't even log at ERROR — it manifests as a signal
  that should have produced a BUY quietly producing nothing, or a
  budget figure that's wrong by exactly the amount of one orphaned
  reservation. Confirmed 2026-07-03: real trading opportunities
  during market hours went unmet because a stale budget figure
  rejected valid BUYs, discovered only because the user happened to
  be watching the console — not because anything alerted. Treat "the
  test I wrote passes" as necessary, not sufficient, for this class
  of code; the standard is "I traced every call site and every piece
  of state this touches," not "the one thing I changed works."

## Strategy promotion workflow

- Lifecycle: `draft → paper → live`. Audit table `algo.strategy_mode_transitions`.
- **Gates** (enforced by `promotion.check_eligibility`): draft→paper requires fresh completed backtest + walkforward (`started_at >= strategies.updated_at`); paper→live requires fresh paper-fill events in `algo.events` (paper runtime doesn't create algo.runs rows).
- **AST edits auto-demote** non-draft → draft (audit row `reason="auto-demoted on AST edit"`).
- **Bypass to live** available only when strategy has prior `to_mode='live'` in history (earned re-promotion); requires typed-name confirmation + reason on audit row.
- **Picker filters** mode-strict: Backtest = all 3 modes; Paper = paper-only; Dry-run = paper-only; Live = live-only.
- **Dry-run mode=dryrun + source=replay** does NOT require Kite creds or live_orders_enabled caps (rehearsal step BEFORE live setup).

## Strategy feature vocabulary (backend/algo/strategy/features.py)

- **Every new %-like feature MUST set `scale` explicitly** (`"fraction"` | `"percent"` | `"ratio"` | omit if not percentage-related). Found 2026-07-03: `distance_from_sma50 > -3` was a silent no-op — the feature is a fraction (0.05 = 5%) but the threshold was typed as a percentage. An audit found the SAME catalog mixes both scales for near-identical concepts (`pct_above_50sma` is fraction despite the name; `nifty_30d_return_pct` is genuinely percentage) — `scale` drives the Strategy Builder's inline unit caption so a user never has to guess. → `feature-scale-and-unwired-pattern`
- **Every new feature MUST be wired into at least one runtime's `EvalContext.features` before shipping, or marked `unwired=True`.** A feature merely added to the catalog (selectable in the Strategy Builder) but never populated by live/paper/backtest always hits `signal_rejected reason=missing_feature` — the condition can never evaluate true, silently. Found 9 such dead features 2026-07-03 (ASETPLTFRM-469). `unwired=True` drives a red warning in the Strategy Builder instead of letting a user build a permanently-dead condition. → `feature-scale-and-unwired-pattern`

## Paper/live parity (promotion gate trusts paper fills)

Paper runtime MUST mirror live execution — the paper→live gate relies on
paper-fill realism, so divergence makes promotion meaningless.

- **MTM equity**: `_account_snapshot` includes open-position market value in `current_equity_inr` + `daily_unrealised_pnl_inr` from `_last_marks` (ticker with no mark → 0, safe skip). `_size_via_composer` passes `cash = nav − deployed_cost` into `SizingContext`, NOT bare `nav`.
- **Directional slippage**: `PaperBroker.execute()` applies `ALGO_PAPER_SLIPPAGE_BPS` (int, default 0) — BUY fills above / SELL below `last_price`; fee base stays `last_price`; `bps=0` is a no-op (regression guard).
- **No silent qty=0 drop**: insufficient capital → emit `signal_rejected` (reason=`insufficient_capital_qty_zero`), mirroring live `_maybe_emit_qty_zero_rejection`. NEVER round to qty=0 and drop.
- **Trailing exits use the SHARED `ExecutionSimulator`** (same component as backtest — structural parity, NOT a paper-local copy). `PaperBroker.execute(trigger_price=…)` fills the exit at trigger ± slippage (models the live GTT); fee product from `strategy.product` (CNC→DELIVERY, MIS→INTRADAY — was hardcoded DELIVERY, mis-billed MIS). Paper stays 1m (live ticks), backtest 15m — parity = same exit logic/fill/fee, differing ONLY in bar resolution. Entries + time-stop keep the market fill (no `trigger_price`). → `intraday-evaluation-parity`

## Live execution safety (GTT / STOP_HIT)

- **Emergency STOP_HIT SELL is sacrosanct**: when WS HWM detects `STOP_HIT` (the GTT may not have fired), the pre-SELL `delete_gtt` is wrapped — a cancel failure MUST NOT skip the emergency SELL (`live/runtime.py` ~L1496). Emergency SELL routes through the tracked `_submit_order` path, never a raw order. → `algo-gtt-trailing-stop`
- **Backend restart kills the Kite WS session** — `./run.sh restart backend` tears down the live runtime; ticks stop and no bar evals fire until the user manually clicks Start/Resume in the Algo Trading UI. Never restart mid-session without warning the user first.
- **eval_node KeyError MUST emit `signal_rejected`** — if a feature is absent from `EvalContext.features` at eval time, append a `signal_rejected` event (`reason="missing_feature"`, `missing_key=str(exc)`) before returning 0. Silent returns mask triage and leave the UI showing no activity for oversold tickers.
- **GTT-triggered SELLs have no in-flight entry** — Kite fires the GTT autonomously so `_reconcile_terminal_with_in_flight` finds no match → `matched_strategy_id=None` → normal SELL cleanup is skipped. Two-path accounting (both idempotent; whichever runs first wins): **Piece A** — `_ratchet_all_gtts` calls `kite.get_gtts()` once per 15-min tick; any tracked `gtt_id` absent from the active set is treated as triggered — sources qty/price from Kite's own GTT order definition (`orders[0]`, NOT the in-memory position tracker, which can drift out of sync with `_trailing_managers` across a restart), applies the fill, clears state, and emits BOTH `order_filled_live` and `gtt_triggered` (fixed 2026-07-02 — Piece A previously emitted only `gtt_triggered`, so any trade it caught was invisible to the Attribution panel and Strategy Performance page). **Piece B** — postback fallback block calls `get_supervisor().find_live_runtime_with_gtt(user_id, ticker)` then `_apply_gtt_triggered_sell_fill` + `_on_sell_fill_trailing`. → `algo-gtt-trailing-stop`
- **algo.events fill price key differs by mode**: live `order_filled_live` events (both the direct `live/runtime.py` fill path and the Kite postback webhook path) carry the price under `payload["price"]`; paper's `order_filled` events use `payload["fill_price"]`. Any code reading a fill for its price MUST do `payload.get("fill_price") or payload.get("price")` — a bare `.get("fill_price")` silently zeroes every live-mode trade's price/PnL/return% (bit `routes/attribution.py` and the new `attribution/trade_pairing.py`, fixed 2026-07-02). → `debugging-live-fill-price-key-mismatch`
- **`self._caps` and any scalar derived from it at `LiveRuntime.__init__` (`allowed_tickers`, `gtt_limit_headroom_pct`) are a startup-time snapshot, frozen for the runtime's lifetime** — a mid-run `PUT /algo/live/caps/{id}` edit is invisible until restart unless the read site re-fetches. Fixed 2026-07-03 by reading `self._caps` directly (not a cached scalar) at every use site, and refreshing `self._caps` itself at two natural points: `_on_bar_close`'s existing fresh-caps PG read, and immediately before each 15-min `_ratchet_all_gtts` tick. Any NEW caps field read in a hot path must follow the same pattern — never cache a caps value into a separate `self._x` at `__init__` without a refresh path. → `live-caps-staleness-mid-run`
- **The `_bucket_by_ticker` skip-preload gate (`_on_bar_close` ~L3455) is the SAME staleness bug class, a different call site** — it only checked `_bucket_by_ticker` (loaded once at `__init__` from `stocks.universe_snapshot`) and `open_positions()` before permanently marking a ticker untradeable (`_bars_by_ticker[ticker] = []`, never retried — the check only runs once per ticker). A ticker added to `allowed_tickers` mid-run that's also absent from `universe_snapshot` (e.g. a less-liquid name never covered by that rebalance) got poisoned on its very first bar — every bar-derived feature (`rsi_2` included) stayed missing forever, even with full `stocks.ohlcv` history. Fixed 2026-07-04 by also checking `self._caps.get("allowed_tickers")` before giving up. Any future gate that decides "is this ticker tradeable" must check the CURRENT `allowed_tickers`, not just a startup-time universe snapshot. → `live-bucket-gate-allowed-tickers`
- **GTT-triggered exits MUST release the matching BUY's budget reservation, not just apply the fill to the position tracker + emit `algo.events`.** Both Piece A and Piece B call `_release_budget_reservation_for_gtt_exit()` (creates a SELL reservation and immediately transitions it to FILLED — a GTT fill is detected post-facto, so there's no PENDING/SUBMITTED phase to model) after applying the fill. Skipping this on a new exit path silently overstates `sum_open_position_cost` forever for that position, eating into Cap 0 pool-wide budget headroom for EVERY strategy the user runs, not just the one that closed (found 2026-07-03 — neither existing path had ever done this). → `gtt-exit-budget-reservation-release`
- **`_reconcile_terminal_with_in_flight` returns a tuple** `(matched_strategy_id, matched_entry)` — callers MUST unpack both. Using `matched_entry` without unpacking was a silent `NameError` (swallowed by `except Exception`) that prevented `_on_sell_fill_trailing` from ever firing via the webhook before this was fixed.
- **`kite.dry_run` (no underscore)** drives `self._dry_run` in `LiveRuntime.__init__` (`getattr(kite, "dry_run", False)`). Test mocks that set `kite._dry_run = False` (underscore) leave `_dry_run=True` because `MagicMock().dry_run` returns a truthy mock. Set `kite.dry_run = False` or `rt._dry_run = False` directly in tests that exercise the GTT detection block.
- **`KiteClient` has NO `ltp()` wrapper** — use `self._kite._kc.ltp([f"NSE:{bare}"])` (raw `KiteConnect`). `kite_client.py` exposes `quote()`, `positions()`, `place_order()`, `place_gtt()` etc. but NOT `ltp()`. Wrong call → `AttributeError` silently caught by `except Exception` → `last_price=None` → crash.
- **Kite holdings/positions API returns bare tradingsymbols** (`"EQUITASBNK"`, no `.NS`). Runtime keys (`_ws_hwm`, `_gtt_ids`, `_positions`) always carry `.NS`. Normalize at the route boundary: `if "." not in ticker: ticker += ".NS"` before any runtime state lookup.
- **`async` LiveRuntime methods can be `await`-ed directly from FastAPI route handlers** — both share the same uvicorn event loop. `asyncio.to_thread` / `run_coroutine_threadsafe` are only needed when calling FROM sync threads (e.g. `_ratchet_all_gtts` inside `asyncio.to_thread`).
- **A long-running backend process does NOT pick up a newly-merged file just because it landed on disk** — `uvicorn --reload`'s `StatReload` is not guaranteed to fire for a git-merge-introduced change. Confirmed 2026-07-03: PR #292 merged `fifo_matcher.py` into `dev` at 10:36:47 UTC; a scheduled job (`algo_closed_trades_rollup`) running on a process started at 09:54:56 UTC (before the merge) hit `ModuleNotFoundError` at 11:00:06 UTC — no `StatReload` log line appears anywhere in that 24-minute window. Only the next restart (11:45:52 UTC) picked up the new module. After merging a PR that adds a file a scheduled job depends on, restart explicitly — do not assume the running process will notice. → `deploy-staleness-after-merge`

## Intraday execution clock (backtest / walkforward)

- **Two-clock engine**: a strategy's SIGNAL cadence (daily/15m) is decoupled from a finer EXECUTION clock that drives ATR trailing / hard+time+regime stops / MIS square-off — so a daily strategy gets intraday exit resolution, replicating live. Daily-signal + trailing-enabled + coverage → exits evaluate every exec bar; entries still fire once/day. Trailing-disabled OR no-coverage collapses to the daily clock (byte-identical). Shared `ExecutionSimulator` wraps the live `TrailingStopManager` (parity is structural). → `intraday-evaluation-parity`
- **Execution grain is data-driven, NOT assumed**: `intraday_coverage()` picks the finest grain present per (ticker, window) in `stocks.intraday_bars`. **Reality: 15m only** (~497 tickers, 2022-06+); NO 1m/5m history exists. Uncovered tickers → daily-fallback (flagged `daily_fallback_tickers`). **1m/5m-cadence backtests are BLOCKED** (no history) → paper only.
- **Backtest/walkforward eval path makes ZERO Kite calls** — read only preserved Iceberg; coverage gaps degrade/flag, never live-fetch (coverage probe wrapped → daily-fallback on catalog error). **Paper is the exception** — it is LIVE-tick-driven (1m bars built from the live Kite WS via `Resampler(60)`, NOT stored data), so it uses the live feed by design; the zero-Kite rule is about historical replay only. `intraday_coverage`/daily-fallback do NOT apply to paper.
- **Stop fills at trigger ± slippage**: `SimBroker._execute_trigger_fill` fills on the CURRENT exec bar at the trailing trigger (models the live GTT), not next-bar-open.
- **★ Fee-product gotcha**: `SimBroker` infers fee product from `intent_emitted_ts_ns` (ts set → INTRADAY). Two-clock exits carry an exec-bar ts but a daily CNC position is a DELIVERY sell — so exit `OrderIntent`s MUST set `product` from `strategy.product` (CNC→DELIVERY, MIS→INTRADAY) or the CNC strategy is mis-billed cheap intraday STT (optimistic P&L). Entries fill DELIVERY via the daily `sim`.

## Zerodha broker connect UX

- **`DELETE /algo/broker` revokes token only** (keeps `api_key_fernet` row intact → status `key_set`). Use `DELETE /algo/broker/key` for full credential teardown. Never wipe the API key on a normal "disconnect" — Kite tokens expire EOD, the key is permanent.
- **UI state machine**: `disconnected` → API key form; `key_set` → "Connect Zerodha" + "Remove API key" button; `connected` → "Reconnect" (re-opens OAuth) + "Remove API key"; `expired` → "Reconnect" + "Remove API key". No "Disconnect" button — revoking mid-session has no real use case.
- **GTT `place_gtt` prices MUST be tick-aligned** — call `get_tick_size()` (Redis-backed) and quantize BOTH `trigger_price` and `limit_price` with `ROUND_DOWN` before every `place_gtt`. Applies to all three call sites: `on_buy_fill_trailing`, `_ratchet_all_gtts`, `ensure_gtts`. → `kite-tick-size-limit-price`
- **`kc.positions()`/`kc.holdings()` can raise `kiteconnect.exceptions.DataException` on a Content-Type mismatch (`text/plain` for a genuinely valid JSON body) — the SDK discards the response instead of parsing it.** Any new call site MUST go through `kite_call_tolerant()` (sync) / `kite_call_tolerant_async()` (async) in `backend/algo/broker/kite_client.py`, never a bare `asyncio.to_thread(kc.positions)`/`kc.positions()`. → `kite-content-type-mismatch`

## Reporting / analytics on algo.events

- **Never query `algo.events` live from a page, and never write reporting logic into the live/paper runtime hot paths.** For any feature that reports on historical algo.events activity (trade history, PnL summaries, attribution), materialize into a small idempotent-upsert Postgres table via a **daily off-hours batch job** instead — dedupe on a natural key derived from the source event ids (e.g. `(buy_event_id, sell_event_id)`) so re-runs are safe. Pattern established by `algo.closed_trades` / `algo_closed_trades_rollup` (`backend/algo/jobs/closed_trades_rollup.py`) — avoids repeating the `algo.events` bloat/read-pressure incident and keeps runtime code free of reporting concerns. → `docs/superpowers/specs/2026-07-01-algo-strategy-performance-design.md`
- **Standalone algo job wrappers in `backend/jobs/executor.py` MUST call `_algo_job_success(repo, run_id)` after their work completes successfully** (pipeline-driven jobs like `algo_events_retention` are exempt — the pipeline orchestrator sets status itself). `scheduler_service.py`'s dispatcher only ever sets `duration_secs` on the success path; `status` is explicitly the executor's own responsibility, by design. Skipping this call leaves the `scheduler_runs` row stuck at `status='running'` forever even when the underlying work succeeded and finished in under a second (found 2026-07-02, `_job_algo_closed_trades_rollup` — mirror `_job_algo_reconciliation` or `_job_algo_kite_instruments_refresh` when adding a new one).
- **Paper-mode `algo.events` retention is 7 days** (`SHORT_RETENTION_DAYS`, swept weekly Sun 03:00 IST) vs 365 days for live events that confirm a Zerodha order placement — a reporting/rollup job's own `window_days` setting (e.g. `algo_closed_trades_rollup`'s 400-day default) is only meaningful for live mode; paper trades older than ~7-13 days are already gone from Iceberg regardless of the job's own window or run cadence.
