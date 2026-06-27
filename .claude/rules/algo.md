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
