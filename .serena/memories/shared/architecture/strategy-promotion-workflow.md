# Strategy promotion workflow (draft → paper → live)

ASETPLTFRM-400 + adjacent epic spinout. Replaces the dead
`algo.strategies.mode` column (always `'draft'`) with a real
permission gate that controls which surfaces a strategy appears
on, with full audit trail and a guarded fast-lane re-promotion
path.

## Lifecycle

```
draft  →[gate: backtest+walkforward fresh]→  paper
paper  →[gate: paper run fresh]→             live
live   ←[AST edit auto-demotes]←             draft
```

- "Fresh" = run started after `strategies.updated_at` (any AST
  edit invalidates prior runs).
- Demotion happens ONLY via AST edit; no manual demote button
  (avoids "did I demote-by-mistake or get auto-demoted?" foot-gun).
- After a strategy has held `to_mode='live'` once in its
  history, a typed-name bypass card unlocks for fast re-promotion
  to live after subsequent edits (earned right).

## Schema

`alembic_revision: b8c9d0e1f2a3` (2026-05-14):

```sql
ALTER TABLE algo.strategies
  ADD CONSTRAINT ck_strategies_mode
  CHECK (mode IN ('draft','paper','live'));

CREATE TABLE algo.strategy_mode_transitions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id uuid REFERENCES algo.strategies(id) ON DELETE SET NULL,
  user_id uuid NOT NULL,
  user_email varchar(255) NOT NULL,  -- denormalized; survives user delete
  from_mode varchar(16),             -- NULL on first transition
  to_mode varchar(16) NOT NULL,
  reason text,
  bypass_used boolean NOT NULL DEFAULT false,
  ast_hash varchar(64),              -- sha256 of canonical AST at transition
  transitioned_at timestamptz NOT NULL DEFAULT NOW()
);
```

FK is `ON DELETE SET NULL` (not CASCADE) so forensic trail
survives a hard-deleted strategy.

## Gate semantics

Backtest / walk-forward gates (draft → paper):
- Query `algo.runs` for `mode='backtest'`/`'walkforward'` with
  `status='completed'`, `error_text IS NULL`, and
  `started_at >= strategies.updated_at`.

Paper gate (paper → live):
- Paper runtime does NOT create `algo.runs` rows — it emits
  events to Iceberg `algo.events`. The gate scans algo.events
  for `mode='paper' AND type='order_filled' AND ts_ns >=
  updated_at_ns`. Iceberg scan wrapped in `asyncio.to_thread`.

Reasons carry concrete counts so users see exactly what's
missing: "Found 99 paper fill(s), but 0 since the latest AST
edit (2026-05-14 14:55 IST). Editing invalidates older paper
sessions — start a fresh paper run and let it execute at least
one order."

## Bypass

- Available ONLY when target=live AND prior `to_mode='live'`
  exists in transitions table.
- Frontend hides the bypass UI when ineligible (no
  disabled-but-visible weakness).
- Backend PATCH still validates server-side (defence in depth):
  403 if bypass=true but ineligible.
- Requires typed strategy name + freeform reason on audit row.

## Auto-demote on AST edit

`update_strategy()` in `backend/algo/strategy/repo.py`:
- Returns `UpdateResult(found, demoted_from, ast_hash)`.
- When `demoted_from != None` the route writes a transition row
  with `reason="auto-demoted on AST edit"`.

Runtime impact: Paper / Live runtimes hold AST in memory, so
demoting mid-flight is harmless for in-flight orders. The next
runtime-restart picks up the new AST (which is back to draft;
re-promotion required first).

## Picker filters (mode-strict)

| Surface | Modes shown |
|---|---|
| Backtest / Walk-forward | all 3 (no filter) |
| Paper | paper-only |
| Dry-run | paper-only (rehearses paper-promoted AST) |
| Live | live-only |

A strategy promoted to live no longer appears on paper/dry-run
pickers — different stages, different surfaces.

`filterStrategiesByMode(strategies, modes)` helper in
`frontend/hooks/useStrategies.ts`. Applied across 9 call sites:
`PaperTab`, `PaperSessionSummary`, `ActiveRunsPanel`,
`DryRunTab`, `LiveDashboard`, `LiveActiveRunsPanel`,
`LiveSettingsTab`, plus Backtest / WalkForward subtabs which
pass all-modes.

## Dry-run pre-flight

`/v1/algo/paper/run mode=dryrun source=replay` does NOT
require Kite credentials or `live_orders_enabled` caps. Dry-run
is the rehearsal step BEFORE caps are set, so requiring them
defeats the purpose. KiteClient(dry_run=True) makes no real
Kite REST calls; placeholder api_key + None access_token is
enough.

## Endpoints

- `GET /v1/algo/strategies/{id}/mode-transitions/eligibility`
  → `{current_mode, transitions: [{target, allowed, reasons[], bypass_available}]}`
- `GET /v1/algo/strategies/{id}/mode-transitions` — history popover
- `PATCH /v1/algo/strategies/{id}/mode` — body `{mode, bypass?, reason?}`
- List response enriched with `has_active_runtime`,
  `active_runtime_modes`, `open_position_count`,
  `has_ever_been_live`, `last_transition_at`, `last_transition_by`.

## Frontend

- `PromoteModal.tsx` — per-target cards with gate reasons,
  optional bypass card (typed-name confirmation + reason).
- `StrategyBuilder.tsx::DemoteWarningBanner` — for paper/live
  strategies, surfaces active-runtime + open-position counts.
- `PromotionToLiveCallout.tsx` — collapsible callout on Paper
  + Dry-run tabs documenting promotion criteria (surface-aware copy).

## Files

- `backend/db/migrations/versions/2026_05_14_algo_strategy_mode_transitions.py`
- `backend/algo/strategy/mode_repo.py`
- `backend/algo/strategy/promotion.py`
- `backend/algo/strategy/runtime_state.py`
- `backend/algo/strategy/repo.py` (auto-demote)
- `backend/algo/routes/strategies.py` (PATCH /mode, eligibility,
  history, list-response enrichment, clone)
- `backend/algo/tests/test_promotion_workflow.py` (10 tests)
- `backend/algo/tests/test_strategies_routes.py` (clone + auto-demote)
- `frontend/components/algo-trading/PromoteModal.tsx`
- `frontend/components/algo-trading/PromotionToLiveCallout.tsx`
- `frontend/hooks/useStrategies.ts`

## Cross-refs

- Shipped via PR #221 (squash f140fd6 on 2026-05-14)
- CLAUDE.md §5.16 "Strategy promotion workflow"
- `feedback_runtime_feature_three_runtimes` memory
