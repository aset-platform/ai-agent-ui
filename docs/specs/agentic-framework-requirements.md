# Requirements Specification: Agentic Framework for Stock Analysis Chat

> Produced by `/sc:brainstorm` — requirements discovery only.
> Next steps: `/sc:design` for architecture, `/sc:workflow` for implementation.

---

## 1. Problem Statement

The current ASET Platform chat server has a flat 2-agent architecture
(General + Stock) with manual routing. Key limitations:

- **No local-data-first strategy**: Tools call yfinance/SerpAPI even
  when Iceberg already has the data (wasted cost + latency)
- **No portfolio reasoning**: No agent understands holdings,
  weightage, sector allocation, or rebalancing
- **No question tracking**: No insight into what users ask, so data
  coverage gaps are invisible
- **SerpAPI cost**: Paid search API used for every news query even
  when yfinance `.news` or cached data would suffice
- **No sub-agent specialization**: Single stock agent does fetch +
  analyze + forecast + search — no parallel execution, no clean
  separation of concerns

---

## 2. User Goals

1. Ask natural-language questions about their portfolio and get
   answers grounded in **locally persisted data** (Iceberg + Redis)
2. Get stock analysis, forecasts, and comparisons without caring
   which data source is used (transparent data routing)
3. Get portfolio optimization suggestions (increase/decrease
   weightage, sector rebalancing, risk reduction)
4. Minimize wait times — answers from local data should be instant
5. Platform operator: minimize external API costs (SerpAPI bills)
6. Platform operator: understand what users ask to proactively
   build data coverage

---

## 3. Functional Requirements

### FR-1: LangGraph Supervisor Architecture

**FR-1.1** Migrate from the current `BaseAgent` + `loop.py` /
`stream.py` agentic loop to a **LangGraph `StateGraph`** with a
supervisor node that orchestrates 4 specialized sub-agents.

**FR-1.2** The supervisor node receives the user message, classifies
intent, and delegates to the appropriate sub-agent(s). Multiple
sub-agents may be invoked in sequence or parallel for complex queries.

**FR-1.3** Sub-agent results are returned to the supervisor, which
synthesizes a final response using the existing FallbackLLM cascade.

**FR-1.4** The graph must support streaming (NDJSON events) compatible
with the existing `/v1/chat/stream` and `/ws/chat` endpoints.

### FR-2: Four Sub-Agents

**FR-2.1 Portfolio Agent**
- Holdings CRUD, P&L calculation, sector/market allocation
- Weightage analysis ("what % of my portfolio is in IT sector?")
- Dividend income tracking and projection
- Rebalancing suggestions ("how should I rebalance for less risk?")
- Cash-flow-adjusted metrics (invested vs market value)
- **Data source**: Iceberg `portfolio_transactions`, `ohlcv`,
  `company_info`, `dividends` tables + Redis cache

**FR-2.2 Stock Analyst Agent**
- OHLCV data retrieval, technical indicator computation
- Fundamental analysis (PE, market cap, 52w range)
- Multi-stock comparison (normalized price, side-by-side metrics)
- **Data source**: Iceberg first → yfinance fallback for missing data

**FR-2.3 Forecaster Agent**
- Prophet-based price forecasting (3m/6m/9m horizons)
- Forecast accuracy metrics (MAE, RMSE, MAPE from backtesting)
- Price targets with confidence intervals
- Portfolio-level aggregated forecast
- **Data source**: Iceberg `forecast_runs`, `forecasts` tables →
  re-run Prophet only if stale (>7 days)

**FR-2.4 Research Agent**
- Market news and sentiment via tiered free-first strategy:
  1. yfinance `.news` (free, per-ticker headlines)
  2. Google News RSS (free, broader market headlines)
  3. SerpAPI (paid, deep enrichment — LAST RESORT only)
- Analyst recommendations (yfinance `.recommendations`)
- Sector/industry trends
- **No general web search** — financial news only
- **Data source**: yfinance → Google News RSS → SerpAPI fallback

### FR-3: Two-Tier Intent Router

**FR-3.1** Tier 1: Keyword/regex router (extends current `router.py`)
classifies 70-80% of queries at near-zero cost. Patterns:
- Portfolio keywords → Portfolio Agent
- Ticker + analysis keywords → Stock Analyst
- Forecast/predict keywords → Forecaster
- News/market/sentiment keywords → Research Agent
- Ambiguous → pass to Tier 2

**FR-3.2** Tier 2: LLM-based intent classification using a cheap/fast
model (e.g., `llama-3.3-70b`) with structured output returning one
of: `portfolio`, `stock_analysis`, `forecast`, `research`, `general`.
Only invoked when Tier 1 confidence is low.

### FR-4: Local-Data-First Strategy

**FR-4.1** Every sub-agent MUST check Iceberg + Redis cache before
calling any external API (yfinance, SerpAPI).

**FR-4.2** Data freshness rules (configurable per data type):
| Data Type | Fresh If | Stale Action |
|-----------|----------|--------------|
| OHLCV | fetched today | yfinance delta fetch |
| Company info | fetched within 1 day | yfinance `.info` |
| Technical indicators | computed today | recompute from Iceberg OHLCV |
| Forecasts | run within 7 days | re-run Prophet |
| Dividends | fetched within 7 days | yfinance `.dividends` |
| Quarterly results | fetched within 7 days | yfinance statements |
| News | cached within 1 hour | yfinance `.news` → SerpAPI |

**FR-4.3** When answering from local data, the response MUST include
a freshness indicator ("Data as of: 2026-03-21") so users know the
recency.

### FR-5: Cost-Optimized Data Source Priority

**FR-5.1** Data source priority chain (strict ordering):
1. **Iceberg** (local, free, fastest)
2. **Redis cache** (local, free, fast)
3. **yfinance** (free, structured, external)
4. **SerpAPI** (paid, unstructured, external) — ONLY for news/
   sentiment that yfinance cannot provide

**FR-5.2** SerpAPI must NEVER be called for structured financial data
(prices, fundamentals, dividends, analyst recs). yfinance covers all
of these for free.

**FR-5.3** Track external API call counts per user per day in the
observability layer. Admin dashboard shows daily SerpAPI usage.

### FR-6: Question Tracking & Data Gap Analysis

**FR-6.1** Log every user query to a new Iceberg table `query_log`
with columns: `timestamp`, `user_id`, `query_text`, `classified_intent`,
`sub_agent_invoked`, `tools_used` (JSON array), `data_sources_used`
(JSON array: iceberg/redis/yfinance/serpapi), `was_local_sufficient`
(bool), `response_time_ms`, `gap_tickers` (tickers that triggered
external fetch).

**FR-6.2** A daily background job (after market close, configurable
schedule) analyzes `query_log` entries where
`was_local_sufficient = false`, extracts `gap_tickers`, and queues
yfinance fetches to populate Iceberg for those tickers.

**FR-6.3** Admin endpoint `GET /v1/admin/query-gaps` returns a
summary: most-queried tickers not in local store, most common
query intents that fall through to external APIs, SerpAPI call
count by day.

### FR-7: Portfolio Intelligence (New Capabilities)

**FR-7.1** Weightage analysis: "What percentage of my portfolio is
in RELIANCE.NS?" → compute from `portfolio_transactions` + latest
OHLCV prices.

**FR-7.2** Sector allocation: "Show my sector breakdown" → join
portfolio holdings with `company_info.sector`, compute market-value
weights per sector.

**FR-7.3** Risk metrics: beta (vs NIFTY50/S&P500), Sharpe ratio,
VaR (95%), max drawdown, correlation matrix — computed from OHLCV
returns in Iceberg.

**FR-7.4** Rebalancing suggestions: "How should I diversify?" →
identify over-concentrated sectors/tickers, suggest reducing
allocation based on risk metrics.

**FR-7.5** Dividend income projection: "How much dividend income
will I earn this year?" → extrapolate from historical dividend
data in Iceberg.

### FR-8: Existing Compatibility

**FR-8.1** The `/v1/chat/stream` and `/ws/chat` endpoints MUST
continue to work with the same request/response format (NDJSON
streaming, tool events, final event).

**FR-8.2** The existing `FallbackLLM` N-tier Groq cascade +
Anthropic fallback MUST be preserved. LangGraph nodes use the same
cascade for LLM calls.

**FR-8.3** The existing Iceberg tables (9 stock tables + 2 LLM +
1 audit) MUST NOT be modified. New tables may be added.

**FR-8.4** The existing Redis cache layer MUST be reused for
sub-agent result caching.

---

## 4. Non-Functional Requirements

**NFR-1 Latency**: Queries answerable from local data (Iceberg +
Redis) MUST respond within 2 seconds (excluding LLM inference time).

**NFR-2 Cost**: SerpAPI calls MUST decrease by 80%+ compared to
current usage. Target: <50 SerpAPI calls/day for a 10-user system.

**NFR-3 Observability**: Every sub-agent invocation logged with
timing, data source, and tool usage. Existing `observability.py`
tier health monitoring preserved.

**NFR-4 Fault tolerance**: If one sub-agent fails (e.g., yfinance
API down), the supervisor gracefully returns a partial response
with the available data + an explanation of what's missing.

**NFR-5 Streaming**: Sub-agent tool events (thinking, tool_start,
tool_done) streamed to the client in real-time, same as today.

**NFR-6 Context management**: LangGraph checkpointing via Redis
for multi-turn conversation memory. Replaces the current
in-memory message history.

---

## 5. User Stories / Acceptance Criteria

### US-1: Portfolio Weightage Query
**As a** user with linked stocks,
**I want to** ask "What's my portfolio allocation by sector?"
**So that** I understand concentration risk.

**Acceptance Criteria:**
- [ ] Response shows sector breakdown with % weights
- [ ] Data comes from Iceberg (no external API call)
- [ ] Response includes "Data as of: {date}" freshness tag

### US-2: Local-Data-First Stock Price
**As a** user,
**I want to** ask "What's AAPL's current price?"
**So that** I get an instant answer from local data.

**Acceptance Criteria:**
- [ ] If OHLCV data fetched today → answer from Iceberg (<2s)
- [ ] If stale → delta-fetch from yfinance, answer, persist
- [ ] SerpAPI is NOT called for price queries

### US-3: News with Cost Control
**As a** user,
**I want to** ask "What's the latest news on RELIANCE?"
**So that** I get market context.

**Acceptance Criteria:**
- [ ] yfinance `.news` checked first (free)
- [ ] SerpAPI called only if yfinance has no results
- [ ] SerpAPI call logged in `query_log` with `data_sources_used`

### US-4: Rebalancing Suggestion
**As a** user,
**I want to** ask "How should I rebalance my portfolio?"
**So that** I reduce risk and improve diversification.

**Acceptance Criteria:**
- [ ] System computes current sector allocation from Iceberg
- [ ] Identifies over-concentrated positions (>30% in one sector)
- [ ] Suggests specific % adjustments with reasoning
- [ ] Uses risk metrics (beta, correlation) from local OHLCV data

### US-5: Question Gap Auto-Fill
**As a** platform operator,
**I want** user queries that triggered external fetches to be
automatically backfilled into Iceberg after market close,
**So that** future identical queries are answered locally.

**Acceptance Criteria:**
- [ ] `query_log` table populated on every chat interaction
- [ ] Daily job at 6 PM IST (configurable) reads gaps
- [ ] yfinance fetch triggered for gap tickers
- [ ] Admin endpoint shows gap analysis summary

### US-6: Multi-Agent Parallel Execution
**As a** user,
**I want to** ask "Analyze AAPL and give me the latest news"
**So that** I get both analysis and news in one response.

**Acceptance Criteria:**
- [ ] Supervisor detects dual intent (analysis + news)
- [ ] Stock Analyst and Research Agent run in parallel
- [ ] Combined response synthesized by supervisor
- [ ] Streaming shows parallel tool events from both agents

---

## 6. Scope Constraints

**SC-1 Finance-only**: The system MUST only answer questions related
to stocks, portfolio, financial markets, risk analysis, forecasting,
and investment strategy. Non-financial queries (general knowledge,
weather, trivia, coding help) receive a polite decline:

> "I'm specialized in stock analysis and portfolio management.
> I can help with market data, stock analysis, forecasts, and
> portfolio questions. What would you like to know about your
> investments?"

**SC-2 No general web search**: The `search_web` (SerpAPI) tool is
removed as a general-purpose tool. The Research Agent uses a tiered
news strategy: yfinance `.news` (free) → Google News RSS (free) →
SerpAPI (paid, last resort for deep enrichment).

**SC-3 Guardrail enforcement**: The Two-Tier Router classifies
non-financial queries BEFORE any LLM call. Off-topic queries are
declined at the router level (zero LLM cost).

---

## 7. Open Questions

1. **LangGraph version**: Should we use `langgraph` v0.x (current
   stable) or wait for v1.0? The API has been evolving rapidly.

2. **Conversation memory**: Should LangGraph checkpointing replace
   the current `useChatSession` frontend session management, or
   coexist? Checkpointing gives server-side memory; current system
   is client-side.

3. **yfinance `.news` quality**: yfinance's news API returns basic
   headlines without full article content. Is headline-level news
   sufficient, or do we need SerpAPI for article summaries?

4. **Rebalancing model**: Should rebalancing suggestions use a
   simple rule-based approach (equal-weight target, sector caps)
   or an optimization model (mean-variance, Black-Litterman)?

5. **Background job framework**: For the daily gap filler — use
   Python `schedule` library, APScheduler, or a cron-based approach?

---

## 7. Architecture Preview (for `/sc:design`)

```
User Query
    │
    ▼
┌─────────────────┐
│  Two-Tier Router │
│  (keyword → LLM) │
└────────┬────────┘
         │ intent classification
         ▼
┌─────────────────────────────────────┐
│         LangGraph Supervisor         │
│  (StateGraph + conditional edges)    │
├──────┬──────┬──────┬────────────────┤
│      │      │      │                │
▼      ▼      ▼      ▼                ▼
Portfolio  Stock   Forecaster  Research  Synthesis
Agent    Analyst    Agent      Agent      Node
│         │         │          │          │
▼         ▼         ▼          ▼          ▼
Iceberg  Iceberg  Iceberg   yfinance   FallbackLLM
Redis    →yfinance →Prophet  →SerpAPI   (N-tier)
         fallback  (if stale) (if needed)
```

---

## 8. Recommended Implementation Phasing

| Phase | Scope | Sprint |
|-------|-------|--------|
| **Phase 1** | LangGraph supervisor + router + 2 agents (Stock Analyst + Research) | Sprint 4 |
| **Phase 2** | Portfolio Agent + Forecaster Agent + local-data-first | Sprint 5 |
| **Phase 3** | Question tracking + gap filler + admin dashboard | Sprint 5 |
| **Phase 4** | Portfolio intelligence (rebalancing, risk, correlation) | Sprint 6 |

---

*Generated: 2026-03-21 | Strategy: Agile | Depth: Deep*
*Next: `/sc:design` for detailed architecture*
