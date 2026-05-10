# Algo Trading v2 — Slice V2-3: Reconciliation Loop

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Periodic reconciliation between Kite-reported broker positions and our `algo.positions` rows. v2 is **alert-only** — we surface drift as events + UI banner; we never silently overwrite. Drift persisting > 3 consecutive runs flips a gate that disables the live-mode toggle until the user manually reconciles.

**Architecture:** Single scheduled job (`algo_reconciliation`) running every 5 minutes during market hours (09:15–15:30 IST), driven by the existing `scheduler.py`. For each user with at least one live strategy active today, fetch `kite.positions["net"]`, compute diff, emit events. State (consecutive-drift counter, last-resolved-at) lives in a new tiny PG table `algo.live.drift_state`.

**Tech Stack:** Python 3.12 / SQLAlchemy 2.0 async / `kiteconnect` SDK / pytest.

**Spec:** `docs/superpowers/specs/2026-05-09-algo-trading-v2-design.md` — Slice V2-3 (§7.3, §13).

**Branch:** `feature/algo-trading-v2-slice-3-reconciliation` off `feature/algo-trading-v2-integration`.

**Depends on:** V2-1 merged (KiteClient.get_positions wrapper extends as part of V2-3, but a connected Kite session is required to test).

---

## File Structure

**Backend (new):**
- `backend/algo/live/__init__.py` — package marker.
- `backend/algo/live/reconciliation.py` — `ReconciliationJob` + `reconcile_user(user_id)` entry point.
- `backend/algo/live/drift_repo.py` — read/write `algo.live.drift_state`.
- `backend/algo/jobs/algo_reconciliation.py` — scheduler hook.
- `backend/algo/tests/test_reconciliation_diff.py` — synthetic drift fixtures.
- `backend/algo/tests/test_reconciliation_dedup.py` — same diff on consecutive runs emits one event, not two.
- `backend/algo/tests/test_reconciliation_resolution.py` — drift clears → `drift_resolved` once.

**Backend (modified):**
- `backend/db/migrations/versions/2026_05_11_algo_drift_state.py` — Alembic: `algo.live_drift_state` table (`user_id PK`, `symbol PK`, `first_seen_at`, `consecutive_runs`, `last_diff jsonb`, `resolved_at`).
- `backend/algo/event_writer.py` — register `position_drift_detected`, `drift_resolved` event types.
- `backend/algo/broker/kite.py` — extend `KiteClient.get_positions()` (currently stubbed; v1 only used it for the connect-test endpoint).
- `backend/scheduler.py` — register `algo_reconciliation` job (5m interval, IST market-hours only).
- `backend/algo/routes/paper.py` — extend `GET /v1/algo/paper/events?type=position_drift_detected` to expose drift events to the frontend.

**Frontend (new):**
- `frontend/components/algo-trading/ReconciliationDriftPanel.tsx` — chip + drawer listing active drifts.
- `frontend/hooks/useReconciliation.ts` — SWR over drift events.

**Frontend (modified):**
- `frontend/components/algo-trading/PaperTab.tsx` — mount `<ReconciliationDriftPanel />` next to `KillSwitchToggle` in the header.
- `frontend/components/algo-trading/SettingsTab.tsx` — drift threshold tunable (default 0; how-many-shares-of-discrepancy is "drift").

**E2E:**
- `e2e/tests/frontend/algo-trading-reconciliation.spec.ts` — synthetic drift via direct DB write; banner appears; clears when DB row resolved.

---

## High-level task list (expand at session start)

1. **`drift_state` migration + repo.**
2. **Diff function** — pure, given two snapshots `our: dict[symbol, qty]` and `broker: dict[symbol, qty]` returns `[(symbol, our_qty, broker_qty, diff)]`.
3. **`reconcile_user(user_id)`** — fetch broker via KiteClient, fetch our via repo, compute diff, upsert drift_state, emit events.
4. **Dedup logic** — drift seen first time → emit `position_drift_detected`; same diff next run → bump counter, no event; new diff → emit again; clear → emit `drift_resolved`.
5. **Scheduler integration** — `register_job("algo_reconciliation", interval_minutes=5, market_hours_only=True)`.
6. **Frontend panel** — chip with count, drawer with list, color-coded severity (yellow ≤ 3 runs, red > 3).
7. **Live-mode gate** — V2-5 will read `drift_state.consecutive_runs > 3` to disable live-mode toggle. V2-3 only writes the state; V2-5 reads.
8. **Tests** — diff function unit + dedup + resolution + market-hours scheduling.
9. **Documentation** — `docs/algo-trading/paper-trading.md` adds "Reconciliation" section.

---

## Acceptance

- [ ] Synthetic drift (broker says 100, we say 50) → exactly one `position_drift_detected` event; subsequent identical runs bump counter only.
- [ ] Drift clears → exactly one `drift_resolved` event.
- [ ] After 3 consecutive runs of unresolved drift, `algo.live_drift_state.consecutive_runs >= 3`.
- [ ] Job runs only during market hours (15:31 IST onwards is a no-op).
- [ ] Frontend panel shows live drifts; auto-clears when resolved.
- [ ] No real Kite calls in CI (mock fixtures).

---

## Out of scope for V2-3

- Auto-heal (broker-wins write-back) — v3.
- Reconciliation against paper-mode positions — only `source='live'` rows are reconciled.
- Live order placement (V2-5).
- Email/SMS alerts — v2 uses in-app banner only.
