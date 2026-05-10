# Regime-Aware Multi-Factor Trading System — Research Synthesis (2026-05-10)

> **Source:** general-purpose subagent web research, 2026-05-10. Anchor for the
> `2026-05-10-algo-regime-aware-multifactor-design.md` spec. 5-user scale,
> daily-bar primary data, NSE equity cash markets.

## Executive Recommendations

| Layer | Recommendation | Why |
|---|---|---|
| Regime classifier | **Rule-based primary + 2-state Gaussian HMM overlay** | Explainability dominates; HMM as sanity check, NOT decision-driver |
| Regimes | **3 (BULL / SIDEWAYS / BEAR)** | Matches user brief; 3-state HMM finds phantom states, but rule-based with 3 explicit cuts is fine |
| HMM refit cadence | Monthly | Daily refits flicker; persist `transmat_` warm-start |
| Factor library | momentum (12_1, 6_1, 52w-prox), quality (F-Score we have), low-vol (60d), trend (ADX, SMA200 slope), volume (OBV, vol_x_avg) | Hits the practitioner consensus; aligns with user's brief |
| Position sizing | **Volatility-targeted, 15% portfolio vol, 10 positions, drawdown throttle** | Vol-targeting is the literature default; drawdown throttle handles the convexity of DD clustering |
| Walk-forward CV | 5-year rolling train / 1-year test / 3-month step **+ regime-stratified per-window metrics** | CPCV is overkill for daily-bar 5-user; regime-stratified is non-negotiable |
| Acceptance gates | Max DD ≤ 25%, recovery ≤ 18mo, **per-regime non-negative**, DSR ≥ 0.95, PBO ≤ 0.3 | Sharpe alone is gameable; per-regime guards against single-regime bets |
| Universe | Top 200 by 60d ADTV from NIFTY 500, monthly snapshot, ₹500cr mcap min, ₹10cr ADTV min | Point-in-time = no survivorship bias (research shows 4.94pp/yr inflation otherwise) |
| Backtest start floor | 2007-01-01 (mandatory) | Forces inclusion of 2008 bear; without it, post-COVID-only training fails 2025+ |

## 1. Regime Detection — Recommended Hybrid

### Rule-based primary

```python
def classify_regime(nifty_close, nifty_sma200, vix, ret_30d, ret_60d,
                    pct_above_50sma) -> str:
    above_trend = nifty_close > nifty_sma200
    vix_calm   = vix < 16
    vix_normal = 16 <= vix <= 25
    vix_stress = vix > 25
    bullish_mom = ret_30d > 0.02 and ret_60d > 0.05
    bearish_mom = ret_30d < -0.02 and ret_60d < -0.05
    healthy_breadth = pct_above_50sma > 0.55

    if above_trend and (vix_calm or vix_normal) and bullish_mom \
       and healthy_breadth:
        return "BULL"
    if (not above_trend) and vix_stress and bearish_mom:
        return "BEAR"
    return "SIDEWAYS"
```

### India VIX bands (empirical)

| Band | India VIX | Implied regime |
|---|---|---|
| Calm | <13 | Complacency, often precedes spikes |
| Normal-low | 13–16 | Healthy bull |
| Normal | 16–20 | Trending market |
| Elevated | 20–25 | Caution; reduce gross exposure |
| Stressed | 25–35 | Active correction / pre-bear |
| Crisis | >35 | Mar 2020 spike to 86 |

### HMM overlay (advisory only)

- 2-state Gaussian HMM on `(NIFTY log-return, 20d realized vol)`.
- Monthly refit; persist `transmat_`, `means_`, `covars_` warm-started.
- **Use forward-only filtering** (`model.predict(X[:t+1])`), NEVER `model.predict(X)` over full sample (uses future).
- Output: `stress_prob ∈ [0,1]`. UI: "Rule says BULL, HMM stress 0.62 — divergence, soften size."
- 2 states, NOT 3 — third state is phantom.

### Anti-look-ahead non-negotiables

- Forward returns NEVER as label inputs.
- HMM training cutoff ≤ T for predictions on date T.
- `bfill` forbidden on macro time series.
- Test: shuffle labels → refit → accuracy collapses to class prior. If not, leak.

## 2. NSE-Specific Regime Inputs

### Breadth: % stocks above 50/200 SMA

```python
def pct_above_sma(closes_df: pd.DataFrame, sma_window: int = 200) -> float:
    sma = closes_df.rolling(sma_window).mean()
    above = (closes_df.iloc[-1] > sma.iloc[-1]).sum()
    return above / closes_df.shape[1]
```

Bands: healthy bull >55% above 200SMA, >65% above 50SMA. Sub-30% above 200SMA = recession-grade.

### Sector rotation patterns (for sector-rotation strategy template)

| Phase | Outperformers | Underperformers |
|---|---|---|
| Early bull (recovery) | PSU Bank, Realty, Auto, Infra, Metals | Pharma, FMCG |
| Mid bull (expansion) | IT, Private Bank, Capital Goods, Cement | Utilities |
| Late bull (overheating) | Energy, Metals, Commodities | Quality consumer |
| Early bear / crisis | FMCG, Pharma, IT (USD beneficiary on weak INR) | Banks, Realty, Metals |
| Mid bear | FMCG, Pharma | Cyclicals across the board |

### FII/DII flows: confirming, NOT leading

5-day rolling z-score, `|z| > 1.5` = extreme flow regime. Use as confirming feature, not trigger.

### Midcap/Largecap ratio (high-quality breadth proxy)

```
ratio = NiftyMidcap150 / Nifty50
healthy = ratio > ratio.rolling(50).mean()
```

When ratio rolls over while NIFTY grinds higher → narrow leadership → correction precursor (Jan 2018, Aug 2024 textbook).

## 3. Factor Library

### Momentum (skip-month convention non-negotiable)

```python
mom_12_1 = (close[t-21] / close[t-252]) - 1   # excludes last month
mom_6_1  = (close[t-21] / close[t-126]) - 1
mom_3_1  = (close[t-21] / close[t-63]) - 1
prox_52w = close[t] / max(close[t-252:t])     # ∈ [0,1]
```

Best in trending bull. Long-tail negative skew (Mar 2020 wiped 12m TSMOM).

### Quality

- **Piotroski F-Score** (we have this in `stocks.fscore_summary`) — F=8–9 outperform F=0–2 over 12m.
- **ROIC** — `NOPAT / (debt + equity − cash)`.
- **Accruals (Sloan)** — `(NI − CFO) / total_assets`. High accruals → negative future returns.

Compare WITHIN sector — bank ROIC vs FMCG ROIC is meaningless.

### Low-volatility

```python
realized_vol_60d = std(log_returns[-60:]) * sqrt(252)
```

NSE 18-year backtest (Dec 2006 – Jun 2025): top-100 low-vol gave 12.38% CAGR, ~20% less risk than NIFTY 50.

### Trend strength

```python
sma200_slope = (sma200[t] - sma200[t-21]) / sma200[t-21]
distance_from_sma200 = (close - sma200) / sma200
strong_uptrend = (close > sma50 > sma200) and (adx > 25) \
                 and (sma200_slope > 0)
```

ADX(14) is directionless — pair with directional indicator.

### Volume

```python
obv = (sign(close.diff()) * volume).cumsum()
volume_x_avg_20 = volume[t] / volume[-20:].mean()
up_down_vol_ratio = sum(vol on green days) / sum(vol on red days)  # 20d
```

Filter index/futures expiry days (distort cash market volume).

### Relative strength

```python
rs_vs_nifty_3m = (stock[t] / stock[t-63]) / (nifty[t] / nifty[t-63])
rs_vs_sector_3m = (stock[t] / stock[t-63]) / (sector[t] / sector[t-63])
```

Cross-sectional rank: daily, normalized to [0,1].

## 4. Walk-Forward CV — Regime-Stratified

### Recommended setup

- Rolling 5-year train, 1-year test, 3-month step.
- **Stratify CV folds by regime label** — training set + test set must each contain BULL + SIDEWAYS + BEAR samples in proportion to full sample.
- Report per-regime metrics separately.

### CPCV (deferred — overkill for 5-user)

López de Prado's Combinatorial Purged CV is the gold standard for IID-violating financial data. Implementations: `skfolio.CombinatorialPurgedCV`, `mlfinlab`, `timeseriescv`. Reach for it when training ML primary models or meta-labeling secondaries — not for our deterministic strategies.

### DSR (Deflated Sharpe Ratio) gate

```
DSR = Φ(  (SR_obs − SR_0) × √(T−1)
        ÷ √(1 − γ3·SR_obs + (γ4−1)/4 · SR_obs²)  )
```

`SR_0` = expected max Sharpe from N trials of length T under null.
DSR ≥ 0.95 = strategy is statistically significant after multiple-testing correction.

### PBO (Probability of Backtest Overfitting) gate

CSCV-based: split sample combinatorially; for each split, find best in-sample variant, observe its OOS rank; PBO = fraction of splits where OOS rank lands in bottom half. PBO ≤ 0.2 decent, ≥ 0.5 = selection process is overfit.

## 5. Position Sizing — Volatility-Targeted

```python
def vol_target_qty(target_portfolio_vol_pct, nav, stock_price,
                   stock_realized_vol_annual, n_positions_target):
    per_pos_vol_budget = target_portfolio_vol_pct / sqrt(n_positions_target)
    notional = (per_pos_vol_budget * nav) / stock_realized_vol_annual
    return int(notional / stock_price)
```

For NAV with 15% target vol, 10 positions, stock with 30% annual vol:
- per-position vol budget = 15% / √10 = 4.74%
- notional = 4.74% × NAV / 30% = 15.8% of NAV (capped by per-position cap below)

### Hard caps (trump vol-targeting on conflict)

- Per-position cap: 12% of NAV
- Per-sector cap: 30% of NAV
- Cash floor: 5% always
- Max gross: 100% (no leverage)

### Drawdown throttle (essential — DD clusters)

| DD from peak | Multiplier |
|---|---|
| 0–5% | 1.0× |
| 5–10% | 0.75× |
| 10–15% | 0.5× |
| 15–20% | 0.25× |
| >20% | 0× (halt new entries; existing on stop only) |

Restoration: ratchet back up only after equity recovers above HWM AND vol normalizes.

## 6. Performance Metric Hierarchy

### Tier 1 — acceptance gates (any breach = REJECT)

1. Max drawdown ≤ 25%
2. Time to recovery ≤ 18 months
3. Per-regime return non-negative — strategy must work in BULL, SIDEWAYS, AND BEAR (or be explicitly tagged regime-conditional)

### Tier 2 — ranking metrics

4. Sortino > 1.0 decent, > 1.5 good
5. Calmar > 0.5 investable, > 1.0 institutional
6. CVaR at 5%

### Tier 3 — diagnostic

7. Capital efficiency
8. Turnover (>150%/yr problematic for STT/brokerage drag)
9. Hit rate, avg winner / loser
10. Per-factor IC (Spearman of signal rank vs forward return)

### Sharpe shrinkage

Backtest Sharpe → live Sharpe ≈ ÷ 2 (gut check). DSR is the rigorous version.

## 7. Attribution

### Brinson (sector allocation vs selection)

```
Allocation_i  = (w_p_i − w_b_i) × (R_b_i − R_b_total)
Selection_i   = w_b_i × (R_p_i − R_b_i)
Interaction_i = (w_p_i − w_b_i) × (R_p_i − R_b_i)
```

Daily output: "Today's +1.2% vs NIFTY's +0.4% is +0.5% from being overweight IT (allocation) and +0.3% from picking better banks (selection)."

### Factor regression

```
R_strategy_t = α + β1·MKT + β2·SMB + β3·HML + β4·MOM + β5·QMJ + ε
```

Indian Fama-French + MOM factor returns: Agarwalla/Jacob/Varma (IIM-A) maintain.

### Per-trade attribution log

For every trade, record: entry_signal_id, exit_signal_id, regime_at_entry, factor_exposures_at_entry, holding_period_days, realized_pnl_pct.

Bucketed query: "MOM strategy in BULL had 70% hit rate; in SIDEWAYS 35%."

## 8. Sector Rotation

Standard implementation (monthly rebalance, top-3 by 6m total return, regime-overlay):

```python
def regime_filtered_sectors(rs_ranked, regime):
    BULL_BIAS = {"BANK", "AUTO", "REALTY", "METAL", "PSU_BANK",
                 "INFRA", "CAPITAL_GOODS"}
    DEFENSIVE = {"FMCG", "PHARMA", "IT", "FIN_SERVICES"}
    if regime == "BULL":
        return [s for s, _ in rs_ranked
                if s in BULL_BIAS or s == "IT"][:3]
    if regime == "BEAR":
        return [s for s, _ in rs_ranked if s in DEFENSIVE][:3]
    return [s for s, _ in rs_ranked][:3]
```

NSE sectoral universe: NIFTY Bank, IT, Auto, Pharma, FMCG, Metal, Energy, Realty, PSU Bank, Financial Services.

## 9. Rolling Universe (point-in-time)

NIFTY Smallcap 250 study (SSRN): survivor-only backtests overstate annual return by **4.94pp** (23.3% relative), Sharpe by 0.097 (9.1% relative). 82.5% removal rate over study window.

### Recipe (monthly)

```
for each rebalance_date in monthly_grid:
    universe = stocks_listed_on(rebalance_date)
                .filter(market_cap >= 500cr)
                .filter(adtv_60d >= 10cr)
                .filter(listing_age_days >= 252)
    universe = universe.sort_by(adtv_60d, desc=True).head(200)
    write_iceberg(universe_snapshot, partition=rebalance_date)
```

Min ₹10cr ADTV justification: 5% NAV position should be <0.001% of daily volume to enter/exit cleanly.

## 10. Anti-Patterns (mandatory guardrails)

| Anti-pattern | Detection | Fix |
|---|---|---|
| Train on post-COVID only | Backtest start ≥ 2007? | Hard floor 2007-01-01 |
| Backtest Sharpe 2.0 → live 0.5 | DSR < 0.95 | Reject |
| Regime classifier on forward labels | Refit on contemporaneous → accuracy collapses | Never use forward returns as label |
| Universe survivorship | Backtest CAGR 4–5pp above benchmark | Point-in-time monthly snapshots |
| Last-close fills, no slippage | Backtest vs live spread | `slippage_bps = max(5, 50 × order_value/ADTV)` |
| Factor IC on full sample | OOS IC report | Always IS/OOS pair |
| Hidden look-ahead via fundamentals | Quarter-end vs publication date join | Store both, join on `published_date ≤ rebalance_date` |
| Regime transition lag (2-week bleed) | — | Pre-position into defensives when HMM stress > 0.4 |

## 11. Architectural Summary

### Layered architecture (diagram)

```
┌─────────────────────────────────────────────────────────────┐
│  Daily Pipeline (post-close, ~22:00 IST)                   │
│  ─────────────────────────────────────────                  │
│  1. OHLCV ingest (existing)                                 │
│  2. Universe snapshot (NEW — monthly, but checked daily)    │
│  3. Factor compute → stocks.daily_factors (NEW)             │
│  4. Regime classifier → stocks.regime_history (NEW)         │
│  5. HMM overlay (monthly refit; daily filtered prediction)  │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Strategy Layer (existing AST + regime metadata)            │
│  - applicable_regimes: ["bull", "sideways"]                 │
│  - in-AST regime_eq("bull") predicate (optional)            │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Selector + Sizer (NEW)                                     │
│  - Filter strategies by current regime                      │
│  - Volatility-target position size                          │
│  - Apply hard caps + drawdown throttle                      │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Execution (existing 3 runtimes: backtest / paper / live)   │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Attribution (NEW)                                          │
│  - Brinson allocation/selection vs NIFTY                    │
│  - Per-trade reason log (regime, factors at entry)          │
│  - Monthly factor regression                                │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Validation (extends existing V2-2 walkforward)             │
│  - Regime-stratified CV                                     │
│  - Per-regime metric breakdown                              │
│  - DSR + PBO computation                                    │
│  - Hard gates: max DD, recovery time, per-regime non-neg    │
└─────────────────────────────────────────────────────────────┘
```

## Sources (consolidated index)

### Regime detection
- [Hamilton — Regime-Switching Models](https://econweb.ucsd.edu/~jhamilto/palgrav1.pdf)
- [QuantStart — HMM in QSTrader](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/)
- [QuantInsti — HMM + Random Forest](https://blog.quantinsti.com/regime-adaptive-trading-python/)
- [Two Sigma — ML Regime Modeling](https://www.twosigma.com/articles/a-machine-learning-approach-to-regime-modeling/)
- [MDPI — Regime-Aware LightGBM](https://www.mdpi.com/2079-9292/15/6/1334)

### India VIX
- [NSE — India VIX historical](https://www.nseindia.com/reports-indices-historical-vix)
- [NSE Working Paper 9 — India VIX and Risk Management](https://nsearchives.nseindia.com/research/content/res_WorkingPaper9.pdf)
- [Motilal Oswal — India VIX strategy](https://www.motilaloswal.com/learning-centre/2025/7/nse-india-vix-concept-and-strategy)

### Breadth + sector rotation
- [TradersCockpit live ADR chart](https://www.traderscockpit.com/?pageView=live-nse-advance-decline-ratio-chart)
- [Endovia Wealth — Sector Rotation Strategy](https://www.endoviawealth.com/sector-rotation-strategy-explained/)
- [ChartAlert — RRG for India](https://chartalert.in/2023/09/19/sectoral-rotation-or-relative-rotational-charts-rrc/)

### FII/DII
- [Upstox — FII/DII flow impact](https://upstox.com/learning-center/share-market/impact-of-fii-and-dii-flows-on-nifty-50/article-1602/)

### Factor library — Indian context
- [Sage — Size, Value, Momentum in Indian Equities (Agarwalla et al.)](https://journals.sagepub.com/doi/full/10.1177/0256090917733848)
- [QED Capital — Momentum in India long-side](https://qedcap.com/ast/uploads/2022/03/Momentum-In-India-Sep2021.pdf)
- [Effect of F-Score on Indian Stocks](https://ccsenet.org/journal/index.php/ijef/article/download/64455/35488)
- [BacktestIndia — 18-year low-vol anomaly](https://backtestindia.com/blog/low-volatility-anomaly-india-nse-backtest)

### Walk-forward CV
- [QuantInsti — Walk-Forward Optimization](https://blog.quantinsti.com/walk-forward-optimization-introduction/)
- [QuantInsti — CPCV explained](https://blog.quantinsti.com/cross-validation-embargo-purging-combinatorial/)
- [skfolio — CombinatorialPurgedCV](https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html)

### DSR / PBO
- [davidhbailey — Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- [SSRN — Bailey et al. PBO](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

### Sizing
- [QuantPedia — Volatility Targeting](https://quantpedia.com/an-introduction-to-volatility-targeting/)
- [ResearchAffiliates — Harnessing Vol Targeting](https://www.researchaffiliates.com/content/dam/ra/publications/pdf/1014-harnessing-volatility-targeting.pdf)

### Survivorship bias (India)
- [SSRN — NIFTY Smallcap 250 survivorship 4.94pp](https://papers.ssrn.com/sol3/Delivery.cfm/5833162.pdf?abstractid=5833162&mirid=1)

### Adaptive markets caveat
- [Andrew Lo — Adaptive Market Hypothesis JPM 2004](https://web.mit.edu/Alo/www/Papers/JPM2004_Pub.pdf)
