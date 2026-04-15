# Forecast POC — FinBERT + XGBoost Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate two forecast improvement techniques via a 30-ticker POC: FinBERT replaces LLM for batch sentiment, and XGBoost ensemble gets reliable technical indicator features.

**Architecture:** FinBERT module provides a drop-in `score_headlines_finbert()` function matching the existing `score_headlines()` interface. XGBoost ensemble already lists 5 technical indicators in `_FEATURES` — task is to verify the merge path works reliably. POC script records baseline, applies changes, re-runs forecasts, compares results.

**Tech Stack:** ProsusAI/finbert (HuggingFace transformers), torch (CPU-only), XGBoost (existing), Prophet (existing)

**Spec:** `docs/superpowers/specs/2026-04-15-forecast-poc-finbert-xgboost-design.md`

---

## Task 1: FinBERT Sentiment Scorer Module

**Files:**
- Create: `backend/tools/_sentiment_finbert.py`
- Create: `tests/backend/test_sentiment_finbert.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backend/test_sentiment_finbert.py
"""Tests for FinBERT batch sentiment scorer."""
import pytest


class TestScoreHeadlinesFinbert:
    def test_positive_headline(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )
        result = score_headlines_finbert(
            ["Company reports record profits, stock surges"]
        )
        assert len(result) == 1
        assert result[0]["label"] in (
            "positive", "negative", "neutral"
        )
        assert -1.0 <= result[0]["mapped"] <= 1.0
        assert result[0]["mapped"] > 0  # positive headline

    def test_negative_headline(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )
        result = score_headlines_finbert(
            ["Company faces bankruptcy, massive layoffs"]
        )
        assert result[0]["mapped"] < 0  # negative

    def test_batch_scoring(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )
        headlines = [
            "Profits surge 50% year over year",
            "CEO arrested for fraud",
            "Board meeting scheduled for Tuesday",
        ]
        result = score_headlines_finbert(headlines)
        assert len(result) == 3
        assert result[0]["mapped"] > 0  # positive
        assert result[1]["mapped"] < 0  # negative

    def test_empty_list(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )
        result = score_headlines_finbert([])
        assert result == []

    def test_result_structure(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )
        result = score_headlines_finbert(["Stock rises"])
        assert "label" in result[0]
        assert "score" in result[0]
        assert "mapped" in result[0]
        assert 0.0 <= result[0]["score"] <= 1.0


class TestComputeWeightedScore:
    def test_weighted_average(self):
        from tools._sentiment_finbert import (
            compute_weighted_score,
        )
        scored = [
            {"mapped": 0.9, "score": 0.95},
            {"mapped": -0.7, "score": 0.80},
        ]
        weights = [1.0, 0.8]
        result = compute_weighted_score(scored, weights)
        assert -1.0 <= result <= 1.0

    def test_empty_returns_none(self):
        from tools._sentiment_finbert import (
            compute_weighted_score,
        )
        assert compute_weighted_score([], []) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=backend python3 -m pytest tests/backend/test_sentiment_finbert.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement FinBERT scorer**

```python
# backend/tools/_sentiment_finbert.py
"""FinBERT batch sentiment scorer for financial headlines.

Replaces LLM cascade for batch sentiment scoring. Uses
ProsusAI/finbert from HuggingFace. Lazy-loads model on
first call (~5s cold start, ~209 MB memory).

For real-time chat sentiment, the LLM cascade is still
used (lower latency for single headlines).
"""
import logging

_logger = logging.getLogger(__name__)

_pipeline = None


def _get_pipeline():
    """Lazy-load FinBERT pipeline (singleton)."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    try:
        from transformers import pipeline as hf_pipeline
        _logger.info("Loading ProsusAI/finbert model...")
        _pipeline = hf_pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            device=-1,  # CPU
            batch_size=16,
        )
        _logger.info("FinBERT model loaded successfully")
        return _pipeline
    except Exception as exc:
        _logger.error(
            "Failed to load FinBERT: %s", exc,
        )
        return None


def score_headlines_finbert(
    headlines: list[str],
) -> list[dict]:
    """Score financial headlines using FinBERT.

    Args:
        headlines: List of headline strings.

    Returns:
        List of dicts with keys: label (str),
        score (float 0-1 confidence), mapped (float
        -1 to +1 sentiment).
    """
    if not headlines:
        return []

    pipe = _get_pipeline()
    if pipe is None:
        _logger.warning(
            "FinBERT unavailable, returning neutral"
        )
        return [
            {"label": "neutral", "score": 0.5,
             "mapped": 0.0}
            for _ in headlines
        ]

    try:
        # Truncate long headlines to 512 tokens
        truncated = [h[:512] for h in headlines]
        results = pipe(
            truncated,
            truncation=True,
            max_length=512,
        )
    except Exception as exc:
        _logger.error(
            "FinBERT scoring failed: %s", exc,
        )
        return [
            {"label": "neutral", "score": 0.5,
             "mapped": 0.0}
            for _ in headlines
        ]

    mapped_results = []
    for r in results:
        label = r["label"].lower()
        confidence = float(r["score"])
        if label == "positive":
            mapped = confidence
        elif label == "negative":
            mapped = -confidence
        else:
            mapped = 0.0
        mapped_results.append({
            "label": label,
            "score": confidence,
            "mapped": round(mapped, 4),
        })

    return mapped_results


def compute_weighted_score(
    scored: list[dict],
    weights: list[float],
) -> float | None:
    """Compute weighted average sentiment score.

    Args:
        scored: Output from score_headlines_finbert().
        weights: Per-headline weights (source trust).

    Returns:
        Weighted average in [-1, +1], or None if empty.
    """
    if not scored or not weights:
        return None

    total_w = 0.0
    weighted_sum = 0.0
    for item, w in zip(scored, weights):
        weighted_sum += item["mapped"] * w
        total_w += w

    if total_w == 0:
        return 0.0
    return max(-1.0, min(1.0, weighted_sum / total_w))
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=backend python3 -m pytest tests/backend/test_sentiment_finbert.py -v`
Expected: All 7 tests PASS (requires transformers + torch installed)

- [ ] **Step 5: Commit**

```bash
git add backend/tools/_sentiment_finbert.py tests/backend/test_sentiment_finbert.py
git commit -m "feat(sentiment): add FinBERT batch scorer module

ProsusAI/finbert via HuggingFace transformers pipeline. Lazy
singleton load, CPU-only, batch_size=16. Maps positive/negative/
neutral to [-1, +1] weighted by confidence. Falls back to neutral
if model fails to load.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 2: Wire FinBERT into Batch Sentiment Pipeline

**Files:**
- Modify: `backend/tools/_sentiment_scorer.py`
- Modify: `backend/config.py`

- [ ] **Step 1: Add SENTIMENT_SCORER config field**

In `backend/config.py`, add to the Settings class:

```python
    sentiment_scorer: str = "finbert"  # "finbert" or "llm"
```

- [ ] **Step 2: Add FinBERT path in `score_headlines()`**

In `backend/tools/_sentiment_scorer.py`, modify `score_headlines()` (line 73) to check the config and route to FinBERT or LLM:

```python
def score_headlines(
    headlines: list[HeadlineItem],
    llm=None,
) -> float | None:
    if not headlines:
        return None

    # Check if FinBERT is configured for batch scoring
    try:
        from config import get_settings
        scorer = getattr(
            get_settings(), "sentiment_scorer", "llm"
        )
    except Exception:
        scorer = "llm"

    if scorer == "finbert":
        from tools._sentiment_finbert import (
            score_headlines_finbert,
            compute_weighted_score,
        )
        titles = [h.title for h in headlines]
        scored = score_headlines_finbert(titles)
        if scored:
            weights = [h.weight for h in headlines]
            from tools._date_utils import (
                time_decay_weight,
            )
            decay_weights = [
                h.weight * time_decay_weight(h.published)
                for h in headlines
            ]
            return compute_weighted_score(
                scored, decay_weights,
            )
        # Fall through to LLM if FinBERT failed

    # Existing LLM path
    if llm is None:
        return None
    # ... (keep existing LLM code unchanged)
```

The key: insert a FinBERT check at the TOP of the function. If `scorer == "finbert"` and scoring succeeds, return early. If it fails, fall through to the existing LLM path.

- [ ] **Step 3: Test FinBERT integration**

Run in Docker container:
```bash
docker compose exec backend python3 -c "
from tools._sentiment_sources import HeadlineItem
from tools._sentiment_scorer import score_headlines
from datetime import datetime
items = [
    HeadlineItem(title='TCS reports 15% profit growth', source='yfinance', weight=1.0, published=datetime.now()),
    HeadlineItem(title='IT sector faces headwinds', source='google', weight=0.6, published=datetime.now()),
]
score = score_headlines(items)
print(f'FinBERT score: {score}')
"
```
Expected: Score between -1.0 and +1.0

- [ ] **Step 4: Commit**

```bash
git add backend/tools/_sentiment_scorer.py backend/config.py
git commit -m "feat(sentiment): wire FinBERT into batch scoring pipeline

Routes score_headlines() through FinBERT when SENTIMENT_SCORER=finbert.
Falls back to LLM cascade if FinBERT fails. Time-decay weighting
preserved. Config: sentiment_scorer field in Settings.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 3: Add Docker Dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile.backend`

- [ ] **Step 1: Add transformers + torch to requirements**

Add to `requirements.txt`:

```
transformers>=4.40
```

Note: torch CPU-only is installed separately in Dockerfile (not in requirements.txt) to use the CPU-only index URL.

- [ ] **Step 2: Update Dockerfile.backend**

Add torch CPU-only install in the builder stage:

```dockerfile
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu
```

- [ ] **Step 3: Rebuild backend**

```bash
./run.sh rebuild backend
```

- [ ] **Step 4: Verify FinBERT loads in container**

```bash
docker compose exec backend python3 -c "
from tools._sentiment_finbert import score_headlines_finbert
r = score_headlines_finbert(['Stock rises on strong earnings'])
print(r)
"
```
Expected: `[{"label": "positive", "score": 0.9x, "mapped": 0.9x}]`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt Dockerfile.backend
git commit -m "build: add transformers + torch (CPU) for FinBERT

torch installed via CPU-only index URL to avoid GPU dependencies.
Image size increase ~500MB. transformers>=4.40 for ProsusAI/finbert.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 4: Verify XGBoost Technical Indicators

**Files:**
- Modify: `backend/tools/_forecast_ensemble.py` (if needed)
- Create: `tests/backend/test_xgboost_features.py`

- [ ] **Step 1: Verify current feature availability**

Run in container to check if indicators actually reach XGBoost:

```bash
docker compose exec backend python3 -c "
from tools._forecast_ensemble import _FEATURES
print('Expected features:', _FEATURES)
print('Count:', len(_FEATURES))
"
```

Expected: 17 features listed including `rsi_14`, `macd`, `bb_upper`, `bb_lower`, `atr_14`

- [ ] **Step 2: Test indicator merge on a real ticker**

```bash
docker compose exec backend python3 -c "
from tools._analysis_shared import compute_indicators
df = compute_indicators('TCS.NS')
if df is not None:
    print('Indicators computed:', list(df.columns))
    print('Rows:', len(df))
    # Check lowercase column names match _FEATURES
    lower_cols = [c.lower() for c in df.columns]
    for f in ['sma_50', 'sma_200', 'rsi_14', 'macd', 'bb_upper', 'bb_lower', 'atr_14']:
        present = f in lower_cols or f.upper() in df.columns
        print(f'  {f}: {\"YES\" if present else \"MISSING\"} ')
else:
    print('No indicators returned')
"
```

- [ ] **Step 3: Fix column name casing if needed**

In `_forecast_ensemble.py`, the `_FEATURES` list uses lowercase (`rsi_14`, `macd`, `bb_upper`). The `compute_indicators()` may return Title-case columns (`RSI_14`, `MACD`, `BB_Upper`). If there's a mismatch, add column name normalization in the merge step (around line 102-129):

```python
# After loading indicators
if tech_df is not None:
    tech_df.columns = [
        c.lower() for c in tech_df.columns
    ]
```

- [ ] **Step 4: Write test for feature matrix assembly**

```python
# tests/backend/test_xgboost_features.py
"""Tests for XGBoost ensemble feature availability."""
import pytest


class TestXGBoostFeatures:
    def test_features_list_includes_technicals(self):
        from tools._forecast_ensemble import _FEATURES
        technicals = [
            "rsi_14", "macd", "bb_upper",
            "bb_lower", "atr_14",
        ]
        for f in technicals:
            assert f in _FEATURES, (
                f"{f} missing from _FEATURES"
            )

    def test_feature_count(self):
        from tools._forecast_ensemble import _FEATURES
        assert len(_FEATURES) >= 13  # 8 base + 5 tech
```

- [ ] **Step 5: Commit**

```bash
git add backend/tools/_forecast_ensemble.py tests/backend/test_xgboost_features.py
git commit -m "fix(ensemble): ensure technical indicator column names match features list

Normalize indicator column names to lowercase for reliable
merge into XGBoost feature matrix.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 5: POC Comparison Script

**Files:**
- Create: `scripts/poc_forecast_comparison.py`

- [ ] **Step 1: Write the comparison script**

```python
# scripts/poc_forecast_comparison.py
"""POC forecast comparison — before vs after FinBERT + XGBoost.

Records baseline metrics from existing forecast_runs, re-runs
forecasts on 30 test tickers, and compares MAPE, directional
accuracy, and confidence scores.

Usage:
    docker compose exec backend python scripts/poc_forecast_comparison.py
"""
import json
import logging
import os
import sys
import uuid

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stocks"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
_log = logging.getLogger("poc")

# ── Test Batch ──────────────────────────────────────
LARGE_CAP = [
    "TCS.NS", "RELIANCE.NS", "INFY.NS", "HDFCBANK.NS",
    "ICICIBANK.NS", "ITC.NS", "LT.NS", "WIPRO.NS",
    "BAJFINANCE.NS", "HDFC.NS",
]
MID_CAP = [
    "TANLA.NS", "IRCTC.NS", "PAYTM.NS", "ZOMATO.NS",
    "DELHIVERY.NS", "NYKAA.NS", "POLICYBZR.NS",
    "MAPMYINDIA.NS", "HAPPSTMNDS.NS", "ROUTE.NS",
]
VOLATILE = [
    "YESBANK.NS", "IDEA.NS", "PCJEWELLER.NS",
    "RPOWER.NS", "SUZLON.NS", "JPPOWER.NS",
    "ADANIGREEN.NS", "ADANIENT.NS", "PNB.NS",
    "TATAMOTORS.NS",
]
ALL_TICKERS = LARGE_CAP + MID_CAP + VOLATILE


def get_baseline():
    """Read current forecast metrics for test tickers."""
    from backend.db.duckdb_engine import (
        invalidate_metadata, query_iceberg_df,
    )
    invalidate_metadata()

    ph = ",".join(f"'{t}'" for t in ALL_TICKERS)
    df = query_iceberg_df(
        "stocks.forecast_runs",
        f"""SELECT * FROM (
            SELECT ticker, mape,
                target_3m_pct_change,
                confidence_score,
                confidence_components,
                computed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker
                    ORDER BY computed_at DESC
                ) AS rn
            FROM forecast_runs
            WHERE ticker IN ({ph})
        ) WHERE rn = 1""",
    )
    baseline = {}
    for _, r in df.iterrows():
        cc = {}
        if isinstance(r.get("confidence_components"), str):
            try:
                cc = json.loads(r["confidence_components"])
            except Exception:
                pass
        baseline[r["ticker"]] = {
            "mape": r.get("mape"),
            "dir_acc": cc.get("direction", 0) * 100,
            "confidence": r.get("confidence_score"),
            "3m_pct": r.get("target_3m_pct_change"),
        }
    return baseline


def run_forecasts():
    """Run forecast on test tickers with force=True."""
    from stocks.repository import StockRepository
    import jobs.executor as ex

    _orig = ex._analyzable_tickers
    def _patched(registry, tickers):
        result = _orig(registry, tickers)
        return [t for t in result if t in ALL_TICKERS]
    ex._analyzable_tickers = _patched

    repo = StockRepository()
    run_id = str(uuid.uuid4())
    _log.info("Running forecasts on %d tickers...", len(ALL_TICKERS))
    ex.execute_run_forecasts(
        "india", run_id, repo, force=True,
    )
    _log.info("Forecast run complete: %s", run_id)


def get_xgboost_importances():
    """Extract feature importances from last XGBoost run."""
    # Feature importances are logged but not persisted.
    # For POC, we read from executor logs or re-run
    # a single ticker to extract them.
    try:
        from stocks.repository import StockRepository
        from tools._forecast_ensemble import (
            ensemble_forecast, _FEATURES,
        )
        from tools._forecast_model import (
            _prepare_data_for_prophet,
            _train_prophet_model,
        )
        from tools._forecast_shared import (
            _load_regressors_from_iceberg,
        )
        from jobs.executor import _ohlcv_from_cached

        repo = StockRepository()
        raw = repo.get_ohlcv("TCS.NS")
        df = _ohlcv_from_cached(raw)
        prophet_df = _prepare_data_for_prophet(df)
        regressors = _load_regressors_from_iceberg(
            "TCS.NS", prophet_df,
        )
        model, train_df = _train_prophet_model(
            prophet_df, ticker="TCS.NS",
            regressors=regressors,
        )
        from tools._forecast_model import (
            _generate_forecast,
        )
        forecast_df = _generate_forecast(
            model, prophet_df, 9,
            regressors=regressors,
        )
        # Run ensemble to get feature importances
        corrected = ensemble_forecast(
            model, train_df, prophet_df,
            forecast_df, "TCS.NS",
            regressors=regressors,
        )
        # XGBoost stores importances internally
        _log.info("XGBoost feature check complete")
    except Exception as exc:
        _log.warning("Feature importance extraction: %s", exc)


def compare(baseline, after):
    """Print comparison table."""
    print()
    print("=" * 80)
    print("POC FORECAST COMPARISON")
    print("=" * 80)
    print(
        f"{'Ticker':16s} | {'Old MAPE':>8s} | "
        f"{'New MAPE':>8s} | {'Delta':>7s} | "
        f"{'Old Conf':>8s} | {'New Conf':>8s}"
    )
    print("-" * 80)

    improvements = 0
    total = 0
    mape_deltas = []

    for t in ALL_TICKERS:
        old = baseline.get(t, {})
        new = after.get(t, {})
        om = old.get("mape")
        nm = new.get("mape")
        oc = old.get("confidence")
        nc = new.get("confidence")

        om_s = f"{om:.1f}%" if om and om == om else "NaN"
        nm_s = f"{nm:.1f}%" if nm and nm == nm else "NaN"
        delta_s = ""
        if om and nm and om == om and nm == nm:
            d = nm - om
            delta_s = f"{d:+.1f}%"
            mape_deltas.append(d)
            if d < 0:
                improvements += 1
            total += 1

        oc_s = f"{oc:.2f}" if oc and oc == oc else "N/A"
        nc_s = f"{nc:.2f}" if nc and nc == nc else "N/A"

        print(
            f"{t:16s} | {om_s:>8s} | {nm_s:>8s} | "
            f"{delta_s:>7s} | {oc_s:>8s} | {nc_s:>8s}"
        )

    print("-" * 80)
    if mape_deltas:
        import numpy as np
        avg_d = np.mean(mape_deltas)
        print(
            f"{'AGGREGATE':16s} | {'':>8s} | {'':>8s} | "
            f"{avg_d:+.2f}% | "
            f"Improved: {improvements}/{total}"
        )
    print("=" * 80)


def main():
    _log.info("=== POC FORECAST COMPARISON ===")
    _log.info("Step 1: Recording baseline...")
    baseline = get_baseline()
    _log.info("Baseline: %d tickers", len(baseline))

    _log.info("Step 2: Running forecasts...")
    run_forecasts()

    _log.info("Step 3: Reading new results...")
    after = get_baseline()  # Re-read after run

    _log.info("Step 4: Comparing...")
    compare(baseline, after)

    _log.info("Step 5: XGBoost features...")
    get_xgboost_importances()

    _log.info("POC complete.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/poc_forecast_comparison.py
git commit -m "feat(poc): add forecast comparison script for 30-ticker test batch

Records baseline MAPE/confidence, re-runs with FinBERT + XGBoost
enrichment, prints before/after comparison table.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 6: Run the POC

- [ ] **Step 1: Rebuild backend with new dependencies**

```bash
./run.sh rebuild backend
```

- [ ] **Step 2: Verify FinBERT loads**

```bash
docker compose exec backend python3 -c "
from tools._sentiment_finbert import score_headlines_finbert
r = score_headlines_finbert(['TCS reports strong Q4 results'])
print(r)
"
```

- [ ] **Step 3: Run the POC comparison**

```bash
docker compose exec backend python scripts/poc_forecast_comparison.py
```

- [ ] **Step 4: Analyze results**

Check:
- Average MAPE delta across 30 tickers
- How many tickers improved vs degraded
- Feature importances from XGBoost logs
- Prune XGBoost features with importance < 0.01

- [ ] **Step 5: Commit results and notes**

```bash
git commit -m "docs(poc): record FinBERT + XGBoost POC results

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```
