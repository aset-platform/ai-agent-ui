# DESIGN: Sentiment Agent — Architecture & Technical Specification

> **Status**: Implemented — All 4 phases complete, 27 tests passing
> **Date**: 2026-03-27
> **Sprint**: Sprint 3
> **Jira**: ASETPLTFRM-211 (Epic)

---

## 1. Problem Statement

The current sentiment pipeline has three limitations:

1. **Single data source** — only yfinance headlines (max 8 per ticker)
2. **No observability** — bare `ChatGroq` bypasses FallbackLLM,
   invisible to LangSmith/LangFuse tracing
3. **Not agentic** — scoring runs only as a background batch job;
   users cannot ask about sentiment in chat

This design introduces a **Sentiment Agent** — a LangGraph sub-agent
that scores headlines from 3 sources, integrates with the chat
supervisor, and uses FallbackLLM for full observability.

---

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌─────────────────────────────────────────────────────────┐
│               LangGraph Supervisor (graph.py)            │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │portfolio │ │stock_    │ │forecaster│ │ sentiment  │ │
│  │          │ │analyst   │ │          │ │  (NEW)     │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
│       └─────────────┴────────────┴─────────────┘        │
│                         │                                │
│                    synthesis                             │
└─────────────────────────┼────────────────────────────────┘
                          │
                      log_query → END
```

### 2.2 Data Flow — Scoring Pipeline

```
┌──────────────────────────────────────────────────────┐
│              _sentiment_sources.py                     │
│                                                       │
│  ┌──────────┐   ┌───────────┐   ┌──────────────┐    │
│  │ yfinance │   │Yahoo RSS  │   │ Google RSS   │    │
│  │ w=1.0    │   │ w=0.8     │   │ w=0.6        │    │
│  └────┬─────┘   └─────┬─────┘   └──────┬───────┘    │
│       └───────────┬────┘────────────────┘            │
│                   │                                   │
│           ┌───────▼───────┐                          │
│           │  Deduplicate  │  (SequenceMatcher ≥0.8)  │
│           │  + Annotate   │  (title, source, weight) │
│           └───────┬───────┘                          │
└───────────────────┼──────────────────────────────────┘
                    │
┌───────────────────┼──────────────────────────────────┐
│           _sentiment_scorer.py                        │
│                   │                                   │
│           ┌───────▼───────┐                          │
│           │  FallbackLLM  │  ← LangSmith auto-trace  │
│           │  batch prompt  │  ← TokenBudget tracked   │
│           └───────┬───────┘                          │
│                   │                                   │
│           ┌───────▼───────┐                          │
│           │  Weighted avg │  Σ(score×w) / Σ(w)       │
│           │  + Clamp      │  [-1.0, +1.0]            │
│           └───────┬───────┘                          │
└───────────────────┼──────────────────────────────────┘
                    │
            ┌───────▼───────┐
            │    Iceberg    │
            │ sentiment_    │
            │ scores        │
            └───────────────┘
```

### 2.3 Dual-Mode Operation

```
Mode 1: Background Batch (gap_filler.py, 06:00 UTC)
──────────────────────────────────────────────────────
  for ticker in repo.get_all_registry():
      headlines = fetch_all_headlines(ticker)   # 3 sources
      score = score_headlines(headlines, llm)    # FallbackLLM
      repo.insert_sentiment_score(...)           # Iceberg

Mode 2: On-Demand Agent (chat, user-initiated)
──────────────────────────────────────────────────────
  User: "What's the sentiment on AAPL?"
  Supervisor → sentiment agent
  → get_cached_sentiment("AAPL")
    → If fresh (< 24h): return cached score + explanation
    → If stale: offer to refresh
  User: "Yes, refresh it"
  → score_ticker_sentiment("AAPL")
    → fetch + score + persist + return explanation
```

---

## 3. File Structure

| File | Purpose | New/Modified |
|------|---------|--------------|
| `backend/tools/_sentiment_sources.py` | Multi-source headline fetcher with dedup | **New** |
| `backend/tools/_sentiment_scorer.py` | Refactored: FallbackLLM + weighted scoring | Modified |
| `backend/tools/sentiment_agent.py` | LangGraph agent tools (3 `@tool` functions) | **New** |
| `backend/agents/configs/sentiment.py` | `SubAgentConfig` for sentiment agent | **New** |
| `backend/agents/graph.py` | Register sentiment node in supervisor | Modified |
| `backend/jobs/gap_filler.py` | Use shared pipeline instead of bare ChatGroq | Modified |
| `tests/backend/test_sentiment_sources.py` | Tests for headline fetching + dedup | **New** |
| `tests/backend/test_sentiment_scorer.py` | Tests for scoring pipeline | **New** |

---

## 4. Detailed Component Design

### 4.1 `_sentiment_sources.py` — Headline Fetcher

```python
# Public API
def fetch_all_headlines(
    ticker: str,
) -> list[HeadlineItem]:
    """Fetch from 3 sources, deduplicate, return annotated list."""

@dataclass
class HeadlineItem:
    title: str
    source: str          # "yfinance" | "yahoo_rss" | "google_rss"
    weight: float        # 1.0 | 0.8 | 0.6
    published: str       # ISO date or empty

# Internal fetchers (each returns list[HeadlineItem])
def _fetch_yfinance(ticker: str) -> list[HeadlineItem]
def _fetch_yahoo_rss(ticker: str) -> list[HeadlineItem]
def _fetch_google_rss(ticker: str) -> list[HeadlineItem]

# Deduplication
def _deduplicate(
    items: list[HeadlineItem],
    threshold: float = 0.8,
) -> list[HeadlineItem]:
    """Remove duplicates by fuzzy title match.
    Keep the item with the highest source weight."""
```

**Source priority**: yfinance → Yahoo RSS → Google RSS.
Each fetcher is wrapped in try/except — if a source fails, log a
warning and continue with remaining sources.

**Yahoo RSS URL**:
`https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US`

**Google RSS URL**:
`https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en`

**Dedup algorithm**: O(n²) pairwise `SequenceMatcher` on lowercased
titles. At ≤30 headlines per ticker, this is negligible.

### 4.2 `_sentiment_scorer.py` — Refactored Scorer

```python
# Public API (refactored)
def score_headlines(
    headlines: list[HeadlineItem],
    llm: BaseChatModel | None = None,
) -> float | None:
    """Score headlines via LLM, return weighted composite.

    Returns None if no headlines or LLM unavailable.
    """

def refresh_ticker_sentiment(
    ticker: str,
    llm: BaseChatModel | None = None,
) -> float | None:
    """End-to-end: fetch → score → persist to Iceberg.

    Idempotent: skips if already scored today.
    Shared by both gap_filler and sentiment agent.
    """

# Kept for backward compat (used by _forecast_shared.py)
def compute_sentiment_regressor(
    ticker, prophet_df, llm=None,
) -> pd.DataFrame | None:
    """Deprecated path — reads from Iceberg instead."""
```

**Key changes**:

- `score_headlines_llm` → `score_headlines` (takes `HeadlineItem`
  list, computes weighted average)
- LLM parameter accepts any `BaseChatModel` (FallbackLLM or test
  mock) — no more direct `ChatGroq` instantiation
- `refresh_ticker_sentiment` is the single code path used by both
  `gap_filler.refresh_sentiment()` and the agent tools

### 4.3 `sentiment_agent.py` — Agent Tools

Three `@tool`-decorated functions:

```python
@tool
def score_ticker_sentiment(ticker: str) -> str:
    """Score live sentiment for a specific stock ticker.

    Fetches latest headlines from yfinance, Yahoo RSS,
    and Google RSS. Scores via LLM and persists to
    Iceberg. Returns a natural language summary.
    """

@tool
def get_cached_sentiment(ticker: str) -> str:
    """Get the most recent cached sentiment score.

    Returns the latest score from Iceberg with its date.
    If stale (>24h), suggests refreshing.
    """

@tool
def get_market_sentiment() -> str:
    """Get aggregate market sentiment across portfolio
    tickers and broad indices (SPY, ^GSPC, ^DJI, ^IXIC).

    Returns overall mood + top bullish/bearish movers.
    """
```

### 4.4 `agents/configs/sentiment.py` — Sub-Agent Config

```python
SENTIMENT_CONFIG = SubAgentConfig(
    agent_id="sentiment",
    name="Sentiment Agent",
    description=(
        "Market and stock sentiment analysis from "
        "news headlines. Scores sentiment, explains "
        "market mood, identifies bullish/bearish movers."
    ),
    system_prompt=_SENTIMENT_SYSTEM_PROMPT,
    tool_names=[
        "score_ticker_sentiment",
        "get_cached_sentiment",
        "get_market_sentiment",
    ],
)
```

### 4.5 Graph Integration (`graph.py`)

Add to `build_supervisor_graph`:

```python
# In imports
from agents.configs.sentiment import SENTIMENT_CONFIG

# In graph construction (after existing sub-agents)
sentiment_node = _make_sub_agent_node(
    SENTIMENT_CONFIG, tool_registry, llm_factory,
)
g.add_node("sentiment", sentiment_node)
g.add_edge("sentiment", "synthesis")

# In supervisor conditional edges
"sentiment": "sentiment",
```

### 4.6 Gap Filler Refactor

```python
# BEFORE (gap_filler.py)
from langchain_groq import ChatGroq
llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
scores = score_headlines_llm(headlines, llm=llm)

# AFTER
from tools._sentiment_scorer import refresh_ticker_sentiment
refresh_ticker_sentiment(ticker, llm=_get_llm())
```

Where `_get_llm()` returns a `FallbackLLM` instance created once
at module level via the same `llm_factory` pattern used by agents.

---

## 5. Observability

| Signal | Where | How |
|--------|-------|-----|
| LLM calls | LangSmith | Auto-traced via FallbackLLM inner LLMs |
| Agent routing | LangSmith | LangGraph node trace (`sentiment`) |
| Cascade events | ObservabilityCollector | FallbackLLM records tier usage |
| Token budget | TokenBudget | FallbackLLM reserve/release |
| Scoring logs | Python logging | `logger.info("Scored %s: %.3f from %d headlines")` |

---

## 6. Error Handling

| Failure | Behavior |
|---------|----------|
| yfinance down | Skip, try Yahoo RSS + Google RSS |
| Yahoo RSS down | Skip, try Google RSS |
| All sources down | Return `None`, log warning, skip Iceberg write |
| FallbackLLM all tiers exhausted | Return `None`, write `source="none"` with `avg_score=0.0` |
| Iceberg write fails | Let error propagate (Hard Rule #10) |
| Dedup edge case (0 headlines) | Return `None` early |

---

## 7. Constraints & Non-Goals

**Constraints**:

- No new pip dependencies (feedparser + difflib already available)
- 79-char line length, PEP 604 types, logging not print
- One composite score per ticker per day in Iceberg
- Headlines are ephemeral — not persisted

**Non-goals (Phase 4+)**:

- FinBERT local inference
- Per-headline storage
- Intraday scoring
- Social media sources (Reddit, X)
- Article body analysis
