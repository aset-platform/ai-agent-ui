# Recency-Aware News & Sentiment — Design Spec

**Date:** 2026-03-30
**Branch:** `feature/sprint4`
**Status:** Draft

---

## Context

The chat agent returns stale news articles and headlines when
answering market sentiment questions. A user asking "what is the
market-wide sentiment for RELIANCE.NS?" received articles about
a budget day selloff and Texas refinery talks — events from
weeks/months ago. No date filtering exists anywhere in the news
pipeline: headline fetchers take the first N items from each
source regardless of publication date, sentiment scoring gives
equal weight to old and new articles, and agent prompts provide
no recency guidance.

**Goal:** Make the agent recency-aware by default (7-day window),
support dynamic window expansion for historical queries, and
weight recent headlines more heavily in sentiment scoring.

---

## Approach: Tool-Level `days_back` Parameter

Add a `days_back: int = 7` parameter to news and sentiment
`@tool` functions. The LLM agent can expand the window when the
user asks for history ("last month" → `days_back=30`). A new
`_date_utils.py` module handles date parsing and time-decay.

### Why This Approach

- **Backward-compatible** — default `days_back=7` preserves
  existing behavior for callers that don't pass it.
- **Dynamic** — LLM agents read the tool schema and can set
  `days_back` based on user intent without graph-state changes.
- **Testable** — `_date_utils.py` is pure functions.
- **No routing changes** — no new fields in `AgentState`, no
  extra LLM calls in the classifier.

---

## Components

### 1. New: `backend/tools/_date_utils.py`

Pure utility module with three functions:

```python
def parse_published(raw: str) -> datetime | None:
    """Parse yfinance Unix ts, RFC 2822, or ISO 8601."""

def is_within_window(
    published: str, days: int,
) -> bool:
    """True if published is within the last `days` days.
    Returns True for unparseable dates (conservative)."""

def time_decay_weight(published: str) -> float:
    """Time-decay multiplier for sentiment scoring.
    0-2 days: 1.0, 3-7 days: 0.5, 8-30 days: 0.25,
    >30 days: 0.1. Unparseable: 0.5 (neutral)."""
```

**Date formats handled:**
- yfinance `providerPublishTime`: Unix int or int-as-string
- RSS `published`: RFC 2822 (`"Thu, 27 Mar 2026 14:30:00 GMT"`)
- ISO 8601: `"2026-03-27T14:30:00Z"` (SerpAPI)

**Conservative policy:** Items with unparseable dates are kept
(not dropped) to avoid silently missing important news.

### 2. Modify: `backend/tools/_sentiment_sources.py`

**`fetch_all_headlines(ticker, max_age_days=7)`**
- Add `max_age_days` parameter (default 7).
- After deduplication, filter items using `is_within_window()`.
- Items with unparseable dates pass through.

**`_deduplicate()` — recency tiebreaker**
- Current sort key: `lambda h: h.weight` (source trust only).
- New sort key: `(weight, parsed_timestamp)` so that among
  items with equal source weight, newer ones survive.

**No changes to individual fetchers** (`_fetch_yfinance`,
`_fetch_yahoo_rss`, `_fetch_google_rss`). They continue to
fetch all available items; filtering happens centrally in
`fetch_all_headlines()` after dedup.

### 3. Modify: `backend/tools/_sentiment_scorer.py`

**`score_headlines()`**
- Current formula: `sum(score * weight) / sum(weight)`
- New formula: `sum(score * weight * decay) / sum(weight * decay)`
  where `decay = time_decay_weight(item.published)`.
- Recent headlines (0-2 days) dominate; older ones contribute
  less but are not excluded.

**`refresh_ticker_sentiment(ticker, llm, max_age_days=7)`**
- Pass `max_age_days` through to `fetch_all_headlines()`.

### 4. Modify: `backend/tools/news_tools.py`

**`get_ticker_news(ticker: str, days_back: int = 7)`**
- After fetching from yfinance + Google RSS, filter articles
  using `is_within_window(article["date"], days_back)`.
- If zero articles remain: return "No recent news found for
  {ticker} in the last {days_back} days."
- Update docstring to explain `days_back`.

**`search_financial_news(query: str, days_back: int = 7)`**
- Same pattern: post-fetch filtering on all sources.
- Redis cache key should include `days_back` to avoid serving
  results cached with a different window.
- Update docstring.

**`get_analyst_recommendations()`** — no changes (already sorts
by date, 24hr cache is appropriate for slow-moving data).

### 5. Modify: `backend/tools/sentiment_agent.py`

**`score_ticker_sentiment(ticker: str, days_back: int = 7)`**
- Pass `days_back` through to `refresh_ticker_sentiment()`.
- Update docstring to explain: "defaults to 7 days; pass
  a larger value for historical sentiment (e.g., 30 for last
  month)."

### 6. Modify: Agent Prompts

**`backend/agents/configs/research.py`** — add to system prompt:
```
- News results are filtered to the last 7 days by default.
  When the user asks about historical events (e.g., "last month",
  "Q4 earnings", "budget day"), pass days_back=30 (or larger).
- Always mention the time window used in your response.
- Prefer the most recent articles when summarizing.
```

**`backend/agents/configs/sentiment.py`** — add to system prompt:
```
- Sentiment is scored from headlines within the last 7 days by
  default. Recent headlines (0-2 days) are weighted more heavily.
- When the user asks about historical sentiment, pass a larger
  days_back value to score_ticker_sentiment.
```

---

## Data Flow (After)

```
User: "What is the sentiment for RELIANCE.NS?"
  → Router → Sentiment Agent
    → get_cached_sentiment("RELIANCE.NS")
      → If stale (>24h):
        → score_ticker_sentiment("RELIANCE.NS", days_back=7)
          → fetch_all_headlines("RELIANCE.NS", max_age_days=7)
            → _fetch_yfinance → _fetch_yahoo_rss → _fetch_google_rss
            → _deduplicate (recency tiebreaker)
            → filter by is_within_window(7 days)
          → score_headlines (with time_decay_weight)
          → persist to Iceberg
    → Return scored sentiment with recency context

User: "What happened with RELIANCE last month?"
  → Router → Research Agent
    → get_ticker_news("RELIANCE.NS", days_back=30)
      → fetch + filter to 30-day window
    → LLM synthesizes with temporal context
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/tools/_date_utils.py` | **NEW** — parse, filter, decay |
| `backend/tools/_sentiment_sources.py` | `max_age_days` param, dedup tiebreaker |
| `backend/tools/_sentiment_scorer.py` | Time-decay weighting |
| `backend/tools/news_tools.py` | `days_back` on 2 tools, post-fetch filter |
| `backend/tools/sentiment_agent.py` | `days_back` passthrough |
| `backend/agents/configs/research.py` | Recency rules in prompt |
| `backend/agents/configs/sentiment.py` | Recency rules in prompt |
| `tests/backend/test_date_utils.py` | **NEW** — unit tests for _date_utils |
| `tests/backend/test_sentiment_sources.py` | Update for max_age_days |
| `tests/backend/test_news_tools.py` | Update for days_back |

---

## Time-Decay Weights

| Headline Age | Decay Factor | Effect |
|-------------|-------------|--------|
| 0-2 days | 1.0 | Full weight — drives sentiment |
| 3-7 days | 0.5 | Half weight — provides context |
| 8-30 days | 0.25 | Background signal only |
| >30 days | 0.1 | Near-zero contribution |
| Unparseable | 0.5 | Neutral — not penalized |

---

## Edge Cases

- **No articles in window:** Return clear message ("No recent
  news in the last N days"). Don't fall back to older articles
  silently.
- **Unparseable dates:** Keep the item with neutral weight (0.5).
  Better to show undated news than miss something important.
- **Weekend gaps:** 7-day window covers full trading weeks.
  Indian markets (RELIANCE.NS) have different holidays but
  7 days handles this.
- **RSS feed lag:** Some RSS feeds serve cached content. The
  filtering catches stale items that the feed returns.
- **Cache key collision:** `search_financial_news` cache key
  must include `days_back` to prevent serving results from a
  different window.

---

## Verification

1. **Unit tests:** New `test_date_utils.py` with cases for
   each date format, edge cases (future dates, epoch 0, None).
2. **Integration:** Run `test_sentiment_sources.py`,
   `test_news_tools.py` — verify existing tests pass with
   default `days_back=7`.
3. **Manual test:** Ask the agent "what is the market sentiment
   for RELIANCE.NS?" — verify articles are from last 7 days.
4. **Historical test:** Ask "what happened with RELIANCE last
   month?" — verify agent passes `days_back=30` and returns
   older articles.
5. **Full suite:** `python -m pytest tests/ -v` — 664+ tests
   passing, 0 failures.
