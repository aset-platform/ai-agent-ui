# NeuralProphet vs Prophet for Stock Price Forecasting

**Date:** 2026-04-16
**Depth:** Deep (3-hop)
**Context:** ASETPLTFRM-203 — evaluating NeuralProphet as Prophet replacement in our regime-adaptive forecast pipeline

---

## Executive Summary

**Verdict: DO NOT MIGRATE to NeuralProphet. Use it as a parallel ensemble member (feature-flagged) alongside Prophet, not a replacement.**

NeuralProphet offers genuine advantages (AR-Net, lagged regressors, 13.5x faster training) but has a **critical blocker**: it does not support logistic growth (saturating forecasts), which our volatile-regime pipeline relies on for 27 tickers (≥60% annualized volatility). The project remains in beta (latest: 0.9.0, June 2024; RC10 pre-release) with no stable 1.0 release. The official docs explicitly warn against production use of the TorchProphet compatibility layer.

**Confidence: High** — multiple independent sources confirm the logistic growth gap and beta status.

---

## 1. Architecture Comparison

| Aspect | Prophet | NeuralProphet |
|--------|---------|---------------|
| **Backend** | Stan (C++ MCMC/MAP) | PyTorch (mini-batch SGD) |
| **Trend** | Piecewise linear or logistic | Piecewise linear only (no logistic) |
| **Seasonality** | Fourier series | Fourier series (identical) |
| **Auto-regression** | None | AR-Net (single-layer NN, configurable lags) |
| **Regressors** | Future regressors only | Future + lagged regressors (separate FFNNs) |
| **Uncertainty** | MAP estimation | Quantile regression (non-comparable intervals) |
| **Holidays** | Country-specific | Country-specific (identical) |
| **Training** | Single-threaded Stan | PyTorch SGD (GPU-capable, batched) |
| **Cross-validation** | `cross_validation()` built-in | `crossvalidation_split_df()` (k-fold) |
| **Model size** | ~1MB JSON | PyTorch state_dict (~5-15MB) |

### Key Architectural Wins for NeuralProphet

1. **AR-Net**: Learns from recent price patterns (last N days). Single-layer NN trained to mimic AR process at scale. This is the main accuracy driver — Prophet has zero local context.
2. **Lagged regressors**: VIX, sentiment, volume can be passed as lagged inputs (past N days) rather than requiring future values. Our current Prophet setup forward-fills these — lagged is more honest.
3. **PyTorch backbone**: Extensible, GPU-capable, modern optimization. No Stan compilation issues.

---

## 2. Accuracy Benchmarks

### Academic Results (stock-specific)

| Study | Finding | Nuance |
|-------|---------|--------|
| arXiv:2601.05202 (Jan 2026) | NP-DNN: 99.21% accuracy | Hybrid model (NP + MLP), not pure NeuralProphet |
| Towards Data Science comparison | NeuralProphet "competitive with Prophet on MAPE" | No significant accuracy gap on standard datasets |
| BCP Business & Management (2025) | Prophet with 5 regressors **outperformed** NeuralProphet and ARIMA | Regressors matter more than model architecture |
| Bytepawn benchmark | NeuralProphet "competitive" but not clearly better | Speed was the clear win, not accuracy |

### Key Insight

**Adding regressors to Prophet > switching to NeuralProphet without regressors.** We already have 11 regressors. The marginal accuracy gain from NeuralProphet's AR-Net on top of our enriched Prophet is likely small (0-2% MAPE improvement based on literature).

The biggest accuracy win from NeuralProphet would come from **lagged regressors** (using past VIX/sentiment/volume patterns, not just current values). This is something Prophet fundamentally cannot do.

---

## 3. Performance (Training Speed)

| Metric | Prophet | NeuralProphet | Our Pipeline |
|--------|---------|---------------|--------------|
| **Per-ticker training** | ~3-8s (Stan MAP) | ~0.3-0.6s (PyTorch SGD) | 802 tickers |
| **Speedup** | baseline | **13.5x faster** | Could cut forecast time from 46min to ~15-20min |
| **GPU acceleration** | Not supported | Supported (optional) | CPU-only in Docker |
| **Parallelism** | `parallel=None` (our constraint) | Native batch training | Compatible with ThreadPoolExecutor |
| **Memory** | ~200MB peak | ~400-600MB (PyTorch overhead) | Docker container has 4GB |

Training speed is the strongest argument for NeuralProphet. Our 46-minute India run could potentially drop to 15-20 minutes.

---

## 4. Critical Blockers for Our Pipeline

### BLOCKER 1: No Logistic Growth (Saturating Forecasts)

Our volatile regime (27 tickers, ≥60% vol) uses `growth='logistic'` with dynamic bounds:
- `cap = ATH × 1.5` (log-transformed)
- `floor = 1yr_low × 0.3` (log-transformed)

This prevents Prophet from extrapolating to infinity/zero on parabolic stocks. **NeuralProphet does not support logistic growth.** It's listed as "planned" but has been planned since 2022 with no delivery date.

Without this, TANLA.NS, BLUESTONE.NS, SKFINDUS.NS etc. would regress to the exact -100% predictions we just spent Sprint 7 fixing.

### BLOCKER 2: Uncertainty Intervals Not Comparable

Prophet uses MAP estimation → our confidence scoring system (`_forecast_accuracy.py`) relies on Prophet's `yhat_lower`/`yhat_upper` for coverage calibration. NeuralProphet uses quantile regression → different uncertainty semantics. Our entire confidence badge system would need rewriting.

### BLOCKER 3: Beta Status / No Stable Release

- Latest stable: **0.9.0** (June 2024)
- Latest RC: **1.0.0rc10** (June 2024)
- No commits since June 2024 (~10 months stale)
- Official warning: "TorchProphet should mainly be used for experiments and we do not encourage the use in production"

### BLOCKER 4: Regularization API Incompatible

Prophet uses `prior_scale` (e.g., `seasonality_prior_scale=0.5`). NeuralProphet uses `_reg` suffix with an **inverse relationship** that "cannot directly be translated." Our `build_prophet_config()` in `_forecast_regime.py` would need complete rework.

---

## 5. Integration Path Analysis

### Option A: Full Replacement (NOT RECOMMENDED)

Replace `from prophet import Prophet` with NeuralProphet everywhere.

- **Breaks**: Logistic growth (27 volatile tickers), confidence scoring, regime config
- **Effort**: 5-8 days (rewrite regime, confidence, ensemble)
- **Risk**: Beta dependency in production, 10-month stale project
- **Verdict**: ❌ Unacceptable — reintroduces the exact problems Sprint 7 solved

### Option B: TorchProphet Compatibility Layer (NOT RECOMMENDED)

Use `from neuralprophet import TorchProphet as Prophet`.

- **Breaks**: Logistic growth (same blocker), regularization params
- **Effort**: 1-2 days
- **Risk**: Official docs say "do not use in production"
- **Verdict**: ❌ Wrapper not recommended even by its authors

### Option C: Parallel Ensemble Member (RECOMMENDED)

Run NeuralProphet alongside Prophet for stable/moderate regimes only. Use AR-Net + lagged regressors as an additional signal in the XGBoost ensemble.

```
                  ┌─ Prophet (regime-adaptive, all regimes)
                  │
Feature Store ────┼─ NeuralProphet (stable+moderate only, AR-Net)
(Iceberg)         │
                  └─ XGBoost (residual correction on ensemble)
```

- **Breaks**: Nothing (additive, feature-flagged)
- **Effort**: 3-4 days
- **Risk**: Low (Prophet remains primary; NP is supplementary)
- **Accuracy gain**: AR-Net captures local patterns Prophet misses; lagged regressors add temporal depth
- **Speed impact**: +2-3 min per run (NP trains in parallel with Prophet)
- **Verdict**: ✅ Best risk/reward ratio

### Option D: Wait for 1.0 Stable + Logistic Growth (DEFERRED)

Monitor NeuralProphet releases. Re-evaluate when:
1. Logistic growth is supported
2. A stable 1.0 is released
3. Active development resumes

- **Verdict**: ⏸️ Sensible fallback if Option C shows no MAPE improvement

---

## 6. What AR-Net Actually Gives Us

The unique value of NeuralProphet is the auto-regression module. Prophet decomposes time series into trend + seasonality + regressors but has **zero memory of recent prices**. AR-Net adds:

- **Mean reversion detection**: If price dropped 10% last week, AR-Net can learn that it typically rebounds 3-5%
- **Momentum capture**: 5-day winning streaks have statistical continuation probability
- **Regime transitions**: Price patterns preceding regime changes (stable→volatile) become learnable

This is fundamentally different from our XGBoost residual correction, which uses point-in-time technical indicators. AR-Net sees the **trajectory**, not just the snapshot.

**Recommended config for our use case:**
```python
NeuralProphet(
    n_lags=30,          # 30 trading days lookback
    n_forecasts=63,     # 3-month horizon (matches our Prophet)
    yearly_seasonality=True,
    weekly_seasonality=True,
    learning_rate=0.01,  # conservative for financial data
    epochs=100,
    batch_size=64,
)
# Add lagged regressors
model.add_lagged_regressor('vix', n_lags=7)
model.add_lagged_regressor('sentiment_7d', n_lags=7)
model.add_lagged_regressor('volume_ratio', n_lags=5)
```

---

## 7. Recommendation

| Action | Priority | Sprint |
|--------|----------|--------|
| **Option C: NeuralProphet as ensemble member** | Medium | Sprint 7 (if time) or Sprint 8 |
| Feature-flag: `config.neuralprophet_enabled` | — | Same |
| Stable + moderate regimes only | — | Same |
| AR-Net n_lags=30, lagged VIX/sentiment/volume | — | Same |
| A/B comparison: 30 tickers, measure MAPE delta | — | Same |
| Skip volatile regime (no logistic growth) | Hard rule | — |
| Monitor NeuralProphet 1.0 release | Ongoing | — |

**Expected outcome**: 1-3% MAPE improvement on stable/moderate tickers from AR-Net local context, with zero regression risk to volatile tickers.

---

## Sources

- [NeuralProphet: Explainable Forecasting at Scale (arXiv:2111.15397)](https://arxiv.org/abs/2111.15397)
- [Stock Market Price Prediction using Neural Prophet with DNN (arXiv:2601.05202)](https://arxiv.org/abs/2601.05202)
- [Prophet vs. NeuralProphet — Towards Data Science](https://towardsdatascience.com/prophet-vs-neuralprophet-fc717ab7a9d8/)
- [NeuralProphet Applied to Stock Price Prediction — Medium](https://ngyibin.medium.com/neuralprophet-applied-to-stock-price-prediction-c02c4c8b31fb)
- [From Prophet to TorchProphet Migration Guide](https://neuralprophet.com/how-to-guides/feature-guides/prophet_to_torch_prophet.html)
- [NeuralProphet GitHub Releases](https://github.com/ourownstory/neural_prophet/releases)
- [NeuralProphet PyPI](https://pypi.org/project/neuralprophet/)
- [NeuralProphet Hyperparameter Selection Guide](https://neuralprophet.com/how-to-guides/feature-guides/hyperparameter-selection.html)
- [NeuralProphet Tutorial 4: Auto Regression](https://neuralprophet.com/tutorials/tutorial04.html)
- [NeuralProphet Tutorial 5: Lagged Regressors](https://neuralprophet.com/tutorials/tutorial05.html)
- [Comparing NeuralProphet and Prophet — Bytepawn](https://bytepawn.com/comparing-neuralprophet-and-prophet-for-timeseries-forecasting.html)
- [Forecasting Stock Prices Using Multi-Macroeconomic Regressors (BCP)](https://bcpublication.org/index.php/BM/article/view/1762)
