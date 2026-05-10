# Algo Trading v2 — Slice V2-5: Live Order Placement (incl. V2-4 safety belts)

> **STATUS:** SKELETON — this is the largest, riskiest v2 slice. Expand into a full TDD task-by-task plan via `superpowers:writing-plans` only after V2-0 + V2-1 + V2-2 + V2-3 are merged and clean. Plan a focused single-session push (1 long day or 2 half days) — do NOT spread across many sessions.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Implement `KiteAdapter.place_order` / `cancel_order` / `modify_order` (today's `NotImplementedError` stubs), add the `algo.live_caps` safety-belts table + UI, and wire a `live` mode into the paper runtime so the same engine that paper-trades can place real orders. Ship in DEFAULT-OFF state — live trading requires explicit per-strategy opt-in via cap form + walk-forward gate.

**Architecture:** Mirror of `paper.runtime` for the order-emission path; replace `PaperBroker.execute()` with `KiteAdapter.place_order()`. Safety belts (`live_caps`) sit between the runtime and the adapter as a pre-trade-check. Risk engine output becomes binding (v1 paper was advisory). Kill-switch hot path now also cancels in-flight orders.

**Tech Stack:** Python 3.12 / `kiteconnect` SDK REST / SQLAlchemy 2.0 async / `responses` (mocked Kite REST in tests) / pytest. New `live` PaperTab mode toggle.

**Spec:** `docs/superpowers/specs/2026-05-09-algo-trading-v2-design.md` — Slice V2-5 (§5, §7.1, §9.1, §11, §13).

**Branch:** `feature/algo-trading-v2-slice-5-live-orders` off `feature/algo-trading-v2-integration`.

**Depends on:** **V2-0 + V2-1 + V2-2 + V2-3 all merged** — non-negotiable. Live orders without WS = blind; without walk-forward = no validation gate; without reconciliation = silent state drift.

---

## File Structure

**Backend (new):**
- `backend/algo/live/runtime.py` — `LiveRuntime` mirroring `PaperRuntime` but routing fills through KiteAdapter.
- `backend/algo/live/safety.py` — `pre_trade_check(signal, caps, day_state)` returning `LivePreTradeDecision`.
- `backend/algo/live/caps_repo.py` — CRUD over `algo.live_caps`.
- `backend/algo/routes/live.py` — `/v1/algo/live/start|stop|caps|status` endpoints.
- `backend/algo/tests/test_kite_place_order.py` — mocked Kite REST happy path + rejection.
- `backend/algo/tests/test_kite_cancel_order.py` — cancel during in-flight.
- `backend/algo/tests/test_live_pre_trade_check.py` — every cap rejects.
- `backend/algo/tests/test_live_kill_switch.py` — armed kill cancels in-flight orders.
- `backend/algo/tests/test_live_walkforward_gate.py` — toggle disabled if no walk-forward in last 30 days.
- `backend/algo/tests/test_live_drift_gate.py` — toggle disabled if drift > 3 runs.

**Backend (modified):**
- `backend/db/migrations/versions/2026_05_12_algo_live_caps.py` — Alembic: `algo.live_caps` table.
- `backend/algo/broker/kite.py` — implement `place_order`, `cancel_order`, `modify_order`; only MARKET + LIMIT; only CNC; only `regular` variety.
- `backend/algo/paper/risk_engine.py` — gain a `binding: bool` flag; when binding=True, breaches return reject (v1 already does this); when binding=True with kill-switch armed, ALSO call `cancel_in_flight_orders(user_id)`.
- `backend/algo/event_writer.py` — register `order_submitted_live`, `order_acknowledged_live`, `order_filled_live`, `order_rejected_live`, `order_cancelled_live`.
- `backend/algo/jobs/algo_reconciliation.py` (V2-3) — extend to read `live_caps.live_orders_enabled` so reconciliation only runs for users with active live strategies.
- `backend/scheduler.py` — register `algo_live_caps_daily_reset` job at 09:00 IST (resets `cumulative_inr_today` + `orders_count_today` columns on `algo.live_caps`).

**Frontend (new):**
- `frontend/components/algo-trading/LiveSafetyBeltsForm.tsx` — per-strategy cap form (max_inr, max_orders_per_day, allowed_tickers chips, walk-forward link).
- `frontend/components/algo-trading/LiveModeToggle.tsx` — segment control with 4-gate validation; 2-step confirm modal on flip.
- `frontend/components/algo-trading/LiveLandedOrdersList.tsx` — recent live fills.
- `frontend/components/algo-trading/LiveCancelInFlightBanner.tsx` — surfaces "kill armed: N orders cancelled, M positions held".
- `frontend/hooks/useLiveCaps.ts` / `useLiveStatus.ts` / `useLiveOrders.ts`.

**Frontend (modified):**
- `frontend/components/algo-trading/PaperTab.tsx` — host the LiveModeToggle + LiveSafetyBeltsForm; show the live banner when in Live mode.
- `frontend/components/algo-trading/ActiveRunsPanel.tsx` — disable start-run when in Live mode unless caps are set + gates pass.

**E2E:**
- `e2e/tests/frontend/algo-trading-live-mode-gates.spec.ts` — walk through the 4-gate validation.
- `e2e/tests/frontend/algo-trading-live-kill-cancels.spec.ts` — synthetic in-flight order; arm kill; verify cancel.
- `e2e/live-smoke/algo-live-real-kite.spec.ts` — opt-in via `RUN_LIVE_SMOKE=1`; runs against Kite paper account; full round-trip.

---

## High-level task list (expand at session start)

### Phase A — Adapter (no caps yet)

1. `KiteAdapter.place_order` REST call against mocked Kite.
2. `KiteAdapter.cancel_order`.
3. `KiteAdapter.modify_order` (price + qty for LIMIT only).
4. Order-status polling — `kite.orders()` reconciliation post-submit.
5. Map Kite rejection reasons into `order_rejected_live` payload.

### Phase B — Caps + safety

6. `algo.live_caps` migration + repo.
7. `pre_trade_check(signal, caps, day_state)` — every cap; ordered by cheapness.
8. `live_caps_daily_reset` scheduler job (resets cumulative counters at market open).

### Phase C — Runtime

9. `LiveRuntime` skeleton — copy `PaperRuntime`, replace broker, add pre-trade-check.
10. Risk engine `binding=True` integration.
11. Kill-switch hot path → `cancel_in_flight_orders(user_id)`.
12. Order in-flight tracking — `algo.runs.live_orders_in_flight` JSONB column or new `algo.live_orders` table (TBD; lean toward column for MVP).

### Phase D — UI

13. `LiveSafetyBeltsForm` — form + validation + submit.
14. `LiveModeToggle` — gate logic (Kite connected / caps set / kill disarmed / walk-forward < 30 days / drift < 3 runs).
15. 2-step confirm modal — strategy name retype.
16. Live banner on toggle flip into Live mode.
17. Cancel-in-flight banner on kill arm.

### Phase E — Live ramp prep (no code, just ops)

18. Document the manual ramp procedure in `docs/algo-trading/live-ramp.md` — ₹1k → ₹10k → ₹50k → user-set across 7 days, with check-in checklist.
19. Author the user-facing first-live-trade README section.

---

## Acceptance

- [ ] `KiteAdapter.place_order` against mocked Kite places an order, gets a `kite_order_id`, persists `order_submitted_live` event.
- [ ] Order rejection (insufficient funds, market closed, etc.) emits `order_rejected_live` with the actual Kite reason; runtime continues.
- [ ] Each of the 9 cap layers rejects appropriately (3 v1 layers × 3 v2-new layers); ordered short-circuit.
- [ ] Kill-switch arm during active live run cancels every in-flight order; positions stay held.
- [ ] Live-mode toggle disabled with explicit tooltip for each closed gate.
- [ ] Walk-forward report > 30 days old → toggle disabled.
- [ ] Drift > 3 runs → toggle disabled.
- [ ] CI passes without real Kite credentials (mocked REST).
- [ ] `RUN_LIVE_SMOKE=1` Playwright project runs end-to-end against Kite paper account.
- [ ] Default state on merge: `live_orders_enabled=false` for all users; even superuser must opt in.

---

## Risks (slice-specific)

This is the slice where mistakes cost money. Read CLAUDE.md §5 (risk engine) and the v2 spec §13 (Risks & Mitigations) before starting.

- **Test mocks diverge from real Kite** — verify against the live smoke project before any merge to `dev`.
- **Concurrent kill + new order race** — kill-check MUST happen inside the same async task that submits the order; never trust a stale Redis read.
- **In-flight cancellation is best-effort** — Kite cancel API can fail; log every failure as `order_cancel_failed` so manual cleanup is possible.
- **Live mode toggle accidentally flipped** — 2-step confirm modal + 4-gate disable + retype-strategy-name validation.
- **Cap arithmetic wrong** — exhaustively test the per-cap math; a "₹100k cap" off-by-one means a ₹200k order slips through.
- **Day-boundary cutover** — `cumulative_inr_today` must reset at market open (09:00 IST), NOT calendar midnight, to match Kite's day boundary.

---

## Out of scope for V2-5

- F&O, options, futures, MIS/NRML — equity CNC delivery only.
- Order types beyond MARKET/LIMIT.
- Multi-strategy live for a single user (the schema accommodates; API gate stays).
- Auto-flatten on kill (kill stops new orders + cancels in-flight; held positions stay).
- Auto-heal reconciliation drift.
- Cross-strategy capital allocator.
