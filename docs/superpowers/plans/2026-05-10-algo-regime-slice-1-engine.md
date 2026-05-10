# Regime-Aware Multi-Factor System — Slice REGIME-1: Regime Engine

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session starts.

> **For agentic workers:** RECOMMENDED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Build the core regime classifier that daily labels each trading day as BULL / SIDEWAYS / BEAR, and surface regime context in the live runtime. This is the foundation slice — it unblocks all downstream factor library, sizing, walk-forward, and attribution work. End with regime widget visible in the Trading tab header and `regime_label` + `stress_prob` features available to strategy ASTs.

**Architecture:** Rule-based primary classifier (NIFTY vs SMA200, India VIX bands, momentum, breadth) outputs a deterministic label; 2-state Gaussian HMM on log-returns + realized vol provides a stress-probability overlay (advisory, not decision-driven). Both persisted nightly into `stocks.regime_history` Iceberg table. HMM state (`transmat`, `means`, `covars`) stored in `stocks.regime_hmm_state` (monthly refit, 1st Sunday 04:00 IST, warm-started). Runtime fetches cached regime + stress_prob on market open; backend routes return regime for UI consumption.

**Tech Stack:** Python 3.12 / FastAPI / hmmlearn / Pydantic v2 / PyIceberg (Iceberg append-only analytics per CLAUDE.md §5.1). Frontend: Next.js 16 / React 19 / ECharts (regime ribbon gauge). Tests: pytest (Python), Playwright (E2E).

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §1 Goals (regime engine bullet), §3.1 (rule-based classifier), §3.2 (HMM overlay), §4.1 (`stocks.regime_history` + `stocks.regime_hmm_state` tables), §4.3 (^INDIAVIX + sector index ingest), §5.1 REGIME-1 row.

**Research Anchor:** `docs/superpowers/research/2026-05-10-regime-aware-multifactor-research.md` — §1 (hybrid rule + HMM, India VIX bands per research), §2 (NSE breadth thresholds: >55% above 200SMA, >65% above 50SMA = healthy bull).

**Branch:** `feature/algo-regime-slice-1-engine` off `feature/regime-multifactor-integration` (created from `dev` after v2 integration PR lands).

**Depends on:** v2 integration → `dev` PR merged first (all backtest/paper/live runtimes working). NO other dependencies.

**Estimated SP:** 13

---

## File Structure

**Backend (new):**
- `backend/algo/regime/__init__.py`
- `backend/algo/regime/rule_based.py` — pure function `classify_regime(nifty_close, nifty_sma200, vix_close, nifty_ret_30d, nifty_ret_60d, pct_above_50sma) → str` (BULL | SIDEWAYS | BEAR).
- `backend/algo/regime/hmm_overlay.py` — `StressHMM` class with `fit()`, `stress_prob()`, `save()`, `load()`. **Anti-look-ahead guard: forward-only filtering via `predict(X[:t+1])` only, never full-sample `predict(X)`.**
- `backend/algo/regime/classifier_job.py` — daily orchestrator (22:30 IST), reads close + SMA200 + VIX + returns + breadth, classifies, appends to `stocks.regime_history` + invalidates cache.
- `backend/algo/regime/repo.py` — write/read `stocks.regime_history` via PyIceberg.
- `backend/algo/regime/tests/` — unit tests for classifier + HMM + job orchestration + anti-look-ahead.

**Backend (modified):**
- `backend/pipeline/runner.py` — add `^INDIAVIX` to daily OHLCV ingest (NEW ticker, required for regime classifier).
- `backend/pipeline/jobs/ohlcv.py` — ensure `^INDIAVIX` + NIFTY sector indices (`^NSEBANK`, `^CNXIT`, `^CNXAUTO`, `^CNXPHARMA`, `^CNXFMCG`, `^CNXMETAL`, `^CNXENERGY`, `^CNXREALTY`, `^CNXPSUBANK`, `^CNXFINANCE`, `^NIFMDCP150`) are fetched daily. (Prerequisite for downstream factor library, relative strength, breadth.)
- `backend/algo/strategy/features.py` — register `regime_label` (string), `stress_prob` (float), `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio`, `vix_close`, `vix_sma_20` as runtime features (used by strategies + factor library downstream).
- `stocks/create_tables.py` — Alembic migration: add `stocks.regime_history` (partition by year(bar_date), PK bar_date) + `stocks.regime_hmm_state` (unpartitioned, ~12 rows/yr).
- `backend/algo/routes/regime.py` — NEW `GET /v1/algo/regime/current`, `GET /v1/algo/regime/history`, `GET /v1/algo/regime/classifier-health` (HMM refit status, stress divergence).
- `backend/algo/jobs/__init__.py` — register `regime.classifier_job` via `@register_job` decorator.

**Frontend (new):**
- `frontend/components/algo-trading/RegimeWidget.tsx` — mounted in Trading tab header. Displays current `regime_label` (color-coded badge: green=BULL, gray=SIDEWAYS, red=BEAR), `vix_close` gauge (red/orange/green bands), `pct_above_200sma` bar chart (healthy >55%), `stress_prob` chip with delta vs rule-based prediction (amber warn if HMM diverges >0.3). Tooltip on divergence chip: "Rule says BULL, HMM stress 0.62 — safeguard active."
- `frontend/components/algo-trading/RegimeHistoryChart.tsx` — color-ribbon overlay on a historical NIFTY price chart (ECharts area stack or band) showing regime transitions: BULL = green, SIDEWAYS = gray, BEAR = red. Paired with NIFTY 50 price series; hover details regime + stress_prob for date.
- `frontend/hooks/useRegime.ts` — SWR hook, `GET /v1/algo/regime/current` + `GET /v1/algo/regime/history`, 60s TTL, revalidateOnFocus: false.

**Frontend (modified):**
- `frontend/components/algo-trading/PaperTab.tsx` — mount `RegimeWidget` in the Trading tab header (alongside strategy selector).

**Tests:**
- `backend/algo/regime/tests/test_classify_regime.py` — table-driven tests: BULL (above SMA200 + calm/normal VIX + bullish momentum + healthy breadth) → "BULL", BEAR (below SMA200 + stress VIX + bearish momentum) → "BEAR", else → "SIDEWAYS". Cover edge cases: missing VIX, momentum just at threshold, breadth rollover.
- `backend/algo/regime/tests/test_hmm_overlay.py` — fit test, prediction test, **`test_hmm_filtered_no_lookahead` (critical gate: last-day prediction via `predict(X[:t+1])` must match manual last-state forward filter)**. Test label stability (refit month-to-month).
- `backend/algo/regime/tests/test_classifier_job.py` — orchestrator reads live OHLCV + macro inputs, classifies, writes Iceberg, invalidates cache.
- `backend/algo/regime/tests/test_vix_ingest.py` — ^INDIAVIX ingest handles yfinance flakes (missing data, NaN closes); fallback graceful.
- `backend/algo/regime/tests/test_feature_registration.py` — regime + breadth + VIX features registered in strategy features module.

**E2E:**
- `e2e/tests/frontend/algo-trading-regime-widget.spec.ts` — seeded regime row, navigate Trading tab, verify RegimeWidget renders correct label + color + VIX band + breadth bar. Hover divergence chip, tooltip visible.

---

## High-level task list (expand at session start)

1. **^INDIAVIX ingest** — add to daily OHLCV pipeline; yfinance fetch + error handling for missing data; fallback to SIDEWAYS when VIX stale >2d. (NEW PREREQUISITE task, critical blocker for classifier.)
2. **Rule-based classifier function** — pure function table-driven by thresholds from research synthesis; all inputs Decimal (NIFTY closes, SMA200, VIX, returns, breadth). Happy path + NaN guards.
3. **HMM class + fit/predict** — 2-state Gaussian HMM on (log-return, realized_vol_20d); monthly refit with warm-start `transmat_`; stress_prob output; **forward-only prediction test as CI gate.**
4. **Iceberg tables** — `stocks.regime_history` (daily), `stocks.regime_hmm_state` (monthly).
5. **Repo layer** — PyIceberg CRUD for regime_history + regime_hmm_state; NaN-replaceable upsert pattern (pre-delete NaN rows for incoming dates).
6. **Daily classifier job** — orchestrator 22:30 IST; reads OHLCV + macro, classifies, writes Iceberg, invalidates `cache:regime:*`.
7. **Feature registration** — `regime_label`, `stress_prob`, breadth, VIX keys added to `strategy/features.py`.
8. **API endpoints** — 3 GET routes: `/current` (latest regime + stress), `/history` (last 252 days), `/classifier-health` (HMM refit time, stress divergence alert).
9. **RegimeWidget component** — React functional component; color-coded badge, VIX gauge, breadth bar, stress chip, hover tooltips.
10. **RegimeHistoryChart component** — ECharts area/band overlay; regime ribbon + NIFTY price.
11. **useRegime hook** — SWR fetch + 60s polling.
12. **PaperTab mount** — integrate RegimeWidget into Trading tab header.
13. **Unit + E2E tests** — table-driven classifier tests, HMM forward-filter gate, ingest tests, component E2E.

---

## Acceptance Checklist

- [ ] `^INDIAVIX` + sector indices ingest daily; 30-day backfill succeeds; yfinance flake handled gracefully.
- [ ] `classify_regime()` returns "BULL" for NIFTY > SMA200 + calm VIX + bullish momentum + healthy breadth; "BEAR" for NIFTY < SMA200 + stress VIX + bearish momentum; else "SIDEWAYS".
- [ ] `stocks.regime_history` table appended nightly; `test_classifier_writes_iceberg` passes.
- [ ] HMM forward-only filtering test (`test_hmm_filtered_no_lookahead`) passes as CI gate — last-day prediction must NOT use future data.
- [ ] `RegimeWidget` renders in Trading tab header; background color matches regime label (green/gray/red); HMM stress chip shows and tooltips on hover.
- [ ] `GET /v1/algo/regime/current` returns `{ regime_label, stress_prob, vix_close, pct_above_200sma, ... }` in <50ms (Redis cache).
- [ ] No regression on existing backtest / paper / live runtimes (regime features available but strategies not yet bound to regimes).
- [ ] Regime-stratified test on 252d historical data: BULL, SIDEWAYS, BEAR each appear ≥10 days; HMM agrees with rule-based ≥80% of the time.

---

## Out of Scope for REGIME-1

- **Factor library backend** — deferred to REGIME-2a (depends on regime + breadth features from this slice).
- **Strategy↔regime binding (metadata + selector + AST `regime_eq`)** — deferred to REGIME-3 (needs regime feed live from REGIME-1, NOT yet used).
- **Volatility-targeted sizing** — deferred to REGIME-4 (reads `realized_vol_60d` from REGIME-2a's factor store).
- **Walk-forward extension + DSR/PBO** — deferred to REGIME-5.
- **All factor library dependencies (momentum, quality, low-vol, trend, volume, RS, breadth compute)** — marked out-of-scope: "REGIME-2a backend infra", "REGIME-2a factor compute", respectively. Only breadth OUTPUT features (`pct_above_50sma`, etc.) are registered as inputs to the regime classifier in this slice.
- **Attribution + Brinson + per-trade reason log** — deferred to REGIME-6.
