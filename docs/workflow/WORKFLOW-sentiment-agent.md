# WORKFLOW: Sentiment Agent — Implementation Plan

> **Status**: Complete — All phases implemented and validated
> **Date**: 2026-03-27
> **Design**: [DESIGN-sentiment-agent.md](../design/DESIGN-sentiment-agent.md)
> **Sprint**: Sprint 3

---

## Sprint Allocation

| Phase | Focus | SP | Jira |
|-------|-------|----|------|
| Phase 1 | Multi-source headline fetcher | 3 | ASETPLTFRM-212 |
| Phase 2 | Scorer refactor + FallbackLLM | 5 | ASETPLTFRM-213 |
| Phase 3 | LangGraph agent + graph integration | 5 | ASETPLTFRM-214 |
| Phase 4 | Gap filler refactor + tests | 3 | ASETPLTFRM-215 |
| **Total** | | **16** | |

---

## Phase 1: Multi-Source Headline Fetcher (3 SP)

### Step 1.1: Create `_sentiment_sources.py`
> **Est**: 3 SP | **Deps**: None | **Parallel**: No

**File to create:**
- `backend/tools/_sentiment_sources.py`

**Implementation:**
1. Define `HeadlineItem` dataclass: `title`, `source`, `weight`,
   `published`
2. `_fetch_yfinance(ticker)` — extract from `yf.Ticker.news`,
   handle v1.2+ nested `content` structure, weight=1.0
3. `_fetch_yahoo_rss(ticker)` — feedparser on Yahoo Finance RSS
   URL, weight=0.8, timeout=10s
4. `_fetch_google_rss(ticker)` — feedparser on Google News RSS
   URL, weight=0.6, timeout=10s
5. `_deduplicate(items, threshold=0.8)` — pairwise
   `SequenceMatcher` on lowercased titles, keep highest-weight
6. `fetch_all_headlines(ticker)` — orchestrate: call each
   fetcher in order, skip on failure, deduplicate merged list

**Checkpoint:** Unit test — mock all 3 sources, verify dedup
removes known duplicates, verify source weights are correct.

---

## Phase 2: Scorer Refactor + FallbackLLM (5 SP)

### Step 2.1: Refactor `_sentiment_scorer.py`
> **Est**: 3 SP | **Deps**: Phase 1 | **Parallel**: No

**File to modify:**
- `backend/tools/_sentiment_scorer.py`

**Changes:**
1. Import `HeadlineItem` and `fetch_all_headlines` from
   `_sentiment_sources`
2. Replace `score_headlines_llm(headlines, llm)` with
   `score_headlines(headlines: list[HeadlineItem], llm)` —
   compute weighted average: `Σ(score × weight) / Σ(weight)`
3. Add `refresh_ticker_sentiment(ticker, llm)` — end-to-end:
   fetch → score → persist. Idempotent (skip if today exists).
   This becomes the single shared code path.
4. Keep `fetch_news_headlines` and `compute_sentiment_regressor`
   as deprecated wrappers for backward compatibility
5. Remove direct `ChatGroq` import — accept `BaseChatModel`

### Step 2.2: Wire FallbackLLM for batch scoring
> **Est**: 2 SP | **Deps**: 2.1 | **Parallel**: No

**Files to modify:**
- `backend/jobs/gap_filler.py`

**Changes:**
1. Replace bare `ChatGroq` instantiation with a module-level
   `_get_scoring_llm()` that returns a `FallbackLLM` instance
2. Replace `refresh_sentiment(ticker)` body with a call to
   `refresh_ticker_sentiment(ticker, llm=_get_scoring_llm())`
3. `refresh_all_sentiment()` loops over tickers using the same
   shared FallbackLLM instance

**Checkpoint:** Run `refresh_sentiment("AAPL")` manually —
verify LangSmith shows the trace. Verify Iceberg row written.

---

## Phase 3: LangGraph Agent + Graph Integration (5 SP)

### Step 3.1: Create agent config
> **Est**: 1 SP | **Deps**: None | **Parallel**: Yes (with 3.2)

**File to create:**
- `backend/agents/configs/sentiment.py`

**Implementation:**
- `SENTIMENT_CONFIG = SubAgentConfig(...)` with system prompt
  and tool names: `score_ticker_sentiment`,
  `get_cached_sentiment`, `get_market_sentiment`

### Step 3.2: Create agent tools
> **Est**: 3 SP | **Deps**: Phase 2 | **Parallel**: Yes (with 3.1)

**File to create:**
- `backend/tools/sentiment_agent.py`

**Implementation:**
1. `@tool score_ticker_sentiment(ticker)` — calls
   `refresh_ticker_sentiment`, returns NL summary
2. `@tool get_cached_sentiment(ticker)` — reads from Iceberg,
   returns score + date + staleness indicator
3. `@tool get_market_sentiment()` — aggregates portfolio tickers
   + broad indices (SPY, ^GSPC, ^DJI, ^IXIC), returns overall
   mood + top 3 bullish/bearish movers

### Step 3.3: Register in supervisor graph
> **Est**: 1 SP | **Deps**: 3.1 + 3.2 | **Parallel**: No

**Files to modify:**
- `backend/agents/graph.py` — add sentiment node + edges
- `backend/main.py` (or `bootstrap.py`) — register tools in
  `setup_tools`

**Checkpoint:** In chat, ask "What is the sentiment on AAPL?"
→ supervisor routes to sentiment agent → cached score returned.
Ask to refresh → live scoring runs → LangSmith trace visible.

---

## Phase 4: Tests + Cleanup (3 SP)

### Step 4.1: Unit tests
> **Est**: 2 SP | **Deps**: Phase 3 | **Parallel**: No

**Files to create:**
- `tests/backend/test_sentiment_sources.py`
- `tests/backend/test_sentiment_scorer.py`

**Test cases:**
1. `test_fetch_yfinance_headlines` — mock yfinance, verify count
2. `test_fetch_yahoo_rss_headlines` — mock feedparser, verify
3. `test_fetch_google_rss_headlines` — mock feedparser, verify
4. `test_dedup_removes_similar` — 80% match removed
5. `test_dedup_keeps_different` — 60% match kept
6. `test_source_failure_skipped` — one source raises, others ok
7. `test_score_headlines_weighted` — verify weighted avg formula
8. `test_score_headlines_no_headlines` — returns None
9. `test_refresh_idempotent` — second call skips scoring
10. `test_refresh_writes_iceberg` — verify repo called
11. `test_agent_cached_fresh` — returns cached, no refresh
12. `test_agent_cached_stale` — suggests refresh

### Step 4.2: Integration smoke test
> **Est**: 1 SP | **Deps**: 4.1 | **Parallel**: No

- Verify `regressor_quality.py` still works with refactored
  scorer (sentiment column still present in Prophet)
- Verify gap_filler scheduled job runs without errors
- Verify existing 548 tests still pass

**Checkpoint:** `python -m pytest tests/ -v` — all green.

---

## Dependency Graph

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4
  │              │         │  │
  │              │         │  └─ 3.1 (config, parallel)
  │              │         └──── 3.2 (tools)
  │              │                 │
  │              └─ 2.1 → 2.2     └─ 3.3 (graph wiring)
  │
  └─ 1.1 (_sentiment_sources.py)
```

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Yahoo RSS URL unreliable | Treat as best-effort; Google RSS is backup |
| FallbackLLM adds latency to batch | Batch runs at 06:00 UTC — latency is acceptable |
| Supervisor routing misses sentiment queries | Add clear routing keywords in agent description |
| Token budget contention with chat users | 100 calls/day at 06:00 UTC — no overlap with chat hours |
