# Regime-Aware Multi-Factor System — Slice REGIME-6: Attribution

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Build the explainability layer for the regime-aware trading system. Stamp every `signal_generated` event with feature snapshot + regime label + factor exposures at decision time. Compute daily Brinson allocation/selection attribution vs NIFTY 50. Generate per-trade reason log by joining closed trades with their entry signals. Run monthly Indian Fama-French + momentum + quality factor regression to quantify strategy exposures.

**Architecture:** No schema changes — extend `signal_generated` event payload (JSONB) with `feature_snapshot`, `regime_label`, `stress_prob`, and `factor_exposures` keys. Payload stamping happens in existing event-writer call sites (3-4 lines per site). New `attribution/` package with 4 modules (brinson, trade_log, factor_regression, job orchestrator). Two new PG tables for daily + monthly batch outputs. Daily Brinson job runs 15:30 IST (post-close). Monthly factor regression job runs 1st Sunday 04:00 IST. New `AttributionPanel` UI tab surfaces daily Brinson decomposition + per-trade reason log as a tabular view.

**Tech Stack:** Python 3.12 / statsmodels OLS for factor regression / pandas / Pydantic v2 / Next.js 16 / React 19.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.6 (attribution module full breakdown), §4.2 (PG tables), §5.1 REGIME-6 row, §6.1 REGIME-6 test row.

**Research anchor:** `docs/superpowers/research/2026-05-10-regime-aware-multifactor-research.md` — §7 (Strategy Attribution patterns: Brinson + factor regression + per-trade log).

**Branch:** `feature/algo-regime-slice-6-attribution` off `feature/algo-regime-multifactor-integration`.

**Depends on:** REGIME-1 (regime label, stress_prob available) + REGIME-2a (factor exposures in runtime context) merged on integration branch.

**Estimated SP:** 13

---

## File Structure

**Backend (new):**
- `backend/algo/attribution/__init__.py` — package marker.
- `backend/algo/attribution/brinson.py` — `compute_brinson(portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns, sector_lookup) -> dict[str, BrinsonComponents]` pure function + data models.
- `backend/algo/attribution/trade_log.py` — `build_trade_reason_log(user_id, strategy_id, bar_date, trades, signal_events) -> list[TradeReason]` + data models.
- `backend/algo/attribution/factor_regression.py` — `monthly_factor_regression(user_id, strategy_id, period_start, period_end, strategy_returns, factor_returns) -> FactorRegressionResult` + OLS fitting logic.
- `backend/algo/attribution/job.py` — `daily_brinson_job()` (15:30 IST) + `monthly_factor_regression_job()` (1st Sunday 04:00 IST) orchestrators + persistence to PG.
- `backend/algo/tests/test_brinson.py` — unit tests (paper sample decomposition, sector vs total return match).
- `backend/algo/tests/test_trade_log.py` — join correctness (entry_signal_id lookup, timestamp alignment).
- `backend/algo/tests/test_factor_regression.py` — OLS formula validation, alpha extraction, beta table shape.
- `backend/algo/tests/test_attribution_job.py` — job idempotency, re-run overwrites, graceful NaN handling.

**Backend (modified):**
- `backend/db/migrations/versions/2026_05_10_algo_attribution_daily.py` — Alembic migration: new `algo.attribution_daily` PG table (user_id, strategy_id, bar_date, brinson_alloc JSONB, brinson_select JSONB, total_active_return NUMERIC).
- `backend/db/migrations/versions/2026_05_10_algo_factor_regression.py` — Alembic migration: new `algo.factor_regression` PG table (user_id, strategy_id, period_start, period_end, alpha NUMERIC, betas JSONB, r_squared NUMERIC).
- `backend/algo/event_writer.py` — register `signal_generated_stamped` event type (or extend existing `signal_generated` with new optional payload keys).
- `backend/algo/backtest/event_writer.py` (within backtest context) — at every `emit_signal_generated()` call, inject feature snapshot + regime + factor exposures from runtime context. 3–4 call sites: (1) backtest entry signal, (2) backtest exit signal (if user-triggered), (3) paper/live entry signal via Kite integration, (4) paper/live exit (trailing stop / manual).
- `backend/algo/routes/attribution.py` — `GET /v1/algo/attribution/daily/{user_id}/{strategy_id}?date_range=...` → list of daily Brinson decompositions. `GET /v1/algo/attribution/trades/{user_id}/{strategy_id}?date_range=...` → per-trade reason log (paginated).
- `backend/algo/jobs/__init__.py` — register `daily_brinson_job` + `monthly_factor_regression_job` via `@register_job` decorator.

**Frontend (new):**
- `frontend/components/algo-trading/AttributionPanel.tsx` — two-tab sub-panel: "Daily Brinson" (table: date, allocation effect, selection effect, interaction, total active return, vs NIFTY close) + "Per-Trade Reasons" (table: entry_date, exit_date, entry_price, exit_price, pnl_pct, entry_regime, entry_factor_exposures, exit_reason text).
- `frontend/hooks/useAttribution.ts` — SWR hook wrapping `GET /v1/algo/attribution/daily` + `GET /v1/algo/attribution/trades`; 60s cache TTL.
- `frontend/utils/attribution.ts` — formatter for Brinson percentages, factor exposure display (vector → scalar via cosine sim or top-3 factors).

**Frontend (modified):**
- `frontend/components/algo-trading/TradingTab.tsx` — mount `AttributionPanel` below existing position summary (or as a sub-tab if too crowded).

**E2E:**
- `e2e/tests/frontend/algo-attribution.spec.ts` — (1) seed a signal_generated event with feature_snapshot + regime + factor_exposures in Iceberg, (2) run daily Brinson job via API call, (3) verify AttributionPanel renders Brinson table with correct decomposition, (4) verify per-trade log shows entry regime + factor exposures.

---

## High-level task list (12–14 items)

1. **Payload extension schema** — document new keys in `signal_generated` event: `feature_snapshot` (dict), `regime_label` (string), `stress_prob` (float), `factor_exposures` (dict). Backward compat: parse events without these gracefully (set to None/defaults).

2. **Event stamping at call sites** — identify all `emit_signal_generated()` calls in backtest, paper, live runtimes. Inject 3–4 new args from runtime context (regime_label, stress_prob, feature_snapshot from cache, factor_exposures from runtime exposure dict). Backfill: events emitted before v3 have None for these fields; UI surfaces "no attribution context".

3. **Brinson pure function** — implement `compute_brinson()` per spec §3.6. Accept portfolio weights, benchmark (NIFTY 50) weights, returns. Output: sector-level allocation, selection, interaction terms. Add Pydantic models for the result structure.

4. **Brinson test** — unit test vs SSRN paper sample (verify allocation + selection decomposition matches published numbers). Test edge case: sector in portfolio but not benchmark (or vice versa).

5. **PG migration: attribution_daily** — add `algo.attribution_daily` table per spec §4.2. Primary key: (user_id, strategy_id, bar_date). Columns: brinson_alloc JSONB, brinson_select JSONB, total_active_return NUMERIC.

6. **PG migration: factor_regression** — add `algo.factor_regression` table per spec §4.2. Primary key: (user_id, strategy_id, period_start, period_end). Columns: alpha NUMERIC, betas JSONB, r_squared NUMERIC.

7. **Trade log builder** — implement `build_trade_reason_log()`. Join each closed `TradeRow` (from `algo.trades` Iceberg) with the `signal_generated` event that opened it (via `entry_signal_id` + `exit_signal_id`). Extract feature_snapshot + regime + factor_exposures from event payload. Compose human-readable reason: "BUY @ 1234.5 because BULL regime, momentum 0.85 (top decile), RSI 62. Exited @ 1456 (+18%) via trailing stop."

8. **Trade log test** — join correctness (entry event + exit event both found, timestamps match, pnl calculation is consistent with trade OHLC).

9. **Factor regression OLS fit** — implement `monthly_factor_regression()`. Accept strategy daily returns + factor daily returns (Fama-French 5 from external source, add MOM + QMJ if available; else use 3-factor as fallback). Fit OLS: R_strategy = α + β1·MKT + β2·SMB + β3·HML + [β4·MOM] + ε. Extract α (unexplained alpha), all betas, R². Pydantic model for output.

10. **Factor regression test** — unit test OLS coefficients on synthetic data (known alpha + betas injected into returns, verify extraction).

11. **Daily Brinson job** — orchestrator that runs post-close (15:30 IST). For each active strategy (live + paper): fetch today's trades + signal events; call `compute_brinson()` vs NIFTY 50 weights; persist to `algo.attribution_daily`. Job is idempotent (re-run overwrites). Handle missing data gracefully (no trades today → skip; NIFTY weights unavailable → warn + skip).

12. **Monthly factor regression job** — orchestrator runs 1st Sunday 04:00 IST. For each strategy: extract last 30 days of daily P&L from `algo.runs.daily_pnl`; fetch factor returns from an external source (defer actual data wiring; assume mock data for now per spec). Fit OLS; persist to `algo.factor_regression`. Idempotent.

13. **Attribution routes** — `GET /v1/algo/attribution/daily` endpoint returns list of daily Brinson rows (paginated, filterable by user/strategy/date_range). `GET /v1/algo/attribution/trades` endpoint returns paginated trade reason log. Both filter by user_id (auth check).

14. **AttributionPanel UI component** — two-tab layout (Daily Brinson + Per-Trade Reasons). Daily Brinson tab: stacked bar chart (allocation + selection components) OR table (date, allocation %, selection %, total active %, vs NIFTY close). Per-Trade tab: sortable table (entry_date, ticker, entry_price, exit_price, holding_days, regime_at_entry, top_3_factors, pnl_pct, exit_reason). Column selector via existing `useColumnSelection` pattern (CLAUDE.md §5.4). CSV download for both tabs.

15. **Frontend integration** — mount AttributionPanel on Trading tab (below position summary or as sub-tab). Hook up `useAttribution` SWR, surface loading/error states. Lighthouse budget: no expected change (panel is lazy-loaded).

---

## Acceptance checklist

- [ ] `signal_generated` event payload extended with `feature_snapshot`, `regime_label`, `stress_prob`, `factor_exposures` keys (backward compat: parse gracefully without them).
- [ ] Daily Brinson job runs post-close (15:30 IST seeded via scheduler), produces 1 row per active strategy in `algo.attribution_daily`.
- [ ] Brinson decomposition for a seeded trade matches SSRN paper sample (allocation + selection + interaction sums to total active return).
- [ ] Monthly factor regression job runs 1st Sunday 04:00 IST, produces 1 row per strategy in `algo.factor_regression` with α + 5 factor betas + R².
- [ ] Trade reason log correctly joins entry + exit signal events; displays entry regime + factor exposures + exit reason in UI.
- [ ] AttributionPanel renders on Trading tab with two tabs (Brinson + Trade Log), both filterable by date_range.
- [ ] CSV download from both tabs works; columns match selected columns in column selector.
- [ ] No regression on existing backtest/paper/live runtimes (event payload extension is backward compat).
- [ ] E2E test: seed signal_generated events with full attribution context, run daily job, verify panel renders correct Brinson + trade log.

---

## Out of scope

- **Portfolio-level Brinson** — single-strategy single-user constraint per spec §1. Multi-strategy allocation attribution deferred to v4.
- **Real Fama-French factor data ingestion** — mock data sourced from research paper tables for now. Actual NSE-specific FF factor return data (Agarwalla/Jacob/Varma IIM-A source) deferred to follow-up if needed.
- **Per-sector regime overlay** — Brinson always uses global NIFTY 50 universe; per-sector regime classification is v4.
- **Historical factor regression backfill** — job runs monthly going forward; no retroactive 12-month backfill of factor betas for existing strategies. Strategies run in v3 will have growing regression history as months accumulate.
