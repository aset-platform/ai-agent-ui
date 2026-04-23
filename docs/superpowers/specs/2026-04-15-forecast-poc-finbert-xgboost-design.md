# Forecast POC — FinBERT Sentiment + XGBoost Enrichment

**Date**: 2026-04-15
**Sprint**: 7 (POC)
**Jira**: ASETPLTFRM-203 (partial — POC validation)
**Status**: Draft
**Estimate**: 3-4 days

---

## Problem Statement

1. **Sentiment scoring is expensive** — LLM cascade (Groq → Ollama →
   Anthropic) is rate-limited, TPD-constrained, and costs tokens.
   Processing 755+ tickers takes hours and wastes Groq budget.

2. **XGBoost ensemble is undertrained** — already running
   (`ensemble_enabled=True`) on 212+ tickers but only uses 8
   features. Technical indicators (RSI, MACD, Bollinger, ATR)
   are computed by `_analysis_shared.py` but not fed to XGBoost.

3. **No POC validation** — before committing to NeuralProphet or
   full macro integration, need data-driven evidence that
   cheaper/simpler techniques improve forecasts.

---

## Scope

### In Scope

- FinBERT batch sentiment scorer (replace LLM in batch pipeline)
- XGBoost ensemble enrichment (+5 technical indicators)
- POC comparison script (30-ticker before/after test)
- Feature importance pruning for XGBoost
- Docker dependency updates (transformers, torch CPU-only)

### Out of Scope

- FinBERT for real-time chat (keep LLM cascade)
- NeuralProphet migration (skipped — RC package, marginal gains)
- FRED/macro regressors (deferred)
- Frontend changes (POC is backend-only)
- Full India re-run (only after POC validates)

---

## Section 1: FinBERT Batch Sentiment Scorer

### New Module

`backend/tools/_sentiment_finbert.py`:
- Loads `ProsusAI/finbert` via HuggingFace `transformers` pipeline
- Lazy singleton — model loaded on first call, cached in memory
- Batch size 16 for CPU throughput (~50 headlines/sec)

### Interface

```python
def score_headlines_finbert(
    headlines: list[str],
) -> list[dict]:
    """Score financial headlines using FinBERT.

    Returns list of {"label": str, "score": float, "mapped": float}
    where mapped is -1.0 (negative), 0.0 (neutral), +1.0 (positive)
    weighted by confidence.
    """
```

### Mapping

| FinBERT Label | Mapped Score |
|---------------|-------------|
| positive | `+1.0 * confidence` |
| negative | `-1.0 * confidence` |
| neutral | `0.0` |

### Integration

In `backend/jobs/executor.py` → `execute_run_sentiment()`:
- Replace LLM scoring call with FinBERT in batch mode
- Write to same `sentiment_scores` Iceberg table
- Same schema: `ticker, score_date, avg_score, headline_count, source`
- Set `source="finbert"` to distinguish from LLM-scored entries
- Fallback to LLM cascade if FinBERT fails to load

### Toggle

Environment variable `SENTIMENT_SCORER=finbert|llm` (default: `finbert`).

### Dependencies

Add to `requirements.txt`:
```
transformers>=4.40
torch --index-url https://download.pytorch.org/whl/cpu
```

Image size increase: ~500MB (CPU-only torch).

---

## Section 2: XGBoost Ensemble Feature Enrichment

### Current Features (8)

```
prophet_yhat, vix, index_return, sentiment,
treasury_10y, yield_spread, oil_price, dollar_index
```

### Adding 5 Technical Indicators

```
RSI_14, MACD, BB_Upper, BB_Lower, ATR_14
```

All computed by existing `_analysis_shared.compute_indicators()`.
The ensemble code already has a gated path for these (lines 23-45
of `_forecast_ensemble.py`) but they need to be reliably included.

### New Total: 13 features

### Feature Importance Pruning

After POC run, extract `model.feature_importances_` per ticker:
- Mean importance across 30 test tickers
- Features with mean importance < 0.01 get pruned
- Same data-driven approach used for Prophet regressor selection

### Files Modified

- `backend/tools/_forecast_ensemble.py` — ensure technical
  indicators are reliably merged into feature matrix
- No new dependencies

---

## Section 3: POC Test Harness

### Script

`scripts/poc_forecast_comparison.py`

### Test Batch (30 tickers)

**Large-cap stable (10):**
TCS.NS, RELIANCE.NS, INFY.NS, HDFCBANK.NS, ICICIBANK.NS,
ITC.NS, LT.NS, WIPRO.NS, BAJFINANCE.NS, HDFC.NS

**Mid-cap moderate (10):**
TANLA.NS, IRCTC.NS, PAYTM.NS, ZOMATO.NS, DELHIVERY.NS,
NYKAA.NS, POLICYBZR.NS, MAPMYINDIA.NS, HAPPSTMNDS.NS, ROUTE.NS

**Volatile/challenging (10):**
YESBANK.NS, IDEA.NS, PCJEWELLER.NS, RPOWER.NS, SUZLON.NS,
JPPOWER.NS, ADANIGREEN.NS, ADANIENT.NS, PNB.NS, TATAMOTORS.NS

### Flow

1. Record baseline — read existing forecast_runs for 30 tickers
   (MAPE, directional accuracy, confidence scores)
2. Apply FinBERT + XGBoost enrichment changes
3. Re-run forecast on 30 tickers (force=True)
4. Compare per-ticker MAPE delta, aggregate stats
5. Extract XGBoost feature importances

### Output

Console report:
```
=== POC FORECAST COMPARISON ===
Ticker         | Old MAPE | New MAPE | Delta  | Dir Acc
TCS.NS         |    9.2%  |    8.5%  | -0.7%  | 65% → 68%
...
AGGREGATE      |   16.2%  |   14.8%  | -1.4%  | 58% → 61%

=== XGBOOST FEATURE IMPORTANCE ===
Feature        | Mean Importance | Keep/Prune
RSI_14         |          0.082  | KEEP
MACD           |          0.045  | KEEP
...
```

### Run Command

```bash
docker compose exec backend python scripts/poc_forecast_comparison.py
```

---

## Section 4: Rollback & Success Criteria

### Rollback

- FinBERT: `SENTIMENT_SCORER=llm` env var reverts to LLM cascade
- XGBoost: feature list is explicit in `_forecast_ensemble.py`,
  remove indicators to revert

### Success Criteria

1. FinBERT produces valid -1 to +1 scores on CPU, throughput
   > 20 headlines/sec
2. At least 2 of 5 new XGBoost indicators show feature
   importance > 0.01; prune the rest
3. Measurable MAPE improvement on at least 15 of 30 test
   tickers (50%+ of batch)
4. If average MAPE improvement < 1% across batch, revert
   the technique (not worth the complexity)

### Decision Matrix

| Outcome | FinBERT | XGBoost | Action |
|---------|---------|---------|--------|
| Both improve | Keep both | Keep enriched | Scale to full India run |
| FinBERT helps, XGBoost neutral | Keep FinBERT | Prune back to 8 | Partial win |
| XGBoost helps, FinBERT neutral | Revert to LLM | Keep enriched | Partial win |
| Neither helps | Revert | Revert | Close POC, focus elsewhere |
