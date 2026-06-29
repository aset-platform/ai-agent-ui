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
- **Trailing exits use the SHARED `ExecutionSimulator`** (same component as backtest — structural parity, NOT a paper-local copy). `PaperBroker.execute(trigger_price=…)` fills the exit at trigger ± slippage (models the live GTT); fee product from `strategy.product` (CNC→DELIVERY, MIS→INTRADAY — was hardcoded DELIVERY, mis-billed MIS). Paper stays 1m (live ticks), backtest 15m — parity = same exit logic/fill/fee, differing ONLY in bar resolution. Entries + time-stop keep the market fill (no `trigger_price`).

## Live execution safety (GTT / STOP_HIT)

- **Emergency STOP_HIT SELL is sacrosanct**: when WS HWM detects `STOP_HIT` (the GTT may not have fired), the pre-SELL `delete_gtt` is wrapped — a cancel failure MUST NOT skip the emergency SELL (`live/runtime.py` ~L1496). Emergency SELL routes through the tracked `_submit_order` path, never a raw order. → `algo-gtt-trailing-stop`

## Intraday execution clock (backtest / walkforward)

- **Two-clock engine**: a strategy's SIGNAL cadence (daily/15m) is decoupled from a finer EXECUTION clock that drives ATR trailing / hard+time+regime stops / MIS square-off — so a daily strategy gets intraday exit resolution, replicating live. Daily-signal + trailing-enabled + coverage → exits evaluate every exec bar; entries still fire once/day. Trailing-disabled OR no-coverage collapses to the daily clock (byte-identical). Shared `ExecutionSimulator` wraps the live `TrailingStopManager` (parity is structural).
- **Execution grain is data-driven, NOT assumed**: `intraday_coverage()` picks the finest grain present per (ticker, window) in `stocks.intraday_bars`. **Reality: 15m only** (~497 tickers, 2022-06+); NO 1m/5m history exists. Uncovered tickers → daily-fallback (flagged `daily_fallback_tickers`). **1m/5m-cadence backtests are BLOCKED** (no history) → paper only.
- **Backtest/walkforward eval path makes ZERO Kite calls** — read only preserved Iceberg; coverage gaps degrade/flag, never live-fetch (coverage probe wrapped → daily-fallback on catalog error). **Paper is the exception** — it is LIVE-tick-driven (1m bars built from the live Kite WS via `Resampler(60)`, NOT stored data), so it uses the live feed by design; the zero-Kite rule is about historical replay only. `intraday_coverage`/daily-fallback do NOT apply to paper.
- **Stop fills at trigger ± slippage**: `SimBroker._execute_trigger_fill` fills on the CURRENT exec bar at the trailing trigger (models the live GTT), not next-bar-open.
- **★ Fee-product gotcha**: `SimBroker` infers fee product from `intent_emitted_ts_ns` (ts set → INTRADAY). Two-clock exits carry an exec-bar ts but a daily CNC position is a DELIVERY sell — so exit `OrderIntent`s MUST set `product` from `strategy.product` (CNC→DELIVERY, MIS→INTRADAY) or the CNC strategy is mis-billed cheap intraday STT (optimistic P&L). Entries fill DELIVERY via the daily `sim`.
