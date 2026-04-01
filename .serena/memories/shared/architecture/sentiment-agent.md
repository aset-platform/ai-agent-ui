# Sentiment Agent Architecture

The Sentiment Agent is the 5th LangGraph sub-agent in the supervisor graph. It scores news headlines from multiple sources and provides sentiment analysis in chat.

## File Structure
| File | Purpose |
|------|---------|
| `backend/tools/_sentiment_sources.py` | Multi-source headline fetcher with dedup |
| `backend/tools/_sentiment_scorer.py` | FallbackLLM scoring with weighted averages |
| `backend/tools/sentiment_agent.py` | 3 `@tool` functions for LangGraph |
| `backend/agents/configs/sentiment.py` | SubAgentConfig |

## Data Flow
```
yfinance (w=1.0) → Yahoo RSS (w=0.8) → Google RSS (w=0.6)
    ↓
Deduplicate (SequenceMatcher ≥0.8)
    ↓
FallbackLLM batch score (traced via LangSmith)
    ↓
Weighted average: Σ(score × source_weight) / Σ(source_weight)
    ↓
Iceberg: stocks.sentiment_scores (1 row/ticker/day)
```

## Dual-Mode Operation
- **Background batch**: gap_filler at 06:00 UTC via `refresh_all_sentiment()`
- **On-demand**: chat agent via `score_ticker_sentiment` / `get_cached_sentiment` / `get_market_sentiment`

## Chat UX (Hybrid)
- `get_cached_sentiment(ticker)` — returns Iceberg score instantly
- If stale (>24h) — suggests refresh
- `score_ticker_sentiment(ticker)` — live fetch + score + persist
- `get_market_sentiment()` — aggregates portfolio + broad indices (SPY, ^GSPC, ^DJI, ^IXIC)

## Scoring
- Score range: -1.0 (very bearish) to +1.0 (very bullish)
- All LLM calls use FallbackLLM → traced in LangSmith
- Gap filler uses agent_id `sentiment_batch`

## Design Doc
`docs/design/DESIGN-sentiment-agent.md`
