# Regime-Aware Multi-Factor System — Slice REGIME-3: Strategy↔Regime Binding + Selector

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Wire two-tier strategy↔regime expressiveness. Metadata field `applicable_regimes: ["bull", "sideways"]` on strategy + optional in-AST `regime_eq("bull")` predicate for power-user fine-grained gating. Strategy selector filters available strategies by current regime. Regime flip mid-week surfaces as recommendation + amber banner + email, with manual pause/resume option.

**Architecture:** PG `algo.strategy_metadata` table (mutable strategy-author state). AST evaluator extension to handle string operands in `compare` node (no grammar surgery). New `regime_changed` event fired on regime flip. Runtime feature `regime_label` (string) registered in feature module. Frontend multi-select chips for applicable_regimes + regime-change banner.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0 async, Next.js 16, React 19.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.9 (AST grammar changes), §1 Goals + Regime flip behavior, §5.1 REGIME-3 row, §6.1 REGIME-3 test row.

**Branch:** `feature/algo-regime-slice-3-binding-selector` off `feature/regime-multifactor-integration`.

**Depends on:** REGIME-1 merged (regime_label feature must be published).

**Estimated SP:** 8

---

## File Structure

**Backend (new):**
- `backend/algo/strategy/metadata.py` — `StrategyMetadata` Pydantic model with `applicable_regimes: list[Literal["bull", "sideways", "bear"]]` field (defaults to all 3).
- `backend/db/migrations/versions/2026_05_10_algo_strategy_metadata_table.py` — Alembic migration: create `algo.strategy_metadata` PG table (strategy_id UUID PK, applicable_regimes TEXT[], expected_edge NUMERIC NULL, description TEXT, updated_at TIMESTAMP).
- `backend/algo/strategy/features.py` — extend `FEATURES` registry with `regime_label` (string type).
- `backend/algo/strategy/ast.py` — extend `Literal_` discriminated union to include `StrLiteral(value: str)`; extend `compare` operator to handle string equality (`==`, `!=`) when both operands are strings; add `regime_eq(regime: str)` sugar node that compiles to `compare(regime_label, ==, regime)`.
- `backend/algo/routes/strategies.py` — modify strategy CRUD (POST/PUT) to accept + upsert `applicable_regimes`; GET response includes metadata.
- `backend/algo/event_writer.py` — register `regime_changed` event type (fired once per regime-flip boundary, not per-signal).
- `backend/algo/jobs/regime_change_notifier.py` — NEW — daily job (23:50 IST) checks if regime flipped from yesterday; if yes, emit `regime_changed` event + send amber banner message to WS + dispatch email to superusers.
- `backend/algo/tests/test_strategy_metadata.py` — metadata CRUD round-trip; default regime-agnostic behavior.
- `backend/algo/tests/test_ast_string_compare.py` — unit tests for `regime_eq` sugar + string-compare evaluation.
- `backend/algo/tests/test_regime_changed_event.py` — regime flip fires event once per boundary.

**Backend (modified):**
- `backend/algo/strategy/features.py` — register `regime_label` key (string).
- `backend/algo/strategy/ast.py` — evaluator + grammar mods (described above).
- `backend/algo/routes/live.py` — when user selects a strategy for live trading, check `applicable_regimes` includes current `regime_label`; warn if mismatch (but allow user override).

**Frontend (new):**
- `frontend/components/algo-trading/StrategySelector.tsx` — NEW — dropdown/combobox filtered to strategies where `applicable_regimes` includes current regime.
- `frontend/components/algo-trading/RegimeChangeBanner.tsx` — amber banner on Trading tab when regime flipped between market open + close of previous day; text "Regime changed from BULL to SIDEWAYS. Your BULL-only strategies are now marked inactive. [View strategies] [Dismiss]"; auto-dismiss after 4 hours or on manual click.
- `frontend/hooks/useRegimeStatus.ts` — SWR hook polling current regime (60s TTL).

**Frontend (modified):**
- `frontend/components/algo-trading/StrategyEditor.tsx` — add `applicable_regimes` multi-select chip group at the top of editor form; visual feedback (red outline) if current regime is not in selected list.
- `frontend/components/algo-trading/TradingTab.tsx` — mount `RegimeChangeBanner` in header; pass `useRegimeStatus` result.
- `frontend/hooks/useStrategy.ts` — response includes `applicable_regimes` array from metadata.

**E2E (new):**
- `e2e/tests/frontend/algo-regime-binding.spec.ts` — editor: set `applicable_regimes=["bull"]`; save strategy; change regime to "sideways" (via seeded Iceberg); verify strategy shows as inactive; switch regime back to "bull"; verify strategy shows active again.
- `e2e/tests/frontend/algo-regime-change-banner.spec.ts` — regime flip triggered via seeded Iceberg; banner appears + contains regime names; dismiss button works + survives page reload (4h TTL).

**Docs (new):**
- `docs/algo-trading/strategy-regime-binding.md` — explains `applicable_regimes` metadata + `regime_eq()` AST sugar with examples.

---

## High-level task list (expand at session start)

1. **PG migration** — create `algo.strategy_metadata` table with 5 columns (strategy_id, applicable_regimes, expected_edge, description, updated_at).
2. **Pydantic model + CRUD** — `StrategyMetadata` class; extend strategy POST/PUT routes to upsert metadata.
3. **AST evaluator extension** — add `StrLiteral` node type; extend `compare` to handle string operands; implement `regime_eq` sugar.
4. **Feature registry** — register `regime_label: string` in the features module (reads from runtime context on every bar).
5. **Backend default behavior** — strategy without metadata defaults to `applicable_regimes=["bull", "sideways", "bear"]` (regime-agnostic); zero breaking change.
6. **Regime change job** — daily 23:50 IST check if regime flipped; emit `regime_changed` event if yes; dispatch WS message + email.
7. **Strategy selector** — frontend dropdown filtered by current regime; grayed-out non-matching strategies.
8. **Regime change banner** — amber banner mounted on Trading tab; shows previous + current regime; auto-dismiss after 4 hours.
9. **Editor chips** — multi-select for `applicable_regimes`; visual warning if current regime not in selected.
10. **AST string-compare tests** — happy path + NaN handling + edge cases (empty string, null).
11. **E2E test regime editor** — set metadata; trigger regime flip via seeded data; verify UI feedback.
12. **E2E test regime change banner** — trigger flip; verify banner render + dismiss + auto-expire.

---

## Acceptance

- [ ] Strategy CRUD round-trip: POST with `applicable_regimes=["bull"]` → GET returns same array. PUT updates. DELETE clears metadata row.
- [ ] `regime_eq("bull")` sugar compiles to `compare(regime_label, ==, "bull")` (no new AST node type).
- [ ] String compare evaluation: `regime_label="BULL"`, `regime_eq("BULL")` evaluates to true; `regime_eq("sideways")` evaluates to false.
- [ ] Default regime-agnostic: strategy with no metadata row defaults to all-3 on selection + in evaluator (no error, no exception).
- [ ] Backward compat: existing strategies (pre-REGIME-3) parse + execute without metadata rows.
- [ ] Regime flip fires `regime_changed` event exactly once per boundary (not per-signal, not per-user).
- [ ] Banner appears within 60s of regime flip (polling 60s TTL); contains old + new regime names.
- [ ] Banner dismissible; respects 4-hour TTL (stored in `algo_regime_change_notification` PG with user_id + regime + timestamp).
- [ ] Email sent on regime flip (to all users with active live runs, or superuser-only — per spec §1).
- [ ] Strategy editor shows multi-select; current regime outlined in red if not in selection.
- [ ] Strategy selector dropdown filters to `applicable_regimes` ∩ {current_regime}.
- [ ] No regressions on existing strategy execution (V2-0, V2-1, V2-2 unchanged).

---

## Out of scope for REGIME-3

- Auto-pause on regime change (v4 after classifier is trusted across multiple cycles).
- Per-sector regime overlay (v4).
- Per-user `algo_regime_override` PG row (mentioned in spec §8 Q11 — defer to v3.1 if needed; 3-day TTL manual override).
- Mandatory `applicable_regimes` enforcement at live-toggle time (soft recommendation only in v3; becomes gate in v3.1).
