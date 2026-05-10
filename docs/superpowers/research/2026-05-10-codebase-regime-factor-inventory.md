# Codebase Inventory: Regime + Factor Surface (2026-05-10)

> **Source:** Explore subagent scan, 2026-05-10. Anchor for the
> Regime-Aware Multi-Factor System spec. Re-scan via the same prompt
> in `feature_subagent_grep_preflight` if the codebase shifts >1 sprint
> from this date.

## Summary table — what we have vs need

| Component | Status | Notes |
|-----------|--------|-------|
| Market regime (NIFTY SMA200) | ✅ EXISTS | Binary signal pre-loaded; injected in all 3 runtimes |
| Market trend (30d return %) | ✅ EXISTS | Per-bar feature; configurable lookback |
| Per-ticker SMA20/50/200 | ✅ EXISTS | Computed on-the-fly; O(N) rolling sum |
| Golden cross | ✅ EXISTS | SMA50 > SMA200 tracking with reset |
| RSI / MACD / ATR / Bollinger | ⚠️ PARTIAL | In `backend/tools/_analysis_indicators.py` only — NOT runtime-integrated |
| Breadth / sector signals | ❌ MISSING | No runtime breadth metrics |
| Feature store | ❌ MISSING | No Iceberg cache of indicators |
| Walk-forward CV | ✅ EXISTS | V2-2 — fixed windows; no regime stratification |
| AST grammar | ✅ EXISTS | Extensible; `regime_eq` needs feature dict support |
| Universe selection | ✅ EXISTS | Static scope-based; no rolling universe |
| Position sizing | ⚠️ PARTIAL | Fixed-qty only; vol-targeted missing |
| Trade attribution | ❌ MISSING | No causal link from condition → trade |

## 1. Regime features wired today

**Computed:**
- `backend/algo/backtest/indicators.py:197-235` — `compute_market_regime()`: NIFTY close > SMA(200) → Decimal(1/0) per bar_date
- `backend/algo/backtest/indicators.py:153-190` — `compute_market_trend_strength()`: NIFTY 30-day return % over configurable lookback

**Injected into runtimes:**
- Backtest: `backend/algo/backtest/runner.py:119-126` (pre-loaded at run start; merged into `ticker_features` per bar at lines 190-195)
- Paper: `backend/algo/paper/runtime.py:90-116` (3-year warmup window at `__init__`; cached in `_market_regime` / `_market_trend`)
- Live: `backend/algo/live/runtime.py:127-147` (identical pattern)

**AST allowlist:**
- `backend/algo/strategy/features.py:82-92` — `nifty_above_sma200`, `nifty_30d_return_pct`
- Validation at parse via `FEATURE_KEYS` frozenset (line 148)

**Frontend catalog:**
- `frontend/components/algo-trading/strategyFeatureCatalog.ts:34-35`

**Default fallback:** missing regime data → `Decimal("0")` so strategies gated on `> 0` degrade gracefully (lines 191, 201-206).

**Gaps:** no bear-regime label; no sector-rotation signals; no VIX-bucketed volatility regime.

## 2. Per-ticker per-bar features (current set)

| Feature | Status | Computed where |
|---|---|---|
| `today_ltp` | ✅ | bar close |
| `today_vol` | ✅ | bar volume |
| `sma_20`, `sma_50`, `sma_200` | ✅ | `compute_indicators()` rolling sum |
| `golden_cross_days_ago` | ✅ | tracker w/ reset; sentinel 999 |
| `prev_day_ltp` | ❌ stub in catalog only |
| `today_x_vol` (vs avg) | ❌ stub in catalog only |
| `away_from_52week_high` | ❌ stub in catalog only |
| `rsi` | ❌ stub in catalog only |
| `today_dpc` (delivery %) | ❌ stub in catalog only |

CI gate: `backend/algo/tests/test_feature_registry_sync.py` enforces frontend ↔ backend FEATURE_KEYS sync.

## 3. Indicator engine reach

In runtime: SMA20/50/200, golden cross, NIFTY SMA200, NIFTY 30d return.

Tools-only (NOT runtime-integrated): SMA50/200, EMA20, RSI14, MACD+Signal+Histogram, Bollinger Bands, ATR14, Ease-of-Movement at `backend/tools/_analysis_indicators.py:21-150` (uses `ta` library).

**Missing entirely:** ADX, Stochastics, CCI, OBV, accumulation/distribution.

## 4. Walk-forward harness (V2-2)

`backend/algo/backtest/walkforward.py`

| Function | Signature | Purpose |
|----------|-----------|---------|
| `walk_windows()` | `(start, end, *, train_days, test_days, step_days) → list[Window]` | Generate (train, test) window pairs |
| `run_walkforward_job()` | `(walkforward_run_id, user_id, config, strategy, universe) → None` | Execute as async job |
| `_aggregate_windows()` | `(summaries) → WalkForwardAggregate` | Mean/std across completed windows |

Trailing partial windows dropped, not truncated. Test windows run through the full backtest runner.

Per-window: status, total_pnl_pct, win_rate, max_drawdown_pct, equity_curve.
Aggregate: avg_win_rate, avg_pnl, avg_max_drawdown, std_pnl, window_count.

**Gap for regime work:** no per-window regime label, no per-regime metric split, no regime-stratified train/test splits.

## 5. Strategy AST grammar

Node families: condition (`compare`, `and`, `or`, `not`, `crossover`, `between`), action (`buy`, `sell`, `exit`, `hold`, `set_target_weight`), composite (`if`, `select_top_n`, `weighted`).

Operand leaves: `FeatureRef` (validated against `FEATURE_KEYS`), `Literal_`.

**Adding regime predicate:** can be done WITHOUT grammar surgery if we add a string-typed feature like `regime_label` ("bull" | "sideways" | "bear") to the registry — then use existing `compare` with string operand. (Today all features are int/float Decimal; need to extend evaluator to handle string compare.)

## 6. Universe selection

`backend/algo/backtest/universe.py:80-120` — `resolve_universe()`:
- `discovery` scope: full universe (pro/superuser); general gets watchlist ∪ holdings
- `watchlist`: watchlist ∪ holdings
- `portfolio`: holdings only
- Unknown → watchlist (safe non-empty default)

Optional `strategy.universe.filter` with `ticker_type` and `market`.

**Gap for regime work:** universe is a static snapshot at run start. No rolling universe (per-window add/drop), no regime-conditional universe ("only large-caps in bear").

## 7. Position sizing today

| Mode | AST syntax | Status |
|---|---|---|
| Fixed shares | `qty: {shares: 10}` | ✅ |
| Fixed INR | `qty: {notional_inr: 50000}` | ❌ stub only |
| Sell all | `qty: {all: true}` | ✅ |

RiskEngine.gate() can scale qty (`accept | scale | reject` + optional `adjusted_qty`) but cannot dynamically size on volatility.

**Gap:** no ATR%-based sizing, no portfolio-vol bucketing, no Kelly fraction, no drawdown-throttled sizing.

## 8. Breadth / sector signals — MISSING

No runtime metrics for:
- % stocks above SMA50/200
- Advance/decline counters
- Sector relative strength (NIFTY50 vs Midcap200 vs Smallcap250)
- Sector index momentum
- FII/DII flow

Frontend insights (Sectors tab) computes some of these client-side but they're NOT fed back into the runtime feature dict.

No daily breadth/macro batch job exists.

## 9. Feature store — MISSING (computed on the fly)

Backtest: `compute_indicators_for_universe()` once at start (line 114 in runner). Paper/live: per-bar O(N) recompute over full ticker history.

No `stocks.technical_indicators` table. No Redis cache. No Iceberg pre-computed indicators table.

**Implication:** paper/live indicator compute is the per-bar bottleneck once we add ATR/MACD/RSI. Pre-batched feature store becomes valuable when factor library lands.

## 10. Trade attribution — MISSING

`TradeRow` (`backend/algo/backtest/types.py:88-100`) has ticker, qty, prices, pnl. No causal link to which AST node fired or which feature value drove the entry.

`signal_generated` / `order_filled` events written to `algo.events` but **payload does NOT include feature dict snapshot at decision time** — so we can't post-hoc say "this BUY fired because nifty_above_sma200=1 AND sma_50 > sma_200".

Two options to add later:
1. Stamp decision context (feature snapshot, regime label, evaluator path) in event payload.
2. Re-evaluate AST at trade open/close dates and infer reason from cached feature values.

## Recommendations for Regime + Factor spec

1. **Feature dict extension** — add `regime_label` (string enum) and `market_breadth_pct` (float %) to support new predicates without grammar surgery.
2. **Feature store** — Iceberg `stocks.daily_factors` table; nightly job; runtime reads cached values instead of recomputing per-bar.
3. **Regime-stratified walk-forward** — extend `walk_windows()` to label each test window by dominant regime; split aggregate metrics per regime.
4. **Trade attribution** — stamp feature snapshot + regime in `signal_generated` event payload; enables retroactive "why" analysis.
5. **Rolling universe** — add `strategy.universe.rebalance_cadence` (monthly default) + `min_adtv_inr` floor for survivorship-bias-free backtests.
