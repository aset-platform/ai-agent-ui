# FinBERT Batch Sentiment Scoring

## Overview
ProsusAI/finbert replaces LLM cascade (Groq) for batch sentiment scoring.
LLM cascade kept for real-time chat. Zero API cost, ~50 headlines/sec on CPU.

## Module
`backend/tools/_sentiment_finbert.py`

Key functions:
- `_get_pipeline()` — lazy singleton, loads model on first call (~5s cold start)
- `score_headlines_finbert(headlines: list[str]) → list[dict]`
  Returns: `{"label": "positive"|"negative"|"neutral", "score": float, "mapped": float}`
- `compute_weighted_score(scored, weights) → float | None`

## Config
`backend/config.py` → `sentiment_scorer: str = "finbert"`
Options: "finbert" (default), "llm" (legacy)

## Wiring
`backend/tools/_sentiment_scorer.py` → `score_headlines()`:
- Checks `settings.sentiment_scorer` at top of function
- If "finbert": routes to `score_headlines_finbert()` with time-decay weights
- If FinBERT fails: falls through to LLM cascade
- If "llm": uses existing LLM path unchanged

## Pipeline Paths
1. Market-wide (executor Step 1): `score_headlines(market_headlines)` → FinBERT ✓
2. Per-ticker (executor Step 3): `refresh_ticker_sentiment()` → `score_headlines()` → FinBERT ✓
3. Market fallback (executor Step 4-5): uses market_score from Step 1 → FinBERT ✓

## Dependencies
- `transformers>=4.40` in requirements.txt
- `torch` CPU-only via `--index-url https://download.pytorch.org/whl/cpu` in Dockerfile
- Image size increase: ~500MB

## Known Limitation
`refresh_ticker_sentiment()` has idempotent check (lines 211-223) — skips if
already scored today. `force=True` in executor bypasses classification freshness
but NOT the per-ticker freshness check. FinBERT won't re-score tickers that
already have today's LLM scores.
