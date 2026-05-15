---
name: subagent-parallel-dispatch-pattern
description: 2026-05-15 Phase 3 of ASETPLTFRM-402 — 3 parallel R1 slices + 1 sequential R2 in ~20 min via subagent fan-out. Pattern for high-throughput multi-slice epic delivery.
metadata:
  type: process
---

# Subagent-driven parallel dispatch

ASETPLTFRM-402 Phase 3 (12 SP) shipped in ~20 min wall clock via parallel + sequential subagent dispatch. Pattern is reusable for multi-slice epics where slices can be partitioned by file ownership.

## When this pattern fits

- ≥3 independent slices in a single epic
- Each slice owns a distinct file domain (different routes/jobs/modules)
- Some slices have dependencies; most don't
- Total work would take 1-2 hours sequential

## Phase 3 example

**Round 1 (parallel — 3 subagents dispatched in one message)**:
- FE-11 Feature importance API → `backend/algo/features/importance.py` + new route
- FE-13 Meta-labeling job → `backend/algo/jobs/trade_outcome_backfill.py`
- FE-14 Coverage dashboard → `backend/algo/routes/feature_coverage.py` + frontend admin tab

**Round 2 (sequential — dispatched after R1 verified)**:
- FE-12 SHAP endpoint → depends on FE-11's `train_classifier` (refactor) and shap dep

## File-domain partitioning rule

Before dispatching parallel agents, audit:

| Risk | Mitigation |
|---|---|
| Multiple agents editing the same source file | Partition by file. If A and B both need to register routes in `backend/routes.py`, accept the cooperative edit risk (agents see each other's `Edit` results) OR refactor so A registers first, then B is sequential |
| Shared AST registry (`features.py`, `strategyFeatureCatalog.ts`) | Pre-plan which agent owns which entries; verify via `test_feature_registry_sync.py` |
| Same Alembic migration counter | Only one agent per migration. Sequential if multiple migrations needed |
| Pipeline DAG seeds | One agent per `seed_*.py`; or one agent owns DAG updates |

For Phase 3:
- Routes registry (`backend/routes.py` + `backend/algo/routes/__init__.py`): FE-11 and FE-14 BOTH needed to register routers. **Cooperative editing worked** — second agent's `Edit` saw the first's lines already in place and appended. Acceptable risk on a registry file where additions don't conflict.
- AST registry (`backend/algo/strategy/features.py`): no parallel slice modified it in Phase 3 (FE-13/14 don't add AST keys; FE-11/12 are routes only). Phase 2's parallel pattern was different because FE-8/FE-9 both added registry keys → had to use sequential.

## Subagent prompt template

Every parallel-dispatched prompt must include:

1. **Hard preflight** — grep-verify imports + read files in full before writing (per `feedback_subagent_grep_preflight`). Without this, ~8 bugs/epic from wrong-symbol-name issues.
2. **File-domain ownership statement** — "You are running in parallel with FE-X and FE-Y subagents. DO NOT touch [list of forbidden paths]."
3. **Verification block** — exact commands to run before reporting back (pytest paths, lint paths).
4. **Non-regression scope** — which existing suites must stay green.
5. **Constraints** — no commit/push, no backend restart, line length 79, etc.

## Shared working tree caution

All parallel subagents check out branches on the SAME filesystem (no isolated worktrees in this harness). Consequences:

- **Last `git checkout` wins** — working tree reflects the most recently switched branch. Multiple agents doing `git checkout featureX` will collide; the user's branch state at the end may not match what any single agent expected.
- **Workaround**: each agent gets its OWN branch pre-created (`git branch feature/fe11 dev`) so they all start from the same commit. The agent does `git checkout feature/feXX` but the work products end up in the working tree regardless of which branch is checked out at any given moment.
- **At commit time**: switch to the correct branch FIRST, then `git add` only the files that slice owns. Use `git add -p` for shared files (e.g. routes.py).

For Phase 3, the working-tree-shared collision wasn't catastrophic because:
1. Each agent worked on file domains that didn't overlap (mostly)
2. Shared registry files cooperatively merged
3. After all 3 agents reported done, the orchestrator committed each slice to a SINGLE integration branch in 3 sequential commits (one per slice, scoped via `git add` to that slice's files)

## Sequential-after-parallel pattern

R2 dispatch (FE-12 SHAP) had to wait for R1 to commit FE-11 because FE-12 reuses FE-11's `train_classifier` refactor. Wall-clock penalty: R2 starts ~5 min after R1 finishes (verify + commit + dispatch). Acceptable.

If a longer dependency chain existed (R3 after R2 after R1), the cumulative wall-clock would degrade — but Phase 3 only had one R1→R2 link, so the parallel R1 fan-out saved most of the time.

## Verification orchestrator role

After all parallel agents report back, the orchestrator (me, not a subagent) must:

1. **Read each agent's diff independently** ("trust but verify" — agent reports describe intent, not actual changes)
2. **Run the union test suite** to catch cross-slice integration breaks
3. **Resolve shared-file merges** if they occurred (cooperative edit usually means nothing; structural conflict means manual edit)
4. **Stage + commit per-slice scoped** — even when work landed on one branch, the commits should reflect slice boundaries for review-time clarity

## Outcome metrics (Phase 3)

| Slice | Wall clock (subagent) | Tests added |
|---|---:|---:|
| FE-11 importance | ~8 min | 23 |
| FE-12 SHAP | ~7 min | 19 |
| FE-13 meta-label | ~7 min | 17 |
| FE-14 coverage | ~12 min | 16 |
| **Total (R1 parallel + R2 sequential)** | **~20 min** (max of parallel + R2) | **75** |

vs sequential dispatch (4 slices × ~10 min each = ~40 min): **2× speedup**.

## When NOT to parallelize

- Slices that touch the same critical-path file (e.g. `backend/algo/strategy/features.py` AST registry — sequence them)
- Slices that conflict on Alembic migration revision ids
- Slices that depend on each other's implementation details (e.g. FE-12 reused FE-11's refactor — keep sequential)
- When the test suite is finicky on Docker resource contention (parallel container test runs can fight for memory/CPU)
- Solo-developer rotation: don't ship more in parallel than you can review in a sitting

## Cross-refs

- `feedback_subagent_grep_preflight` — the preflight rule that prevents the wrong-name bug class
- `centralized-feature-engine` — the epic where this pattern was developed
- CLAUDE.md §1 (Session startup) — Ollama is for single-function delegations; subagents are for coherent multi-file slices
