# Implementation Workflow: Sprint 4 — LangGraph Foundation

> Phase 1 of the Agentic Framework migration.
> Sprint dates: **Mar 26 – Apr 1, 2026**
> Prereq: `docs/specs/agentic-framework-design.md`

---

## Sprint 4 Goal

Build the LangGraph StateGraph supervisor foundation with 2 sub-agents
(Stock Analyst + Research), wire into existing endpoints, and keep old
agents as feature-flag fallback. Finance-only guardrail enforced.

**Velocity target**: 34 story points (based on Sprint 2: 59 pts, Sprint 3: 24 pts delivered)

---

## Story Dependency Graph

```
S4-1 (State Schema)
  │
  ├──▶ S4-2 (Guardrail Node)
  │       │
  │       ├──▶ S4-3 (Router Node)
  │       │       │
  │       │       ├──▶ S4-4 (LLM Classifier)
  │       │       │
  │       │       └──▶ S4-5 (Supervisor Node)
  │       │               │
  │       │               ├──▶ S4-6 (Sub-Agent Factory)
  │       │               │       │
  │       │               │       ├──▶ S4-7 (Stock Analyst)
  │       │               │       │
  │       │               │       └──▶ S4-8 (Research Agent)
  │       │               │
  │       │               └──▶ S4-9 (Synthesis Node)
  │       │
  │       └──▶ S4-10 (Decline Node)
  │
  └──▶ S4-11 (Graph Assembly)
          │
          └──▶ S4-12 (Route Integration)
                  │
                  └──▶ S4-13 (Tests)
```

---

## Stories

---

### S4-1: AgentState schema + graph_state.py
**Points**: 2 | **Priority**: P0 (blocks everything)

**Description**: Create the LangGraph TypedDict state schema
with reducer annotations for message accumulation and tool
event merging.

**Files**:
- `backend/agents/graph_state.py` — NEW

**Acceptance Criteria**:
- [ ] `AgentState` TypedDict with all fields from design spec
- [ ] `messages` field uses `Annotated[..., add_messages]` reducer
- [ ] `tool_events` field uses list-concat reducer
- [ ] `data_sources_used` field uses dedup-merge reducer
- [ ] Imports from `langgraph.graph.message` work (package installed)
- [ ] `flake8` + `black` pass

**Dependencies**: None

---

### S4-2: Guardrail node — finance-only filter
**Points**: 3 | **Priority**: P0

**Description**: First node in the graph. Checks content safety
(reuse `is_blocked()` from `router.py`), determines if query is
financial (keyword + ticker regex), extracts ticker symbols.
Non-financial queries → decline node. No LLM cost.

**Files**:
- `backend/agents/nodes/guardrail.py` — NEW
- `backend/agents/nodes/__init__.py` — NEW

**Acceptance Criteria**:
- [ ] `guardrail(state) -> dict` function signature
- [ ] Blocked content → `{"next_agent": "decline", "error": "blocked"}`
- [ ] Non-financial query → `{"next_agent": "decline"}`
- [ ] Financial query → `{"next_agent": "router", "tickers": [...]}`
- [ ] `_FINANCIAL_KEYWORDS` set with 50+ financial terms
- [ ] Ticker extraction via `_TICKER_PATTERN` regex
- [ ] Zero LLM calls (pure regex/keyword)
- [ ] Unit tests: financial, non-financial, blocked, ticker extraction

**Dependencies**: S4-1

---

### S4-3: Two-tier router node (Tier 1 — keyword)
**Points**: 2 | **Priority**: P0

**Description**: Keyword-based intent classifier. Scores query
against 4 intent keyword sets (portfolio, stock_analysis,
forecast, research). High-confidence match → supervisor.
No match → LLM classifier (Tier 2).

**Files**:
- `backend/agents/nodes/router_node.py` — NEW

**Acceptance Criteria**:
- [ ] `router_node(state) -> dict` function signature
- [ ] `_INTENT_MAP` with 4 intent categories, 10+ keywords each
- [ ] Best-scoring intent → `{"intent": ..., "next_agent": "supervisor"}`
- [ ] No keyword match → `{"next_agent": "llm_classifier"}`
- [ ] Unit tests: each intent category + ambiguous fallback

**Dependencies**: S4-1, S4-2

---

### S4-4: LLM classifier node (Tier 2)
**Points**: 3 | **Priority**: P1

**Description**: Fallback intent classifier for ambiguous queries.
Uses cheapest Groq model with structured output. Returns intent
or "decline" for non-financial queries.

**Files**:
- `backend/agents/nodes/llm_classifier.py` — NEW

**Acceptance Criteria**:
- [ ] `llm_classifier(state) -> dict` function signature
- [ ] Uses `ChatGroq` with cheapest tier (`llama-3.3-70b-versatile`)
- [ ] Prompt returns exactly one of: portfolio, stock_analysis, forecast, research, decline
- [ ] Invalid LLM output → defaults to "decline"
- [ ] Unit tests (mock LLM response): each category + invalid output

**Dependencies**: S4-1, S4-3

---

### S4-5: Supervisor node — intent-to-agent mapping
**Points**: 1 | **Priority**: P0

**Description**: Simple lookup node. Maps classified intent to
sub-agent node name. No LLM call. Pure dict mapping.

**Files**:
- `backend/agents/nodes/supervisor.py` — NEW

**Acceptance Criteria**:
- [ ] `supervisor(state) -> dict` function signature
- [ ] Maps: portfolio→portfolio, stock_analysis→stock_analyst, forecast→forecaster, research→research
- [ ] Unknown intent → defaults to "stock_analyst"
- [ ] Unit tests: each mapping + unknown fallback

**Dependencies**: S4-1

---

### S4-6: Sub-agent node factory
**Points**: 5 | **Priority**: P0 (core execution engine)

**Description**: Factory function `_make_sub_agent_node()` that
creates a tool-calling loop node from a `SubAgentConfig`. This
is the core execution engine — it invokes `FallbackLLM` with
bound tools, loops on `tool_calls`, collects NDJSON events, and
calls `format_response()` for post-processing.

**Files**:
- `backend/agents/sub_agents.py` — NEW
- `backend/agents/config.py` — MODIFY (add `SubAgentConfig`)

**Acceptance Criteria**:
- [ ] `SubAgentConfig` dataclass: agent_id, name, system_prompt, tool_names, format_response (optional callable)
- [ ] `_make_sub_agent_node(config, tool_registry, llm_factory) -> Callable`
- [ ] Returned node function: `node(state: AgentState) -> dict`
- [ ] Tool-calling loop with MAX_ITERATIONS=15
- [ ] Uses `FallbackLLM` via factory (preserves N-tier cascade)
- [ ] Uses `tool_registry.invoke()` (NOT `tool.invoke()`)
- [ ] Collects NDJSON events: thinking, tool_start, tool_done
- [ ] Events include `"agent"` field for sub-agent identification
- [ ] Calls `format_response(final, messages)` if config has it
- [ ] Tracks `data_sources_used` per tool call
- [ ] Returns: messages, tool_events, final_response, data_sources_used, current_agent
- [ ] Unit tests (mock LLM + tools): single tool call, multi-tool loop, no tool call, format_response

**Dependencies**: S4-1

---

### S4-7: Stock Analyst sub-agent config
**Points**: 3 | **Priority**: P0

**Description**: Define `STOCK_ANALYST_CONFIG` with the existing
stock agent's system prompt, tool list, and `format_response()`
from `stock_agent.py`. Migrate the prompt and post-processing
logic without changing behavior.

**Files**:
- `backend/agents/configs/stock_analyst.py` — NEW
- `backend/agents/configs/__init__.py` — NEW

**Acceptance Criteria**:
- [ ] `STOCK_ANALYST_CONFIG: SubAgentConfig` with agent_id="stock_analyst"
- [ ] System prompt = existing `_STOCK_SYSTEM_PROMPT` from stock_agent.py
- [ ] tool_names = existing 6 stock tools (fetch, info, load, analyse, fetch_multiple, list_available)
- [ ] `format_response()` = existing logic from `StockAgent.format_response()`
- [ ] Uses `build_report()` from `report_builder.py`
- [ ] Produces identical output to current StockAgent for same inputs
- [ ] Unit test: format_response produces expected markdown template

**Dependencies**: S4-6

---

### S4-8: Research sub-agent + tiered news tools
**Points**: 5 | **Priority**: P1

**Description**: Create Research Agent config + 3 new tiered news
tools. News priority: yfinance `.news` (free) → Google News RSS
(free) → SerpAPI (paid, last resort). Remove general `search_web`
dependency.

**Files**:
- `backend/agents/configs/research.py` — NEW
- `backend/tools/news_tools.py` — NEW

**Acceptance Criteria**:
- [ ] `RESEARCH_CONFIG: SubAgentConfig` with agent_id="research"
- [ ] System prompt: financial news research specialist
- [ ] tool_names = ["get_ticker_news", "get_analyst_recommendations", "search_financial_news"]
- [ ] `get_ticker_news(ticker)`: Redis cache (1h) → yfinance `.news` → Google News RSS
- [ ] `get_analyst_recommendations(ticker)`: Redis cache (24h) → yfinance `.recommendations`
- [ ] `search_financial_news(query)`: Redis → yfinance → RSS → SerpAPI (only if <3 free results)
- [ ] Google News RSS parser: `feedparser` library for `news.google.com/rss/search?q={query}`
- [ ] SerpAPI call count logged to observability
- [ ] Unit tests (mock yfinance, RSS, SerpAPI): each tool, cache hit, cache miss, SerpAPI fallback

**Dependencies**: S4-6

---

### S4-9: Synthesis node
**Points**: 2 | **Priority**: P1

**Description**: Final response formatting node. If sub-agent
produced a complete response (>100 chars), pass through. Otherwise,
use synthesis LLM cascade to polish the answer.

**Files**:
- `backend/agents/nodes/synthesis.py` — NEW

**Acceptance Criteria**:
- [ ] `synthesis(state) -> dict` function signature
- [ ] Long response (>100 chars) → pass through unchanged
- [ ] Short/empty response → invoke synthesis LLM cascade
- [ ] Uses existing `_build_synthesis_llm()` pattern from BaseAgent
- [ ] Returns `{"final_response": str}`
- [ ] Unit tests: passthrough, synthesis needed, empty response

**Dependencies**: S4-1, S4-6

---

### S4-10: Decline node
**Points**: 1 | **Priority**: P0

**Description**: Returns polite decline message for non-financial
queries. Zero LLM cost.

**Files**:
- `backend/agents/nodes/decline.py` — NEW

**Acceptance Criteria**:
- [ ] `decline_node(state) -> dict` function signature
- [ ] Returns fixed `_DECLINE_MSG` string
- [ ] Sets `tool_events: []` and `intent: "decline"`
- [ ] Unit test: returns expected message

**Dependencies**: S4-1

---

### S4-11: Graph assembly — build_supervisor_graph()
**Points**: 5 | **Priority**: P0

**Description**: Assemble the full StateGraph with all nodes,
conditional edges, and compile. This is the main integration
point that connects all nodes into a working graph.

**Files**:
- `backend/agents/graph.py` — NEW

**Acceptance Criteria**:
- [ ] `build_supervisor_graph(tool_registry, llm_factory, settings) -> CompiledGraph`
- [ ] All 11 nodes added (guardrail, router, llm_classifier, supervisor, stock_analyst, research, synthesis, log_query, decline + placeholder portfolio, forecaster)
- [ ] Conditional edges: guardrail→router|decline, router→supervisor|llm_classifier, llm_classifier→supervisor|decline, supervisor→{4 agents}
- [ ] Fixed edges: all agents→synthesis, synthesis→log_query, log_query→END, decline→log_query
- [ ] Portfolio + Forecaster nodes present as stubs (return "Coming soon" message) — wired but not functional until Sprint 5
- [ ] Graph compiles without errors
- [ ] Integration test: financial query routes through full graph
- [ ] Integration test: non-financial query hits decline
- [ ] Integration test: blocked content hits decline

**Dependencies**: S4-2, S4-3, S4-4, S4-5, S4-6, S4-7, S4-8, S4-9, S4-10

---

### S4-12: Wire graph into routes.py + ws.py + bootstrap.py
**Points**: 5 | **Priority**: P0

**Description**: Replace old agent dispatch in `_chat()`,
`_chat_stream()`, and `_handle_chat()` with LangGraph invocation.
Add feature flag `USE_LANGGRAPH` (default True) to fall back to
old agents if needed. Update `bootstrap.py` to build the graph.

**Files**:
- `backend/routes.py` — MODIFY
- `backend/ws.py` — MODIFY
- `backend/bootstrap.py` — MODIFY
- `backend/config.py` — MODIFY (add `use_langgraph: bool = True`)

**Acceptance Criteria**:
- [ ] `bootstrap.py`: builds graph via `build_supervisor_graph()`, stores on ChatServer
- [ ] `routes.py::_chat_stream()`: if `use_langgraph`, invoke graph; else old path
- [ ] `routes.py::_chat()`: same feature flag
- [ ] `ws.py::_handle_chat()`: same feature flag
- [ ] Input state built from `ChatRequest` fields
- [ ] NDJSON streaming: tool_events emitted in order, then final event
- [ ] Streaming format backward-compatible (same event types + new `agent` field)
- [ ] Timeout enforced via `agent_timeout_seconds`
- [ ] Old agents (general_agent, stock_agent) still registered as fallback
- [ ] E2E test: send "Analyse AAPL" → get stock analysis response
- [ ] E2E test: send "What is the weather?" → get polite decline

**Dependencies**: S4-11

---

### S4-13: Tests — graph nodes + integration
**Points**: 5 | **Priority**: P1

**Description**: Comprehensive test coverage for all new graph
nodes and the assembled graph.

**Files**:
- `tests/backend/test_graph_nodes.py` — NEW
- `tests/backend/test_graph_integration.py` — NEW
- `tests/backend/test_news_tools.py` — NEW

**Acceptance Criteria**:
- [ ] `test_graph_nodes.py`: unit tests for each node (guardrail, router, llm_classifier, supervisor, synthesis, decline) — mock LLM + tools
- [ ] `test_graph_integration.py`: full graph traversal tests — financial query → stock_analyst → synthesis → log_query
- [ ] `test_graph_integration.py`: non-financial query → guardrail → decline → log_query
- [ ] `test_graph_integration.py`: ambiguous query → router → llm_classifier → supervisor
- [ ] `test_news_tools.py`: get_ticker_news (cache hit, yfinance, RSS), get_analyst_recommendations, search_financial_news (tiered fallback)
- [ ] All tests use `mock.patch` at source module (project rule)
- [ ] 0 failures with `python -m pytest tests/backend/test_graph*.py tests/backend/test_news_tools.py -v`

**Dependencies**: S4-11, S4-12

---

## Sprint Summary

| # | Story | Pts | Priority | Dependencies |
|---|-------|-----|----------|--------------|
| S4-1 | AgentState schema | 2 | P0 | — |
| S4-2 | Guardrail node | 3 | P0 | S4-1 |
| S4-3 | Router node (Tier 1) | 2 | P0 | S4-1 |
| S4-4 | LLM classifier (Tier 2) | 3 | P1 | S4-3 |
| S4-5 | Supervisor node | 1 | P0 | S4-1 |
| S4-6 | Sub-agent factory | 5 | P0 | S4-1 |
| S4-7 | Stock Analyst config | 3 | P0 | S4-6 |
| S4-8 | Research agent + news tools | 5 | P1 | S4-6 |
| S4-9 | Synthesis node | 2 | P1 | S4-6 |
| S4-10 | Decline node | 1 | P0 | S4-1 |
| S4-11 | Graph assembly | 5 | P0 | S4-2..S4-10 |
| S4-12 | Route integration | 5 | P0 | S4-11 |
| S4-13 | Tests | 5 | P1 | S4-11, S4-12 |
| | **Total** | **42** | | |

---

## Execution Order (Recommended)

### Day 1 (Mar 26): Foundation
- S4-1 (State Schema — 2 pts)
- S4-5 (Supervisor — 1 pt)
- S4-10 (Decline — 1 pt)
- S4-2 (Guardrail — 3 pts)

### Day 2 (Mar 27): Routing
- S4-3 (Router Tier 1 — 2 pts)
- S4-4 (LLM Classifier — 3 pts)

### Day 3 (Mar 28): Core Engine
- S4-6 (Sub-Agent Factory — 5 pts)

### Day 4 (Mar 29): Sub-Agents
- S4-7 (Stock Analyst Config — 3 pts)
- S4-8 (Research Agent + News Tools — 5 pts)
- S4-9 (Synthesis — 2 pts)

### Day 5 (Mar 30): Integration
- S4-11 (Graph Assembly — 5 pts)
- S4-12 (Route Integration — 5 pts)

### Day 6 (Mar 31): Testing + Polish
- S4-13 (Tests — 5 pts)
- Bug fixes, edge cases, linting

### Day 7 (Apr 1): Buffer
- Overflow, code review, PR to dev

---

## Checkpoints

| Checkpoint | When | Validation |
|------------|------|------------|
| **CP-1** | After Day 2 | `guardrail` + `router` + `decline` work in isolation unit tests |
| **CP-2** | After Day 3 | Sub-agent factory produces working node with mock LLM + tools |
| **CP-3** | After Day 4 | Stock Analyst + Research nodes produce real output with live LLM |
| **CP-4** | After Day 5 | Full graph compiles, routes.py serves LangGraph responses, old agents still work as fallback |
| **CP-5** | After Day 6 | All tests green, 0 regressions in existing 442 backend tests |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| LangGraph API breaking changes | Pin `langgraph==1.0.10` (already in requirements.txt) |
| FallbackLLM incompatible with LangGraph | Factory pattern isolates LLM creation — no wrapping needed |
| Streaming format breaks frontend | Feature flag `USE_LANGGRAPH` defaults to True; toggle off to revert |
| News tools RSS parsing unreliable | Graceful fallback chain with try/except at each tier |
| Sprint overrun (42 pts > capacity) | Portfolio + Forecaster are stubs → can defer S4-8 (Research) to Sprint 5 if needed |

---

## Future Sprints (Preview)

### Sprint 5 (Apr 2–8): Full Migration
- Portfolio Agent + 6 portfolio tools
- Forecaster Agent (reuse existing forecast_stock)
- Query log + data_gaps Iceberg tables
- Query logger node (log_query) fully wired
- Daily gap filler job
- Remove old general_agent.py + stock_agent.py
- Remove feature flag (LangGraph-only)

### Sprint 6 (Apr 9–15): Portfolio Intelligence
- Risk metrics (beta, Sharpe, VaR, correlation matrix)
- Rebalancing suggestions
- Dividend income projection
- Admin query-gap dashboard endpoint
- Semantic query cache (Redis embeddings)

---

*Generated: 2026-03-21 | Strategy: Agile | Sprint: 4 of 6*
*Ready for Jira ticket creation via `/sc:implement` or manual entry*
