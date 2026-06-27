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

## Live execution safety (GTT / STOP_HIT)

- **Emergency STOP_HIT SELL is sacrosanct**: when WS HWM detects `STOP_HIT` (the GTT may not have fired), the pre-SELL `delete_gtt` is wrapped — a cancel failure MUST NOT skip the emergency SELL (`live/runtime.py` ~L1496). Emergency SELL routes through the tracked `_submit_order` path, never a raw order. → `algo-gtt-trailing-stop`
