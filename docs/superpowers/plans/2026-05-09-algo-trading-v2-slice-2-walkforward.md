# Algo Trading v2 — Slice V2-2: Walk-Forward CV Harness

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Add a walk-forward cross-validation harness on top of the v1 backtest runner so a strategy can be validated across rolling train/test windows before it goes live. The walk-forward report is one of the four gates on the live-mode toggle in V2-5.

**Architecture:** Pure orchestration over the existing `backend/algo/backtest/runner.run_backtest()`. The harness slices a long period into N rolling (train, test) windows and invokes the runner once per window. Each window persists as a normal `algo.runs` row with new `parent_walkforward_id` + `window_start` + `window_end` columns. A parent walkforward row aggregates per-window summaries.

**Tech Stack:** Python 3.12 / SQLAlchemy 2.0 async / pytest. New sub-tab on the existing Backtest tab in the frontend.

**Spec:** `docs/superpowers/specs/2026-05-09-algo-trading-v2-design.md` — Slice V2-2 (§9.1).

**Branch:** `feature/algo-trading-v2-slice-2-walkforward` off `feature/algo-trading-v2-integration`. Can run in parallel with V2-1.

**Depends on:** V2-0 merged. NO dependency on V2-1 — fully decoupled.

---

## File Structure

**Backend (new):**
- `backend/algo/backtest/walkforward.py` — `WalkForwardConfig` Pydantic + `walk_windows(start, end, train_days, test_days, step_days)` iterator + `run_walkforward(strategy, config, user)` orchestrator.
- `backend/algo/routes/walkforward.py` — `POST /v1/algo/walkforward/run`, `GET /v1/algo/walkforward/runs/{id}`.
- `backend/algo/tests/test_walkforward_windows.py` — window iterator unit tests (boundary cases: shorter-than-train period, period not divisible by step, trailing partial window).
- `backend/algo/tests/test_walkforward_runner.py` — orchestrator end-to-end with a small synthetic strategy.

**Backend (modified):**
- `backend/db/migrations/versions/2026_05_10_algo_runs_walkforward_columns.py` — Alembic migration: `algo.runs` adds `parent_walkforward_id UUID NULL`, `window_start DATE NULL`, `window_end DATE NULL`.
- `backend/algo/backtest/runs_repo.py` — accept the new columns in `create_run()`.
- `backend/algo/event_writer.py` — register `walkforward_window_started`, `walkforward_window_completed` event types.

**Frontend (new):**
- `frontend/components/algo-trading/WalkForwardSubTab.tsx` — config form (train_days, test_days, step_days, period) + stacked equity curves report.
- `frontend/components/algo-trading/WalkForwardEquityCurves.tsx` — N stacked ECharts.
- `frontend/hooks/useWalkForwardRuns.ts` — SWR.

**Frontend (modified):**
- `frontend/components/algo-trading/BacktestTab.tsx` — sub-tab strip: "Single run" (existing) | "Walk-forward CV" (new).

**E2E:**
- `e2e/tests/frontend/algo-trading-walkforward.spec.ts` — kick off, wait for completion, verify N curves render.

---

## High-level task list (expand at session start)

1. **Window iterator** — pure function; covers full period with rolling steps; non-overlapping train/test sub-ranges within each window.
2. **Migration** — additive columns on `algo.runs`.
3. **Repo extension** — `create_run(parent_walkforward_id, window_start, window_end)`.
4. **Orchestrator** — for each window, call `run_backtest(strategy, train_period)` (no eval) → `run_backtest(strategy, test_period)` (eval); persist test-window run with `parent_walkforward_id`.
5. **Aggregate summary** — across N test-window runs, compute aggregate win-rate, avg PnL%, avg max DD, std-dev of returns; store on parent row.
6. **API endpoints** — POST kicks off async job; GET returns aggregate + per-window summaries.
7. **Frontend sub-tab** — form + report.
8. **Stacked equity curves** — single ECharts with N series, color-graded by window index.
9. **Documentation** — `docs/algo-trading/backtest.md` adds "Walk-forward CV" section.

---

## Acceptance

- [ ] `walk_windows(2024-01-01, 2026-01-01, train_days=180, test_days=30, step_days=30)` produces 23 windows, all train periods 180 days, all test periods 30 days, non-overlapping within window, sliding by 30 days.
- [ ] Running on Golden Cross v1 over 2024-01-01 → 2026-01-01 produces 23 child runs + 1 parent walkforward row.
- [ ] Frontend renders 23 stacked equity curves; aggregate summary cards show win-rate / avg PnL / avg max DD.
- [ ] Run can be cancelled mid-execution (existing async-job pattern).
- [ ] No regression on single-run backtests.

---

## Out of scope for V2-2

- Live trading (V2-5).
- WS multiplexer (V2-1).
- Reconciliation (V2-3).
- Auto-promotion of strategies to Live based on walk-forward score (deferred — UX risk; user explicitly approves each strategy).
