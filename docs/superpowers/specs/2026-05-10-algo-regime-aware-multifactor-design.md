# Regime-Aware Multi-Factor Trading System — Design Spec

**Date:** 2026-05-10
**Author:** Abhay Kumar Singh
**Status:** Draft (awaiting user approval)
**Module name:** Algo Trading (extension)
**Predecessor specs:**
- `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md` (v1)
- `docs/superpowers/specs/2026-05-09-algo-trading-v2-design.md` (v2 — live trading)
- `docs/superpowers/specs/2026-05-10-algo-v2-observability-postback-design.md` (v2 follow-ups, in progress)

**Research anchors:**
- `docs/superpowers/research/2026-05-10-regime-aware-multifactor-research.md` (NSE-specific, 1500-line synthesis)
- `docs/superpowers/research/2026-05-10-codebase-regime-factor-inventory.md` (gap analysis)

**Working branch:** TBD — after v2 integration → `dev` PR lands. Branch off `dev` as `feature/regime-multifactor-integration`.

**Scale assumption:** 5 users (4 active traders + 1 superadmin). Architecture stays single-instance, no event bus, no microservices. Where the literature offers something more sophisticated but the marginal value is small at this scale, we explicitly choose the simpler path and document it.

---

## 1. Problem & Goals

The v1+v2 platform delivers a working backtest → paper → live pipeline with a JSON-AST strategy grammar. Strategies today are **regime-blind**: a momentum strategy will fire its entries during a 2022-style bear-market grind just as readily as during a 2021 bull, blowing up its drawdown profile and torching user trust.

Two structural gaps make this worse:
1. **No factor library.** Strategies are written by hand against ~7 raw features (`today_ltp`, SMA20/50/200, golden_cross, NIFTY SMA200, NIFTY 30d return). There is no standard set of momentum / quality / low-vol / trend / volume factors to compose strategies from.
2. **No regime context.** The runtime pre-loads `nifty_above_sma200` as a binary signal, but the user's strategy has to wire it in by hand — and there is no stronger regime signal (VIX bands, breadth, sector rotation) to lean on.

This spec adds the missing layer between data ingest and strategy execution: a **regime engine** that classifies each trading day into BULL / SIDEWAYS / BEAR with explainable rule-based logic plus an HMM stress overlay; a **factor library** computed nightly into a feature store; a **selector + sizer** that filters strategies by regime metadata and sizes positions on volatility; an **attribution log** so we can ask "why did this trade make/lose money"; and a **regime-stratified walk-forward CV** with DSR/PBO gates so we don't deceive ourselves about which strategies actually work.

### Goals

- **Regime engine** — daily classifier producing `regime_label ∈ {BULL, SIDEWAYS, BEAR}` and `stress_prob ∈ [0,1]`. Rule-based primary (NIFTY vs SMA200, India VIX bands, 30/60d returns, % above 50SMA breadth). 2-state Gaussian HMM as advisory overlay (NOT decision-driver). Persisted in `stocks.regime_history` Iceberg table; exposed as runtime features.
- **Factor library + feature store** — nightly job computes momentum (mom_12_1, mom_6_1, prox_52w), quality (Piotroski we already have, ROIC), low-vol (60d realized vol), trend strength (ADX(14), SMA200_slope), volume (OBV, volume_x_avg_20), relative strength (vs NIFTY + sector). Cached in `stocks.daily_factors` Iceberg. Runtime reads cached values instead of recomputing per-bar.
- **Strategy↔regime binding** — `applicable_regimes: ["bull", "sideways"]` metadata field on strategy doc + optional in-AST `regime_eq("bull")` predicate (resolves the user-confirmed binding choice from 2026-05-10).
- **Volatility-targeted sizing** — new AST sizing modes `qty: {vol_target_pct: 1.5}`, `qty: {kelly_fraction: 0.25}`. Runtime sizer applies vol-target + per-position cap (12%) + per-sector cap (30%) + drawdown throttle (5/10/15/20% DD → 0.75/0.5/0.25/0× multiplier).
- **Regime-stratified walk-forward CV** — extends V2-2 walkforward with per-window regime labels, per-regime metric breakdown, DSR + PBO computation. New acceptance gates: max DD ≤ 25%, recovery ≤ 18mo, per-regime non-negative return, DSR ≥ 0.95, PBO ≤ 0.3. Live-mode toggle now requires walkforward report passing all five gates (instead of the v2 single "exists and < 30 days old" gate).
- **Trade attribution** — every `signal_generated` event stamped with feature snapshot + regime label + factor exposures at decision time. New `/v1/algo/attribution` endpoint computes daily Brinson allocation/selection vs NIFTY 50 + per-trade reason log. Monthly factor regression job (Indian Fama-French + MOM + quality).
- **Sector rotation strategy template** — first ready-made strategy template using the new factor library: monthly rebalance into top-3 sectors by 6-month return, regime-overlay filter (BULL → cyclicals, BEAR → defensives).
- **Point-in-time universe + slippage upgrade** — `stocks.universe_snapshot` Iceberg table partitioned by `rebalance_date`, monthly job rebuilds top-200-by-ADTV from NIFTY 500. Backtest runner reads point-in-time universe instead of current registry. Slippage model upgraded to `max(5, 50 × order_value/ADTV) bps`. Mandatory backtest start floor `2007-01-01` enforced by parser (must include 2008 bear).

### Regime flip behavior (user-confirmed 2026-05-10)

When regime changes mid-week and an active live run's strategy doesn't match the new regime: **surface as recommendation, manual pause/resume**. New `regime_changed` event, amber banner on Trading tab, email. Zero auto-shutdowns. Auto-pause is a future iteration once the classifier is trusted across multiple regime cycles.

### Strategy↔regime binding (user-confirmed 2026-05-10)

Two-tier expressiveness:
- **Metadata** — `applicable_regimes` array on every strategy. Selector filters available strategies by current regime.
- **In-AST** — `regime_eq("bull")` evaluates against the runtime feature `regime_label` (string). Power users can write fine-grained gating like "trade only on bull-regime days when RSI < 70 AND breadth > 0.55".

Adding `regime_label` (string) requires extending the AST evaluator to handle string `compare` operands — currently all features are int/float Decimal. Small evaluator change, no grammar surgery.

### Non-goals

- **Not building XGBoost/LightGBM regime classifiers.** Rule-based + HMM hybrid. ML classifiers add lookahead-risk surface area not justified at 5-user scale; deferred until we have a reason.
- **Not implementing CPCV (Combinatorial Purged CV)** in this spec. Standard regime-stratified rolling walk-forward is sufficient for our deterministic strategies. CPCV is the gold standard for ML primary models — we don't have any.
- **Not auto-pausing strategies on regime change.** Manual pause/resume per the user-confirmed choice.
- **Not building a portfolio optimizer in v3.** Volatility-targeted per-position sizing + hard caps is the v3 scope. Mean-variance / Black-Litterman / risk parity across strategies is a v4 problem.
- **Not adding F&O.** Equity cash market only; same as v1+v2.
- **Not auto-promoting strategies** based on walkforward score. Walkforward gates *enable* the live-mode toggle; the user still has to flip it.
- **Not changing single-strategy-per-user constraint.** v2 ships single live strategy per user; v3 keeps that constraint. Cross-strategy capital allocation is v4.
- **Not building a real-time tick-level regime classifier.** Daily-bar regime classification only. Intraday pivots (e.g. VIX intraday spike) are out of scope.

---

## 2. Architecture

### 2.1 What changes (and what doesn't)

v1+v2's modular monolith stays intact. v3 adds 4 new packages, extends 1 existing one, adds 4 new Iceberg tables, and adds 1 new tab.

```
backend/algo/
├── (existing v1+v2 modules — no changes to broker/, instruments/,
│   strategy/, paper/, live/, fees, etc.)
│
├── factors/                      # ★ NEW package
│   ├── __init__.py
│   ├── momentum.py               # mom_12_1, mom_6_1, prox_52w
│   ├── quality.py                # ROIC, accruals (uses existing Piotroski)
│   ├── lowvol.py                 # 60d realized vol, beta_to_nifty
│   ├── trend.py                  # ADX(14), SMA200_slope, dist_from_SMA200
│   ├── volume.py                 # OBV, volume_x_avg_20, up_down_vol_ratio
│   ├── relative_strength.py      # rs_vs_nifty, rs_vs_sector
│   ├── compute_job.py            # nightly orchestrator
│   ├── breadth.py                # pct_above_50sma, pct_above_200sma,
│   │                             # midcap_largecap_ratio
│   └── tests/
│
├── regime/                       # ★ NEW package
│   ├── __init__.py
│   ├── rule_based.py             # primary classifier (§3.1)
│   ├── hmm_overlay.py            # 2-state Gaussian HMM (§3.2)
│   ├── classifier_job.py         # daily orchestrator
│   ├── repo.py                   # read/write stocks.regime_history
│   └── tests/
│
├── universe/                     # ★ NEW package (extends backtest/universe.py)
│   ├── __init__.py
│   ├── snapshot_job.py           # monthly rebuilder for stocks.universe_snapshot
│   ├── pit_resolver.py           # point-in-time universe lookup
│   └── tests/
│
├── sizing/                       # ★ NEW package
│   ├── __init__.py
│   ├── vol_target.py             # vol_target_qty(...)
│   ├── caps.py                   # per-position + per-sector
│   ├── drawdown_throttle.py      # multiplier ladder
│   └── tests/
│
├── attribution/                  # ★ NEW package
│   ├── __init__.py
│   ├── brinson.py                # daily allocation/selection
│   ├── trade_log.py              # per-trade reason builder
│   ├── factor_regression.py      # monthly Fama-French + MOM + quality
│   ├── job.py                    # daily + monthly orchestrator
│   └── tests/
│
├── strategy/                     # MODIFIED
│   ├── ast.py                    # ★ add regime_eq node OR string-compare support
│   ├── features.py               # ★ register all factor library + regime_label keys
│   ├── metadata.py               # ★ NEW — applicable_regimes field on strategy doc
│   └── ...
│
├── backtest/
│   ├── walkforward.py            # ★ extend with regime-stratified splits + DSR/PBO
│   ├── runner.py                 # ★ read from stocks.daily_factors + universe_snapshot
│   ├── metrics.py                # ★ NEW — DSR, PBO, per-regime metric breakdown
│   └── ...
│
├── routes/
│   ├── regime.py                 # ★ NEW — GET current + history + classifier health
│   ├── factors.py                # ★ NEW — GET factor scores per ticker
│   ├── attribution.py            # ★ NEW — GET daily Brinson + per-trade log
│   └── walkforward.py            # ★ extend response with per-regime + DSR + PBO
│
├── jobs/                         # MODIFIED
│   └── (regime + factor + attribution + universe jobs registered here
│        via existing @register_job decorator)
│
└── tests/

stocks/                            # MODIFIED
└── create_tables.py              # ★ add 4 new Iceberg tables (§4)

backend/pipeline/                  # MODIFIED
├── runner.py                     # ★ add `^INDIAVIX` to daily OHLCV ingest
└── jobs/
    └── ohlcv.py                  # ★ ensure ^INDIAVIX, sector indices fetched

frontend/
├── components/algo-trading/
│   ├── RegimeWidget.tsx          # ★ NEW — current regime + breadth + VIX gauge
│   ├── RegimeHistoryChart.tsx    # ★ NEW — regime ribbon overlay on NIFTY chart
│   ├── FactorScoresTab.tsx       # ★ NEW — per-ticker factor scores (Insights ext)
│   ├── AttributionPanel.tsx      # ★ NEW — Brinson + per-trade log
│   ├── WalkForwardSubTab.tsx     # ★ extend — per-regime + DSR/PBO + 5 gate UI
│   ├── PaperTab.tsx              # ★ mount RegimeWidget in header
│   └── StrategyEditor.tsx        # ★ add applicable_regimes multi-select
└── hooks/
    ├── useRegime.ts              # ★ NEW — SWR 60s
    ├── useFactorScores.ts        # ★ NEW
    └── useAttribution.ts         # ★ NEW
```

### 2.2 Data flow

```
22:00 IST nightly pipeline:
  OHLCV ingest (existing) ──► stocks.ohlcv
                                     ↓
                              ┌──────┴──────────────────────────┐
                              ↓                                  ↓
                       Universe snapshot                   Factor compute
                       (monthly rebalance)                 (nightly all factors,
                       stocks.universe_snapshot            point-in-time correct)
                                                            stocks.daily_factors
                                     ↓
                              Regime classifier
                              (rule-based + HMM filtered)
                              stocks.regime_history

09:15 IST market open:
  Live runtime starts → reads regime + factors from cache
                     → applies sizer (vol-target + caps + DD throttle)
                     → executes per existing v2 path

15:30 IST market close:
  Attribution job → computes daily Brinson vs NIFTY
                  → builds per-trade reason log
                  → writes to algo.attribution_daily

End-of-month:
  Factor regression → α + factor betas per strategy → algo.factor_regression
  HMM refit → updated transition matrix + emission means → stocks.regime_hmm_state
```

### 2.3 Why this layered architecture (vs alternatives)

| Choice | Alternative | Why this |
|---|---|---|
| Pre-computed nightly factor store | Per-bar runtime compute | Runtime stays fast; backtest 10× speedup; live runtime per-bar O(N) recompute eliminated; cost: 1 nightly job ~10min |
| Rule-based regime + HMM advisory | HMM-only | Explainability dominates at 5-user scale; HMM as sanity check, not decision-driver; per user brief |
| 3 regimes (BULL/SIDEWAYS/BEAR) | 2 (calm/stressed) | Matches user brief; SIDEWAYS catch-all is a feature not a bug — most days *are* SIDEWAYS |
| Metadata + AST predicate (both) | Metadata-only | Power users can write fine-grained gates without lifting them out of the AST; user-confirmed |
| Surface-as-recommendation on regime flip | Auto-pause | Safer first iteration; trust must be earned by classifier before automation; user-confirmed |
| Point-in-time universe (monthly snapshot) | Current-registry universe | Eliminates 4.94pp/yr backtest overstatement (NIFTY Smallcap250 SSRN); mandatory anti-pattern guard |
| Volatility-targeted sizing | Fixed-fractional / Kelly | Vol-target is the literature default; full Kelly produces 50–85% historical drawdowns |
| Drawdown throttle (5/10/15/20% → 0.75/0.5/0.25/0×) | None | DD clusters; conditional probability of more DD given >10% DD is materially higher than unconditional |
| Regime-stratified walk-forward + DSR + PBO | Plain walk-forward | After 7 strategy variants, expected best-of-7 in-sample Sharpe ≥ 1 even when true Sharpe is 0 (Bailey) |
| Pre-computed factor store in Iceberg | PG | Append-only analytics; aligns with CLAUDE.md §5.1 PG vs Iceberg rule |

---

## 3. Modules

### 3.1 Regime classifier — `backend/algo/regime/rule_based.py`

```python
def classify_regime(
    nifty_close: Decimal,
    nifty_sma200: Decimal,
    vix_close: Decimal,
    nifty_ret_30d: Decimal,
    nifty_ret_60d: Decimal,
    pct_above_50sma: Decimal,
) -> str:
    """Returns 'BULL' | 'SIDEWAYS' | 'BEAR' for the given trading day's
    close-of-day inputs. Pure function — no I/O, no NaN handling beyond
    explicit guards."""
    above_trend = nifty_close > nifty_sma200
    vix_calm   = vix_close < Decimal("16")
    vix_normal = Decimal("16") <= vix_close <= Decimal("25")
    vix_stress = vix_close > Decimal("25")
    bullish_mom = nifty_ret_30d > Decimal("0.02") \
                  and nifty_ret_60d > Decimal("0.05")
    bearish_mom = nifty_ret_30d < Decimal("-0.02") \
                  and nifty_ret_60d < Decimal("-0.05")
    healthy_breadth = pct_above_50sma > Decimal("0.55")

    if above_trend and (vix_calm or vix_normal) and bullish_mom \
       and healthy_breadth:
        return "BULL"
    if (not above_trend) and vix_stress and bearish_mom:
        return "BEAR"
    return "SIDEWAYS"
```

Thresholds calibrated from research synthesis §1 + §2.1 (India VIX bands, NSE breadth empirics). All thresholds are config-tunable via `algo_regime_config` PG row (mutable state per CLAUDE.md §5.1).

### 3.2 HMM overlay — `backend/algo/regime/hmm_overlay.py`

```python
class StressHMM:
    """2-state Gaussian HMM on (NIFTY log-return, 20d realized vol).

    State 0 = calm; State 1 = stressed. Stable label assignment via
    ordering by mean realized vol post-fit.

    Refit cadence: monthly (1st Sunday 04:00 IST).
    Persistence: stocks.regime_hmm_state (transmat, means, covars,
                 trained_through DATE).
    Online: filtered prediction only (model.predict(X[:t+1])).
    """
    def fit(self, X: np.ndarray, trained_through: date) -> None: ...
    def stress_prob(self, X_window: np.ndarray) -> float: ...
    def save(self) -> None: ...
    @classmethod
    def load(cls) -> "StressHMM": ...
```

Anti-look-ahead: training cutoff ≤ T for predictions on date T. **Never** call `model.predict(X)` over the full sample (uses Viterbi smoothing, future-dependent). Test `test_hmm_filtered_no_lookahead` verifies last-day prediction matches `predict(X[:t+1])`.

### 3.3 Factor library — `backend/algo/factors/`

One file per factor family. Each exports a single `compute(ticker_history: pd.DataFrame) -> dict[date, dict[str, Decimal]]` function. Composed by `compute_job.py` orchestrator.

| Factor file | Output keys |
|---|---|
| `momentum.py` | `mom_12_1`, `mom_6_1`, `mom_3_1`, `prox_52w` |
| `quality.py` | `roic`, `accruals` (existing `f_score` from `stocks.fscore_summary`) |
| `lowvol.py` | `realized_vol_60d`, `beta_to_nifty` |
| `trend.py` | `adx_14`, `sma200_slope`, `distance_from_sma200` |
| `volume.py` | `obv`, `volume_x_avg_20`, `up_down_vol_ratio_20` |
| `relative_strength.py` | `rs_vs_nifty_3m`, `rs_vs_nifty_6m`, `rs_vs_sector_3m` |
| `breadth.py` | `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio` |

All formulas fixed in research synthesis §3 + §2. Skip-month convention non-negotiable on momentum (`mom_12_1` excludes last 21 trading days). Sector classification reads from existing `stocks.stock_master.sector` column.

### 3.4 Sizing — `backend/algo/sizing/`

```python
# vol_target.py
def vol_target_qty(
    target_portfolio_vol_pct: Decimal,
    nav: Decimal,
    stock_price: Decimal,
    stock_realized_vol_annual: Decimal,
    n_positions_target: int,
) -> int:
    per_pos_vol_budget = target_portfolio_vol_pct / Decimal(n_positions_target).sqrt()
    notional = (per_pos_vol_budget * nav) / stock_realized_vol_annual
    return int(notional / stock_price)

# caps.py
class PositionCaps:
    per_position_max_pct: Decimal = Decimal("12")
    per_sector_max_pct: Decimal = Decimal("30")
    cash_floor_pct: Decimal = Decimal("5")

    def cap(self, intended_qty: int, intended_value: Decimal,
            nav: Decimal, sector: str,
            current_sector_exposure: Decimal) -> int: ...

# drawdown_throttle.py
def dd_multiplier(dd_from_peak_pct: Decimal) -> Decimal:
    if dd_from_peak_pct <= Decimal("5"):  return Decimal("1.0")
    if dd_from_peak_pct <= Decimal("10"): return Decimal("0.75")
    if dd_from_peak_pct <= Decimal("15"): return Decimal("0.5")
    if dd_from_peak_pct <= Decimal("20"): return Decimal("0.25")
    return Decimal("0")  # halt new entries
```

AST sizing modes added to existing `qty: { ... }` discriminated union:
- `qty: { vol_target_pct: 1.5 }` — volatility-targeted (per-position vol budget %)
- `qty: { kelly_fraction: 0.25 }` — quarter-Kelly (requires strategy `expected_edge` metadata)

Existing modes (`shares`, `notional_inr`, `all`) stay supported.

Sizer composition order on every signal:
1. Compute base qty per AST `qty:` clause.
2. Apply `PositionCaps.cap()`.
3. Multiply by `dd_multiplier(current_dd)`.
4. Final qty into pre-trade check (existing v2 path).

### 3.5 Walk-forward CV extension — `backend/algo/backtest/walkforward.py`

Extends existing V2-2 harness with:

```python
class WalkForwardConfig(BaseModel):
    # existing fields
    period_start: date
    period_end: date
    train_days: int
    test_days: int
    step_days: int
    # NEW
    regime_stratified: bool = True
    require_per_regime_non_negative: bool = True
    require_dsr_min: Decimal = Decimal("0.95")
    require_pbo_max: Decimal = Decimal("0.30")
    require_max_dd_pct: Decimal = Decimal("25")
    require_recovery_months_max: int = 18

class PerRegimeMetrics(BaseModel):
    regime: str  # BULL | SIDEWAYS | BEAR
    n_days: int
    cum_return_pct: Decimal
    sharpe: Decimal
    sortino: Decimal
    max_dd_pct: Decimal
    hit_rate: Decimal

class WalkForwardAggregate(BaseModel):
    # existing fields
    avg_pnl_pct: Decimal
    avg_max_dd_pct: Decimal
    # NEW
    per_regime: list[PerRegimeMetrics]
    deflated_sharpe: Decimal       # ∈ [0,1] DSR
    pbo: Decimal                   # ∈ [0,1] PBO
    recovery_months: int
    gates_passed: dict[str, bool]  # 5 gates above
```

DSR + PBO formulas implemented in `backend/algo/backtest/metrics.py`. Bailey/López de Prado closed-form (research synthesis §4).

### 3.6 Attribution — `backend/algo/attribution/`

**Per-trade decision context stamping** (event payload):

Every `signal_generated` event payload extended with:
```json
{
  "feature_snapshot": {
    "today_ltp": 1234.5,
    "sma_50": 1200.0,
    "rsi_14": 62.3,
    "mom_12_1": 0.18,
    ...
  },
  "regime_label": "BULL",
  "stress_prob": 0.12,
  "factor_exposures": {
    "momentum": 0.85,
    "quality": 0.42,
    "lowvol": -0.15
  }
}
```

Persists in existing `algo.events` Iceberg (no schema change — payload is JSONB).

**Brinson daily attribution** — `brinson.py`:

```python
def compute_brinson(
    portfolio_weights: dict[str, Decimal],   # ticker → weight
    benchmark_weights: dict[str, Decimal],   # NIFTY 50 weights
    portfolio_returns: dict[str, Decimal],
    benchmark_returns: dict[str, Decimal],
    sector_lookup: dict[str, str],
) -> dict[str, BrinsonComponents]:
    """Per-sector allocation / selection / interaction effects."""
    ...
```

**Per-trade reason builder** — `trade_log.py`:
- Joins each closed `TradeRow` with the `signal_generated` event that opened it (via `entry_signal_id`).
- Surfaces in UI as: "BUY @ 1234.5 fired because regime=BULL, momentum factor 0.85 (top decile), RSI 62 (clear of overbought). Exited @ 1456 (+18%) because trailing stop triggered."

**Monthly factor regression** — `factor_regression.py`:
- Regresses strategy daily returns on Indian Fama-French + MOM + QMJ factor returns.
- Output: `α` (unexplained alpha), per-factor `β` exposures.

### 3.7 Universe snapshot — `backend/algo/universe/snapshot_job.py`

Monthly job (1st Sunday 03:00 IST):

```python
def rebuild_universe_snapshot(rebalance_date: date) -> None:
    """Rebuilds top-200-by-ADTV universe from NIFTY 500 candidates.

    Filters:
      - listed_on(rebalance_date)
      - market_cap >= 500cr
      - adtv_60d >= 10cr
      - listing_age_days >= 252
    Writes partition to stocks.universe_snapshot.
    """
```

Backtest runner reads `universe_snapshot WHERE rebalance_date = (last <= bar_date)` instead of current-registry universe. Eliminates the 4.94pp/yr survivorship inflation documented in NIFTY Smallcap 250 SSRN study.

### 3.8 Slippage model upgrade — `backend/algo/backtest/sim_broker.py`

Existing fixed-bps slippage replaced with ADTV-scaled model:

```python
def estimate_slippage_bps(
    order_value_inr: Decimal,
    ticker_adtv_inr: Decimal,
) -> Decimal:
    base_bps = Decimal("5")
    impact_bps = Decimal("50") * (order_value_inr / ticker_adtv_inr)
    return max(base_bps, impact_bps)
```

Live runtime keeps using actual fill prices from Kite — slippage model is backtest-only.

### 3.9 Strategy AST changes — `backend/algo/strategy/`

**`metadata.py` (NEW):**
```python
class StrategyMetadata(BaseModel):
    applicable_regimes: list[Literal["bull", "sideways", "bear"]] = [
        "bull", "sideways", "bear"  # default = all (regime-agnostic)
    ]
    expected_edge: Decimal | None = None  # for Kelly sizing
    description: str = ""
```

**`features.py` extension** — register all factor library keys + regime/breadth keys:
- All factor outputs (28 new feature keys per §3.3).
- `regime_label` (string) + `stress_prob` (float).
- `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio`.
- `vix_close`, `vix_sma_20`.

CI sync test (`test_feature_registry_sync.py`) extended to enforce frontend ↔ backend factor key sync.

**`ast.py` evaluator extension:**
- `Literal_` discriminated union extended with `StrLiteral(value: str)`.
- `compare` operator extended to handle string equality (`==`, `!=`) when both operands are strings.
- New helper node `regime_eq(regime: str)` is sugar for `compare(regime_label, ==, regime)`.

### 3.10 Frontend — regime widget + new tabs

- **`RegimeWidget.tsx`** — mounted in Trading tab header. Shows current `regime_label` (color-coded badge), `vix_close` gauge, `pct_above_200sma` bar, `stress_prob` chip with HMM divergence warning if rule-based and HMM disagree.
- **`RegimeHistoryChart.tsx`** — color-ribbon overlay on a NIFTY price chart showing historical regime transitions (BULL = green band, SIDEWAYS = gray, BEAR = red).
- **`FactorScoresTab.tsx`** — extends Insights tab. Per-ticker factor scores table; column selector via existing `useColumnSelection` (CLAUDE.md §5.4 tabular page pattern).
- **`AttributionPanel.tsx`** — Trading tab sub-section. Daily Brinson decomposition; per-trade reason log table.
- **`WalkForwardSubTab.tsx` extension** — adds 5 traffic-light gate indicators (max_dd ≤25%, recovery ≤18mo, per-regime non-negative, DSR ≥0.95, PBO ≤0.3); per-regime metric grid; equity curves color-coded by regime.
- **`StrategyEditor.tsx`** — `applicable_regimes` multi-select chip group at the top of the editor.

---

## 4. Data layer

### 4.1 New Iceberg tables

| Table | Cols | Partition | TTL |
|---|---|---|---|
| `stocks.regime_history` | `bar_date DATE PK, regime_label STRING, stress_prob DOUBLE, rule_inputs JSONB (vix, ret_30d, ret_60d, pct_above_50sma, etc.), classifier_version STRING` | year(bar_date) | unlimited |
| `stocks.regime_hmm_state` | `trained_through DATE PK, transmat JSONB, means JSONB, covars JSONB, n_observations INT` | unpartitioned (~12 rows/yr) | unlimited |
| `stocks.daily_factors` | `ticker STRING, bar_date DATE, mom_12_1 DOUBLE, mom_6_1 DOUBLE, prox_52w DOUBLE, roic DOUBLE, realized_vol_60d DOUBLE, beta_to_nifty DOUBLE, adx_14 DOUBLE, sma200_slope DOUBLE, distance_from_sma200 DOUBLE, obv DOUBLE, volume_x_avg_20 DOUBLE, rs_vs_nifty_3m DOUBLE, rs_vs_sector_3m DOUBLE, sector STRING` (PK ticker+bar_date) | year(bar_date) | 14 months (matches recommendation retention) |
| `stocks.universe_snapshot` | `rebalance_date DATE, ticker STRING, adtv_inr_60d DOUBLE, market_cap_inr DOUBLE, sector STRING, included_in_top_200 BOOL` (PK rebalance_date+ticker) | year(rebalance_date) | unlimited |

NaN-replaceable upsert (CLAUDE.md §5.1): scoped pre-delete NaN rows for incoming keys, then append. Filter dedup query to non-NaN.

### 4.2 PG additions

| Table | Cols | Notes |
|---|---|---|
| `algo.strategy_metadata` | `strategy_id UUID PK, applicable_regimes TEXT[], expected_edge NUMERIC, description TEXT, updated_at TIMESTAMP` | Mutable strategy-author state; per CLAUDE.md PG vs Iceberg |
| `algo.attribution_daily` | `user_id UUID, strategy_id UUID, bar_date DATE, brinson_alloc JSONB, brinson_select JSONB, total_active_return NUMERIC` (PK 4-tuple) | Append-mostly but daily mutation possible on rerun |
| `algo.factor_regression` | `user_id UUID, strategy_id UUID, period_start DATE, period_end DATE, alpha NUMERIC, betas JSONB, r_squared NUMERIC` | Monthly batch |

### 4.3 OHLCV pipeline additions

`backend/pipeline/runner.py` — add `^INDIAVIX` and NIFTY sector indices (`^NSEBANK`, `^CNXIT`, `^CNXAUTO`, `^CNXPHARMA`, `^CNXFMCG`, `^CNXMETAL`, `^CNXENERGY`, `^CNXREALTY`, `^CNXPSUBANK`, `^CNXFINANCE`, `^NIFMDCP150`) to the daily OHLCV ingest. Required for regime classifier (VIX) + sector rotation strategy template + relative strength factor + midcap/largecap breadth.

Existing `^NSEI` ingest covers NIFTY 50 — already there.

### 4.4 Cache invalidation

Per CLAUDE.md §5.13: every Iceberg write through `_retry_commit()` invalidates the appropriate `cache:*` keys. New entries in `_CACHE_INVALIDATION_MAP`:
- `stocks.regime_history` → `cache:regime:*`
- `stocks.daily_factors` → `cache:factors:*`
- `stocks.universe_snapshot` → `cache:universe:*`

### 4.5 Forecast field stamping

`signal_generated` event payload extended with `feature_snapshot`, `regime_label`, `factor_exposures` (no schema change — payload is JSONB). Backward compatible: events without these keys (pre-v3) still parse; UI surfaces "no attribution context" gracefully.

---

## 5. Per-slice decomposition

8 slices, dependency DAG:

```
Slice REGIME-1: VIX/sector ingest + Regime engine (rule + HMM)
   │
   ├──► Slice REGIME-3: Strategy↔regime metadata + selector + AST regime_eq
   │
   └──► Slice REGIME-2a: Factor library backend (compute job + Iceberg + runtime read)
         │
         ├──► Slice REGIME-2b: Factor Scores frontend tab (independent UX)
         │
         └──► Slice REGIME-4: Volatility-targeted sizing + caps + DD throttle
              │
              ├──► Slice REGIME-5: Walk-forward extension + DSR/PBO + 5 gates
              │
              ├──► Slice REGIME-6: Attribution log + Brinson + factor regression
              │
              └──► Slice REGIME-7: Sector rotation template + universe snapshot + slippage upgrade
```

### 5.1 Slice manifest

| # | Slice | Ships | Tab | Est. SP |
|---|---|---|---|---|
| **REGIME-1** | Regime engine | `^INDIAVIX` + sector indices ingest; `regime/rule_based.py`; `regime/hmm_overlay.py`; `regime/classifier_job.py` (daily 22:30 IST); `stocks.regime_history` + `stocks.regime_hmm_state` Iceberg; `RegimeWidget` + `RegimeHistoryChart`; `regime_label` + `stress_prob` features registered | Trading (header widget) | 13 |
| **REGIME-2a** | Factor library — backend infra | `factors/` 7 files (momentum, quality, lowvol, trend, volume, relative_strength, breadth) — compute functions only; `factors/compute_job.py` orchestrator (nightly 23:00 IST); `stocks.daily_factors` Iceberg table + Alembic-style PyIceberg create; `FACTOR_KEYS` registered in `strategy/features.py`; backtest + paper + live runtimes read from `daily_factors` cache instead of per-bar compute; backfill script for 90-day cold start | (no new tab — backend only) | 13 |
| **REGIME-2b** | Factor scores frontend | `FactorScoresTab.tsx` on Insights tab using existing tabular page pattern (CLAUDE.md §5.4) — column selector via `useColumnSelection`, CSV download, sort by any factor; `useFactorScores.ts` SWR hook; `GET /v1/algo/factors/{ticker}` + `GET /v1/algo/factors?tickers=...` endpoints; CI sync test extended | Insights (Factor Scores) | 8 |
| **REGIME-3** | Strategy↔regime binding + selector | `algo.strategy_metadata` PG; `applicable_regimes` field on strategy CRUD; AST evaluator extended with string-compare; `regime_eq` sugar; `StrategyEditor` multi-select chip; `regime_changed` event + amber banner + email | Trading (banner + editor) | 8 |
| **REGIME-4** | Volatility-targeted sizing | `sizing/` 3 files; AST `qty: {vol_target_pct, kelly_fraction}` modes; runtime sizer composition; `dd_multiplier` reads NAV peak from `algo.runs.equity_curve` | (existing tabs) | 8 |
| **REGIME-5** | Walk-forward extension + DSR/PBO + 5 gates | `walkforward.py` regime-stratified split; `metrics.py` DSR + PBO + per-regime breakdown; `WalkForwardSubTab` 5-gate UI; live-mode toggle now requires walkforward report passing all 5 gates | Backtest (subtab) + Live toggle | 13 |
| **REGIME-6** | Attribution | `signal_generated` payload stamping; `attribution/` 4 files; `algo.attribution_daily` PG; `algo.factor_regression` PG; daily Brinson job + monthly regression job; `AttributionPanel` UI | Trading (Attribution panel) | 13 |
| **REGIME-7** | Sector rotation template + PIT universe + slippage | `universe/snapshot_job.py` monthly; `stocks.universe_snapshot` Iceberg; backtest runner reads PIT universe; `sim_broker.py` slippage upgrade; `2007-01-01` backtest start floor parser guard; sector rotation strategy template authored as a reference doc | (no new UI — uses existing) | 13 |

**Total: 89 SP across ~7–8 sessions.**

### 5.2 Critical path

- **REGIME-1 unblocks REGIME-3 (selector needs regime feed) and REGIME-2a (factor store has light dep on regime context for some breadth metrics).**
- **REGIME-2a unblocks REGIME-2b, 4, 5, 6, 7** — five downstream slices read from the factor store.
- **REGIME-2b is purely UX** — can be deferred or parallelized. It does NOT block any other slice.
- **REGIME-5, 6, 7 are mutually independent** once REGIME-2a is in. Can be parallel sessions or interleaved with REGIME-2b.
- **REGIME-7 ships the live-mode-toggle gate change** — before this lands, walk-forward gate is still v2's "exists and < 30 days old". Be careful merging order.

### 5.3 Suggested session ordering

| Session | Slices | Why |
|---|---|---|
| 1 | REGIME-1 (regime engine) | Most important next module (per user brief). Enables every downstream slice. Ends with regime widget visible in UI. |
| 2 | REGIME-2a (factor library backend) | The foundation slice. Backend-only — ends with `daily_factors` Iceberg populated and the 3 runtimes reading from cache instead of per-bar compute. End-to-end value without any UI work. |
| 3 | REGIME-3 (binding + selector) | Small. Glue between regime engine and strategy execution. Independent of REGIME-2a if no factors are referenced in strategies yet. |
| 4 | REGIME-4 (sizing) | Independent, manageable. Vol-target is the literature default; tested in isolation easily. Reads `realized_vol_60d` from REGIME-2a's cache. |
| 5 | REGIME-2b (factor scores UI) — OR — REGIME-5 (walk-forward gates) | Pick based on appetite. REGIME-2b is a clean UX session (8 SP) that surfaces the factor library to users. REGIME-5 (13 SP) is the bigger gate-change session. Either is fine here. |
| 6 | Whichever of REGIME-2b / REGIME-5 wasn't picked in Session 5 | Same logic. |
| 7 | REGIME-6 (attribution) | Reads from everything; ships the explainability UI. |
| 8 | REGIME-7 (sector rotation + PIT universe + slippage) | Final hardening; ships the first regime-aware strategy template. |

---

## 6. Testing

### 6.1 Per slice

| Slice | Tests |
|---|---|
| REGIME-1 | `test_classify_regime_bull/sideways/bear` (rule table coverage); `test_hmm_filtered_no_lookahead` (last-day prediction matches `predict(X[:t+1])`); `test_classifier_writes_iceberg`; `test_vix_ingest_handles_missing_data`; E2E: regime widget shows correct label after seeded regime row |
| REGIME-2a | One unit test per factor function (28 features × 1 happy + 1 NaN-input); `test_factor_compute_job_writes_iceberg`; `test_factor_store_invalidates_cache`; `test_runtime_reads_from_cache_not_recomputes` (backtest + paper + live); `test_factor_backfill_idempotent`; `test_factor_keys_registered_in_features_module` |
| REGIME-2b | `test_factors_endpoint_per_ticker`; `test_factors_endpoint_bulk`; `test_factors_endpoint_unauthorized_403`; component test for `FactorScoresTab` (column selector + sort + CSV download per CLAUDE.md §5.4); `test_feature_registry_sync_extended` (frontend ↔ backend factor keys); E2E: Factor Scores tab populates within 30s of seeded row |
| REGIME-3 | `test_applicable_regimes_default_is_all`; `test_selector_filters_strategies_by_regime`; `test_ast_string_compare`; `test_regime_eq_sugar_compiles_to_compare`; `test_regime_changed_event_emits_once_per_flip`; E2E: regime-bound strategy disabled in editor when current regime mismatches |
| REGIME-4 | `test_vol_target_qty_scales_inversely_with_vol`; `test_per_position_cap_truncates`; `test_per_sector_cap_truncates`; `test_dd_multiplier_ladder` (table-driven boundary tests); `test_sizer_composition_order` (cap before DD throttle); `test_kelly_requires_expected_edge_metadata` |
| REGIME-5 | `test_walkforward_regime_stratified_splits` (BULL/SIDEWAYS/BEAR each present in train + test); `test_dsr_formula` (against Bailey/López de Prado paper sample); `test_pbo_formula` (against Bailey paper sample); `test_5_gate_evaluation` (any 1 fail → reject); `test_live_mode_toggle_requires_5_gates_passed`; E2E: submit walkforward, verify 5 traffic-lights render |
| REGIME-6 | `test_signal_generated_payload_includes_feature_snapshot`; `test_brinson_decomposition_matches_paper_sample`; `test_factor_regression_alpha_extraction`; `test_per_trade_reason_joins_correctly`; E2E: Attribution panel shows Brinson breakdown after seeded daily |
| REGIME-7 | `test_universe_snapshot_excludes_low_adtv`; `test_universe_snapshot_excludes_young_listings`; `test_pit_universe_resolves_to_last_snapshot_lt_bar_date`; `test_slippage_model_min_5bps`; `test_slippage_model_scales_with_order_value`; `test_backtest_start_floor_2007_rejected_at_parse`; integration test: sector-rotation template runs end-to-end on point-in-time universe with regime overlay |

### 6.2 E2E (Playwright)

- Trading tab: regime widget renders correct color + VIX + breadth + stress chip. Hover tooltip explains classification.
- Backtest tab: walkforward submit → 5 traffic-light gate strip renders + per-regime equity curves stacked.
- Strategy editor: applicable_regimes multi-select; saving disables strategy if current regime not in selected.
- Attribution panel: daily Brinson + per-trade log render after seeded events.
- Live mode toggle: disabled with tooltip "Walk-forward gate: 4/5 passed (PBO 0.42 too high)" when one gate fails.

### 6.3 Anti-pattern guard tests

These are CI gates, not feature tests:

- `test_no_forward_returns_in_factor_features` — walks all factor functions, asserts no negative `.shift()` upstream.
- `test_no_bfill_in_macro_pipeline` — greps for `bfill`/`backfill` in regime + factor code.
- `test_universe_includes_delisted_in_pit_lookup` — synthetic delisted ticker present in PIT universe for date < delisting.
- `test_regime_classifier_label_shuffle_collapses_accuracy` — meta-test on the HMM (shuffle labels → accuracy → class prior).

### 6.4 Lighthouse

`/algo-trading` route already meets `/analytics/*` budget. New widgets (`RegimeWidget`, `AttributionPanel`) lazy-loaded; no budget change expected. New page `/algo-trading?tab=factors` (Factor Scores) gets `/analytics/*` budget per CLAUDE.md §5.15.

---

## 7. Rollout

1. After v2 integration → `dev` PR lands, branch `feature/regime-multifactor-integration` from `dev`.
2. Per-slice feature branches off the integration branch: `feature/regime-slice-N-<topic>`.
3. Each slice merges to integration via squash.
4. Integration → `dev` via single PR after REGIME-7 lands.
5. **Backfill ramp** (post dev merge):
   - Day 0: turn on regime classifier daily job; 30 days backfill of regime history.
   - Day 1: turn on factor compute job; 90 days backfill (sufficient for momentum 12_1 lookback).
   - Day 7: turn on universe snapshot monthly job; backfill 24 months of snapshots.
   - Day 14: enable walk-forward 5-gate UI; existing strategies grandfathered (live toggle stays open even if walkforward not re-run).
   - Day 21: enforce 5-gate requirement for any *new* live toggle activation.
   - Day 28: review 1-month factor IC + regime hit rate; tune thresholds if needed.

### 7.1 Live-mode toggle migration

v2 gate today: `walkforward report exists AND < 30 days old`.
v3 gate: `walkforward report exists AND < 30 days old AND all 5 quality gates passed`.

Existing live runs grandfathered (don't auto-disable on the day of the gate change). Next *attempted* live-toggle activation post-day-21 enforces the new gate.

---

## 8. Open Questions

| Topic | Resolution |
|---|---|
| Should regime classifier thresholds be per-user or global? | Global. 5 users — no per-user customization. If a user disagrees with a regime call they can write a regime-eq AST predicate around their strategy. |
| HMM 2-state vs 3-state | 2-state. Research synthesis explicit: 3-state finds phantom calm/transition state. |
| Per-sector regime overlay (e.g. NIFTY IT in BULL while NIFTY Pharma in BEAR) | Out of scope for v3. Single global regime + sector rotation strategy template covers the practical use. Per-sector regime is a v4 question. |
| What happens to strategies with `applicable_regimes=[]` (empty)? | Treated as `applicable_regimes=["bull", "sideways", "bear"]` (regime-agnostic). Default UX. |
| Backtest start floor 2007-01-01 — what about strategies on tickers listed post-2007? | Universe snapshot handles this; ticker only present in snapshot for dates ≥ listing. Strategy backtest can still start 2007 — universe is empty before listing for that ticker. |
| Auto-promotion of strategies based on walk-forward gates passing | NO. User explicitly approves each promotion. Walk-forward gates *enable* the live toggle button; they don't *flip* it. |
| Per-trade attribution overhead on event payload size | Estimated +500 bytes per signal_generated event. Sentinel: if event volume grows >100k/day we drop `feature_snapshot` and recompute from factor store at read time. Out of scope for now (5 users × a few signals/day = trivial). |
| Sector classification for cross-listed tickers (US ADRs of Indian companies) | Out of scope. Indian equity cash market only. |
| Drawdown peak — strategy-level or portfolio-level? | Strategy-level for now (matches single-strategy-per-user constraint). Portfolio-level when v4 multi-strategy ships. |
| Regime classifier warm start on backtest | Backtest reads regime_history (pre-computed) where available; pre-Day-0 backtests recompute on the fly via the same `classify_regime()` pure function. |
| What if HMM and rule-based persistently diverge | Surface a warning chip on the regime widget: "Rule says BULL, HMM stress 0.62 — divergence". User can manually override via a per-user `algo_regime_override` PG row (3-day TTL). Don't auto-update the rule. |

---

## 9. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Regime misclassification kills a profitable strategy mid-week | M | Surface-as-recommendation (manual pause) — user-confirmed default. Auto-pause is v4 once classifier is trusted across multiple cycles. |
| Factor library overfits in-sample on post-COVID data only | H | Mandatory backtest start floor 2007-01-01 (parser-enforced); regime-stratified CV ensures BULL+SIDEWAYS+BEAR all present in train+test |
| DSR + PBO gates too strict, no strategy passes | M | Gates calibrated from research; if no strategy passes after 4 weeks of attempts, lower DSR threshold to 0.90 (still strong). Document in §8 review. |
| HMM refits flicker labels month-to-month | M | Warm-start refit from previous month's `transmat_`; persist + reuse. State 0 always = lower-vol-mean (post-fit ordering). Alert if month-over-month label flip rate > 30%. |
| Vol-target sizer over-allocates to low-vol stocks | M | Per-position cap (12%) + per-sector cap (30%) trumps vol-target on conflict. |
| Drawdown throttle traps user in zero-sizing | M | Restoration: ratchet back up after equity recovers to HWM AND vol normalizes (60d realized vol < 30d trailing). |
| Universe snapshot job fails → backtest falls back to current registry → silent survivorship inflation | H | Backtest runner asserts `universe_snapshot WHERE rebalance_date = ...` returns ≥ 50 rows; raises hard error if not. NO fallback to current registry. |
| Slippage model too aggressive → backtest underestimates real performance | L | 5bps minimum is the documented practitioner default; impact 50bps × order/ADTV is conservative. Calibrate after first live week. |
| `^INDIAVIX` ingest fails (yfinance flake) → regime classifier falls back to bull/bear without VIX bands | M | If VIX missing for >2 days, regime classifier returns SIDEWAYS (safe) + emits `regime_classifier_degraded` event. UI shows amber banner. |
| Per-trade attribution payload bloats events table | L | Estimated +500 bytes/event; 5 users × 5 signals/day = 12,500 bytes/day. Iceberg compression handles 10× this trivially. |
| AST string-compare extension breaks existing parsers | L | All existing strategies use int/float operands; new code path only triggers on `Literal_.value: str`. CI test `test_existing_strategies_parse` covers regression. |
| Frontend regime widget polls 60s → backend load | L | 5 users × 1 widget × 60s = 0.08 req/s. Endpoint reads from Redis (1ms). |
| Sector classification stale (a stock moves sector) | M | `stocks.stock_master.sector` updated on company_info refresh per CLAUDE.md §5.1 per-ticker refresh step. Existing pipeline. |
| HMM lookahead via `model.predict(X)` instead of `predict(X[:t+1])` | H | Test `test_hmm_filtered_no_lookahead` is a hard CI gate. Code review checklist. |

---

## 10. Future work (v4+)

- **Per-sector regime overlay** — independent regime classification per NIFTY sectoral index.
- **Multi-strategy capital allocator** — risk-parity or mean-variance across active strategies.
- **CPCV (Combinatorial Purged CV)** — for ML primary models or meta-labeling secondaries.
- **ML regime classifier** — XGBoost/LightGBM on engineered features once we have 24 months of regime-labeled outcomes to train against.
- **Auto-pause on regime change** — once classifier is trusted (≥ 2 full BULL→BEAR→BULL cycles).
- **Intraday regime pivots** — VIX intraday spike detection (currently daily-bar only).
- **Portfolio-level attribution** — Brinson across strategies (currently per-strategy).
- **Black-Litterman views** — incorporate user discretionary views as Bayesian prior on factor returns.
- **Alternative data factors** — FII/DII flow z-scores, NSE bulk-deals flow, options OI skew.
