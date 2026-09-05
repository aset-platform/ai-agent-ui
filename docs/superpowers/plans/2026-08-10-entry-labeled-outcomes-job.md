# PRE-6 — entry_labeled_outcomes daily rollup job — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Build the recurring daily job that materializes `algo.entry_labeled_outcomes` (schema already migrated, `2026_08_10_labeled_outcomes`) from live data — the steady-state labeling pipeline feeding R2. Schedule standalone at 16:00 IST (Mon–Fri); to be clubbed into the India pipeline later.

**Architecture:** Mirror `backend/algo/jobs/closed_trades_rollup.py` exactly: sync entrypoint → `asyncio.run(_run)`, reads Iceberg via `query_iceberg_table` (never during market hours), writes PG via `disposable_pg_session`, idempotent upsert on the natural key over a trailing window (re-materialize handles late/settling outcomes). Register in `backend/jobs/executor.py` + `_algo_job_success`; schedule via a seed script writing `scheduled_jobs`.

**Tech Stack:** Python 3.12, pytest (Docker: `docker compose exec -e PYTHONPATH=.:backend backend python -m pytest <path> -v`).

## Global Constraints
- Line ≤79; `X | None`; no bare `except`; caught exceptions in this long-running job log with `exc_info=True`. black/isort unavailable — format by hand.
- Reads Iceberg (`algo.events`, `stocks.entry_quality_daily`, `stocks.intraday_bars`, `stocks.ohlcv`) via `query_iceberg_table`; writes PG via `disposable_pg_session`. Never `_pg_session()` in loops.
- Idempotent upsert on `ON CONSTRAINT uq_entry_labeled_outcomes_signal` (natural key: user_id, strategy_id, ticker, trade_date, mode).
- Standalone job wrappers in executor.py MUST call `_algo_job_success(repo, run_id)` on success (per `.claude/rules/algo.md`).
- **Scale/units:** snapshot payload `dist_sma50`/`dist_sma200` are FRACTIONS (e.g. 0.2555) → multiply ×100 into the `_pct` columns; `ret_3d_pct`/`gap_pct` in the snapshot are already percent → store as-is.
- **v1 scope:** filled candidates → real aggregated outcome (settled); rejected candidates → features + rejection_reason, outcome NULL, `outcome_settled=false`, `outcome_kind='counterfactual'` (values deferred to PRE-3). Do NOT attempt exit-simulated counterfactuals here.

---

### Task 1: The rollup module

**Files:**
- Create: `backend/algo/jobs/entry_labeled_outcomes_rollup.py`
- Test: `backend/algo/jobs/tests/test_entry_labeled_outcomes_rollup.py` (new; create dir/`__init__.py` if needed — mirror where `closed_trades_rollup` tests live)

**Interfaces produced:**
- `run_entry_labeled_outcomes_rollup_job(payload: dict | None = None) -> dict` — sync entrypoint (`asyncio.run(_run)`). Returns `{"rows_upserted": int, ...}`.
- `_run(payload) -> dict` — async core.

**Logic (mirror closed_trades_rollup's structure):**
1. `window_days = payload.get("window_days", 400)`; `today = _ist_today()`; `start = today - window_days`.
2. **Candidates (features):** `query_iceberg_table("algo.events", ...)` for `type='entry_strength_snapshot'` in the window. Group by `(strategy_id, mode, payload.ticker, ts_date)`; keep the FIRST snapshot per group (min ts_ns) as representative. Extract: `trigger`, `rsi2_forming`→`rsi2_at_entry`, `dist_sma50`×100→`dist_sma50_pct`, `dist_sma200`×100→`dist_sma200_pct`, `ret_3d_pct`, `gap_pct`, `breadth_oversold`, `breadth_total`, `signal_ts_ns`, `user_id`.
3. **Rejections:** `query_iceberg_table("algo.events", ...)` for `type='signal_rejected'` in the window; per `(strategy_id, mode, ticker, ts_date)` take a representative `payload.reason` → `rejection_reason`.
4. **Fills:** read `algo.closed_trades` (PG) for the strategy(ies)+modes in the window. Group by `(user_id, strategy_id, ticker, opened_at, mode)` and AGGREGATE multi-lot: `qty=Σqty`, `entry_price=Σ(avg_price·qty)/Σqty`, `exit_price=Σ(fill_price·qty)/Σqty`, `realised_pnl_inr=Σpnl`, `return_pct=(exit/entry-1)·100`, `opened_at_ts_ns=min`, `closed_at_ts_ns=max`, `exit_reason`=last, `buy_event_id`/`sell_event_id`=first. (KTKBANK 2026-06-24 x3, WABAG 2026-07-17 x2 are the known multi-lot cases — a test MUST cover aggregation.)
5. **QM/ESS:** `query_iceberg_table("stocks.entry_quality_daily", ...)` join by `(ticker, trade_date)` → qm_score, ess_score, ess_gate_passed, qm_mdd_pctile, qm_rs_pctile, qm_sharpe_pctile, ess_absorption_volume_score, ess_selling_deceleration_score, ess_trend_stability_score.
6. **MFE/MAE (filled only):** intraday 15m bounded by `opened_at_ts_ns..closed_at_ts_ns` (max high/min low → pct vs entry), daily-ohlcv fallback; `outcome_src` ∈ {intraday15m, daily, none}. (Reuse the approach from `.superpowers/sdd/2026-08-09-.../` PRE-1 script logic — replicate, don't import scratch.)
7. **Assemble labeled rows** keyed on `(user_id, strategy_id, ticker, trade_date, mode)`, unioning candidates:
   - has a fill → `filled=true`, real outcome fields, `outcome_kind='real'`, `outcome_settled=true`, `label_win = realised_pnl_inr > 0`, `rejection_reason=null`.
   - no fill → `filled=false`, `rejection_reason` (from step 3), outcome fields NULL, `outcome_kind='counterfactual'`, `outcome_settled=false`, `label_win=null`.
   - A candidate row REQUIRES a snapshot (features). A fill with no snapshot (pre-R1 history) still materializes with features NULL — but for the recurring job every fill has a snapshot; note in a comment.
8. **Upsert** each row into `algo.entry_labeled_outcomes` via `INSERT ... ON CONFLICT ON CONSTRAINT uq_entry_labeled_outcomes_signal DO UPDATE SET ...` inside `disposable_pg_session`. `dry_run` payload → compute+log counts, skip write (mirror closed_trades_rollup).

- [ ] **Step 1: Write failing tests** — construct a small synthetic set (mock `query_iceberg_table` + a seeded `algo.closed_trades`/temp rows, or factor the pure assembly/aggregation into a helper fed dicts and unit-test that): (a) a filled single-lot candidate → 1 real settled win/loss row with correct scaled features (dist ×100); (b) a multi-lot same-day fill (KTKBANK-style, 3 lots) → ONE aggregated row (Σpnl, weighted entry); (c) a snapshot-only rejected candidate → filled=false, rejection_reason set, outcome NULL/unsettled; (d) idempotency: running twice upserts, not duplicates (row count stable). Prefer a pure `_assemble_rows(...)` helper for (a)-(c) + one integration-style test for (d).
- [ ] **Step 2: Run tests (Docker) — verify fail.**
- [ ] **Step 3: Implement the module** per the logic above.
- [ ] **Step 4: Run tests (Docker) — verify pass.**
- [ ] **Step 5: Smoke on real dev data** — `run_entry_labeled_outcomes_rollup_job({"window_days": 400})` in the container; confirm `algo.entry_labeled_outcomes` row count is sane (>= the 39 PRE-1 seeded, now incl. rejected candidates) and re-run is idempotent (count stable). Report counts.
- [ ] **Step 6: flake8 (zero new), commit** (`feat(algo): entry_labeled_outcomes daily rollup job`; co-author trailer; no push).

---

### Task 2: Register + schedule (16:00 IST daily)

**Files:**
- Modify: `backend/jobs/executor.py` (new `@register_job("algo_entry_labeled_outcomes_rollup")` wrapper → calls `run_entry_labeled_outcomes_rollup_job(payload)` then `_algo_job_success(repo, run_id)` — mirror `_job_algo_reconciliation` at ~L3598)
- Create: `scripts/seed_entry_labeled_outcomes_rollup.py` (mirror `scripts/seed_closed_trades_rollup.py`: upsert a `scheduled_jobs` row, stable `uuid5` id, `ON CONFLICT (name) DO UPDATE`)
- Test: `backend/jobs/tests/` — assert the job type is registered/dispatchable.

**Schedule values:** `job_type="algo_entry_labeled_outcomes_rollup"`, `name="Algo Entry Labeled Outcomes Rollup - Daily"`, `cron_days="mon,tue,wed,thu,fri"`, `cron_time="16:00"`, `cron_dates=None`, `scope=None`, `enabled=TRUE`.

- [ ] **Step 1: Register the executor wrapper** (mirror `_job_algo_reconciliation`: resolve repo/run_id, call the rollup, then `_algo_job_success(repo, run_id)`; log with exc_info on failure).
- [ ] **Step 2: Test** the job type is registered (mirror an existing executor registration test) — run (Docker).
- [ ] **Step 3: Write the seed script** (copy `seed_closed_trades_rollup.py`, swap name/job_type/cron_time=16:00; keep a NEW stable uuid namespace/name).
- [ ] **Step 4: Run the seed** in the container; confirm the `scheduled_jobs` row exists with `cron_time=16:00`, enabled. (Note in the report: a backend restart is needed for the scheduler to pick up a new scheduled_jobs row — do NOT restart; flag for the user, since restart drops the live Kite WS.)
- [ ] **Step 5: flake8 (zero new), commit** (`feat(algo): register + schedule entry_labeled_outcomes rollup 16:00 IST`; no push).

## Self-Review
- Coverage: reads all needed sources; scale ×100 on dist; multi-lot aggregation tested; rejected candidates recorded; idempotent; registered + scheduled; `_algo_job_success` called.
- Deferred (to PRE-3): counterfactual outcome values for rejected candidates. Documented on the row as `outcome_kind='counterfactual'`, `outcome_settled=false`.
- Restart caveat surfaced (scheduler picks up new row on restart; user coordinates — Kite WS).
