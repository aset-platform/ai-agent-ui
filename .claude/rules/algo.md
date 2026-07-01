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

## Strategy promotion workflow

- Lifecycle: `draft → paper → live`. Audit table `algo.strategy_mode_transitions`.
- **Gates** (enforced by `promotion.check_eligibility`): draft→paper requires fresh completed backtest + walkforward (`started_at >= strategies.updated_at`); paper→live requires fresh paper-fill events in `algo.events` (paper runtime doesn't create algo.runs rows).
- **AST edits auto-demote** non-draft → draft (audit row `reason="auto-demoted on AST edit"`).
- **Bypass to live** available only when strategy has prior `to_mode='live'` in history (earned re-promotion); requires typed-name confirmation + reason on audit row.
- **Picker filters** mode-strict: Backtest = all 3 modes; Paper = paper-only; Dry-run = paper-only; Live = live-only.
- **Dry-run mode=dryrun + source=replay** does NOT require Kite creds or live_orders_enabled caps (rehearsal step BEFORE live setup).

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
- **GTT-triggered SELLs have no in-flight entry** — Kite fires the GTT autonomously so `_reconcile_terminal_with_in_flight` finds no match → `matched_strategy_id=None` → normal SELL cleanup is skipped. Two-path accounting (both idempotent; whichever runs first wins): **Piece A** — `_ratchet_all_gtts` calls `kite.get_gtts()` once per 15-min tick; any tracked `gtt_id` absent from the active set is treated as triggered (apply fill, clear state, emit `gtt_triggered`). **Piece B** — postback fallback block calls `get_supervisor().find_live_runtime_with_gtt(user_id, ticker)` then `_apply_gtt_triggered_sell_fill` + `_on_sell_fill_trailing`. → `algo-gtt-trailing-stop`
- **`_reconcile_terminal_with_in_flight` returns a tuple** `(matched_strategy_id, matched_entry)` — callers MUST unpack both. Using `matched_entry` without unpacking was a silent `NameError` (swallowed by `except Exception`) that prevented `_on_sell_fill_trailing` from ever firing via the webhook before this was fixed.
- **`kite.dry_run` (no underscore)** drives `self._dry_run` in `LiveRuntime.__init__` (`getattr(kite, "dry_run", False)`). Test mocks that set `kite._dry_run = False` (underscore) leave `_dry_run=True` because `MagicMock().dry_run` returns a truthy mock. Set `kite.dry_run = False` or `rt._dry_run = False` directly in tests that exercise the GTT detection block.
- **`KiteClient` has NO `ltp()` wrapper** — use `self._kite._kc.ltp([f"NSE:{bare}"])` (raw `KiteConnect`). `kite_client.py` exposes `quote()`, `positions()`, `place_order()`, `place_gtt()` etc. but NOT `ltp()`. Wrong call → `AttributeError` silently caught by `except Exception` → `last_price=None` → crash.
- **Kite holdings/positions API returns bare tradingsymbols** (`"EQUITASBNK"`, no `.NS`). Runtime keys (`_ws_hwm`, `_gtt_ids`, `_positions`) always carry `.NS`. Normalize at the route boundary: `if "." not in ticker: ticker += ".NS"` before any runtime state lookup.
- **`async` LiveRuntime methods can be `await`-ed directly from FastAPI route handlers** — both share the same uvicorn event loop. `asyncio.to_thread` / `run_coroutine_threadsafe` are only needed when calling FROM sync threads (e.g. `_ratchet_all_gtts` inside `asyncio.to_thread`).

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
