# Architecture Design: LangGraph Agentic Framework

> Produced by `/sc:design` — architecture specification.
> Prereq: `docs/specs/agentic-framework-requirements.md`
> Next: `/sc:workflow` for sprint-level implementation plan.

---

## 1. System Overview

```
                        ┌──────────────────────┐
                        │   Frontend (Next.js)  │
                        │  POST /v1/chat/stream │
                        │    WS /ws/chat        │
                        └──────────┬───────────┘
                                   │ NDJSON / WebSocket
                                   ▼
                        ┌──────────────────────┐
                        │   FastAPI Gateway     │
                        │  routes.py / ws.py    │
                        └──────────┬───────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     Finance Guardrail       │
                    │  (is_financial? → decline)   │
                    └──────────────┬──────────────┘
                                   │ financial query
                    ┌──────────────▼──────────────┐
                    │     Two-Tier Router          │
                    │  Tier 1: keyword/regex       │
                    │  Tier 2: LLM classifier      │
                    └──────────────┬──────────────┘
                                   │ intent: portfolio |
                                   │   stock_analysis |
                                   │   forecast |
                                   │   research
                    ┌──────────────▼──────────────┐
                    │   LangGraph Supervisor       │
                    │      (StateGraph)             │
                    ├────┬────┬────┬───────────────┤
                    │    │    │    │               │
                    ▼    ▼    ▼    ▼               ▼
               Portfolio Stock  Fore-  Research  Synthesis
               Agent    Agent  caster  Agent     Node
                 │        │      │       │         │
                 ▼        ▼      ▼       ▼         ▼
              Iceberg  Iceberg Iceberg yfinance  FallbackLLM
              + Redis  →yfin   →Prophet .news    (N-tier)
                       fallbk  (stale)  →Google
                                        →SerpAPI
```

---

## 2. LangGraph State Schema

```python
"""backend/agents/graph_state.py"""

from typing import Annotated, Sequence
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """Shared state flowing through the supervisor graph.

    Fields use reducer annotations where needed so that
    parallel branches merge cleanly.
    """

    # ── Core conversation ─────────────────────────
    messages: Annotated[
        Sequence[BaseMessage], add_messages
    ]
    user_input: str           # original user query
    user_id: str              # JWT user ID
    history: list[dict]       # raw frontend history

    # ── Routing ───────────────────────────────────
    intent: str               # classified intent
    next_agent: str           # next node to invoke
    current_agent: str        # currently executing

    # ── Data context ──────────────────────────────
    tickers: list[str]        # extracted ticker symbols
    data_sources_used: list[str]  # iceberg/redis/yfinance/serpapi
    was_local_sufficient: bool

    # ── Sub-agent results ─────────────────────────
    tool_events: list[dict]   # NDJSON events for streaming
    final_response: str       # synthesized answer
    error: str | None         # error message if failed
```

### Reducer Strategy

| Field | Reducer | Why |
|-------|---------|-----|
| `messages` | `add_messages` | Appends; never overwrite history |
| `tool_events` | `lambda a, b: a + b` | Merge events from parallel agents |
| `data_sources_used` | `lambda a, b: list(set(a + b))` | Deduplicate sources |
| All others | Last-write-wins (default) | Single writer per field |

---

## 3. Graph Topology

```
START
  │
  ▼
┌─────────────┐
│  guardrail  │──(non-financial)──▶ decline_node ──▶ END
└──────┬──────┘
       │ (financial)
       ▼
┌─────────────┐
│   router    │──(confidence < 0.7)──▶ llm_classifier
└──────┬──────┘                            │
       │ intent                            │
       ◄───────────────────────────────────┘
       │
       ▼
┌─────────────────┐
│   supervisor    │
└──┬──┬──┬──┬─────┘
   │  │  │  │
   ▼  ▼  ▼  ▼
 portfolio  stock_analyst  forecaster  research
   │           │              │           │
   ▼           ▼              ▼           ▼
(tool loop) (tool loop)  (tool loop) (tool loop)
   │           │              │           │
   └─────┬─────┘──────────────┘───────────┘
         │
         ▼
   ┌──────────┐
   │ synthesis │
   └─────┬────┘
         │
         ▼
   ┌──────────┐
   │  log_query│──▶ END
   └──────────┘
```

### Node Descriptions

| Node | Purpose | LLM Cost | Tools |
|------|---------|----------|-------|
| `guardrail` | Check if query is financial; extract tickers | None (regex) | None |
| `router` | Keyword-based intent classification (Tier 1) | None (regex) | None |
| `llm_classifier` | LLM-based intent classification (Tier 2) | 1 cheap call | None |
| `supervisor` | Routes to sub-agent(s) based on intent | None (lookup) | None |
| `portfolio` | Portfolio queries (holdings, P&L, allocation) | 1-2 LLM calls | 6 tools |
| `stock_analyst` | Stock data + technical analysis | 2-4 LLM calls | 6 tools |
| `forecaster` | Prophet forecasting + targets | 1-2 LLM calls | 3 tools |
| `research` | News + analyst recs + sentiment | 1-2 LLM calls | 3 tools |
| `synthesis` | Final response formatting + post-processing | 1 LLM call | None |
| `log_query` | Persist to query_log table | None | None |
| `decline_node` | Return polite decline message | None | None |

---

## 4. Node Specifications

### 4.1 Guardrail Node

```python
def guardrail(state: AgentState) -> dict:
    """Check if query is financial. Extract tickers."""
    from agents.router import is_blocked, _TICKER_PATTERN

    user_input = state["user_input"]

    # Content safety
    if is_blocked(user_input):
        return {
            "next_agent": "decline",
            "error": "blocked",
        }

    # Financial relevance check (fast regex)
    is_financial = _is_financial_query(user_input)
    if not is_financial:
        return {"next_agent": "decline"}

    # Extract tickers
    tickers = _TICKER_PATTERN.findall(user_input)

    return {
        "tickers": tickers,
        "next_agent": "router",
    }
```

**`_is_financial_query()`**: Checks for presence of any financial
keyword (stock, portfolio, forecast, price, dividend, risk, sector,
market, buy, sell, hold, OHLCV, RSI, MACD, PE ratio, etc.) OR a
ticker pattern. If none found → non-financial → decline.

### 4.2 Router Node (Tier 1)

```python
_INTENT_MAP = {
    "portfolio": {
        "portfolio", "holdings", "allocation",
        "weightage", "rebalance", "diversify",
        "sector breakdown", "my stocks",
        "invested", "p&l", "returns",
    },
    "stock_analysis": {
        "analyse", "analyze", "technical",
        "indicators", "rsi", "macd", "sma",
        "support", "resistance", "ohlcv",
        "compare", "fetch", "load",
    },
    "forecast": {
        "forecast", "predict", "prophet",
        "target", "price target", "outlook",
        "6 month", "3 month", "9 month",
    },
    "research": {
        "news", "headline", "sentiment",
        "analyst", "recommendation",
        "market trend", "sector trend",
    },
}

def router(state: AgentState) -> dict:
    """Tier 1: keyword-based intent classification."""
    query = state["user_input"].lower()

    scores = {}
    for intent, keywords in _INTENT_MAP.items():
        score = sum(1 for kw in keywords if kw in query)
        if score > 0:
            scores[intent] = score

    if not scores:
        # Ambiguous → Tier 2 LLM classifier
        return {"next_agent": "llm_classifier"}

    best = max(scores, key=scores.get)
    return {
        "intent": best,
        "next_agent": "supervisor",
    }
```

### 4.3 LLM Classifier Node (Tier 2)

```python
def llm_classifier(state: AgentState) -> dict:
    """Tier 2: LLM-based intent classification.

    Uses cheapest/fastest model in the cascade.
    """
    from langchain_groq import ChatGroq

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0,
        max_retries=0,
    )

    prompt = (
        "Classify this financial query into exactly "
        "one category.\n"
        "Categories: portfolio, stock_analysis, "
        "forecast, research\n"
        "If not financial at all: decline\n\n"
        f"Query: {state['user_input']}\n"
        "Category:"
    )

    resp = llm.invoke([HumanMessage(content=prompt)])
    intent = resp.content.strip().lower()

    if intent == "decline" or intent not in {
        "portfolio", "stock_analysis",
        "forecast", "research",
    }:
        return {"next_agent": "decline"}

    return {
        "intent": intent,
        "next_agent": "supervisor",
    }
```

### 4.4 Supervisor Node

```python
def supervisor(state: AgentState) -> dict:
    """Route to the sub-agent matching the classified intent."""
    intent = state["intent"]

    # Direct mapping — no LLM call needed
    agent_map = {
        "portfolio": "portfolio",
        "stock_analysis": "stock_analyst",
        "forecast": "forecaster",
        "research": "research",
    }

    next_agent = agent_map.get(intent, "stock_analyst")
    return {"next_agent": next_agent}
```

### 4.5 Sub-Agent Nodes (Tool-Calling Loop)

Each sub-agent is itself a **mini-graph** with a tool-calling loop:

```python
def _make_sub_agent_node(
    agent_config: SubAgentConfig,
    tool_registry: ToolRegistry,
    fallback_llm_factory: Callable,
):
    """Factory: create a sub-agent node function.

    Each sub-agent:
    1. Builds its system prompt + messages
    2. Invokes FallbackLLM with bound tools
    3. Loops on tool_calls (max 15 iterations)
    4. Collects NDJSON tool events for streaming
    5. Returns final response + events
    """

    def node(state: AgentState) -> dict:
        llm = fallback_llm_factory(
            agent_id=agent_config.agent_id,
        )
        tools = tool_registry.get_tools(
            agent_config.tool_names,
        )
        llm_with_tools = llm.bind_tools(tools)

        messages = [
            SystemMessage(content=agent_config.system_prompt),
            *state["messages"],
        ]
        events = []
        data_sources = []

        for iteration in range(MAX_ITERATIONS):
            events.append({
                "type": "thinking",
                "iteration": iteration + 1,
                "agent": agent_config.agent_id,
            })

            response = llm_with_tools.invoke(messages)
            messages.append(response)

            if not response.tool_calls:
                break

            for tc in response.tool_calls:
                name = tc["name"]
                args = tc.get("args", {})

                events.append({
                    "type": "tool_start",
                    "tool": name,
                    "args": args,
                    "agent": agent_config.agent_id,
                })

                result = tool_registry.invoke(name, args)

                # Track data source
                source = _infer_data_source(name, result)
                data_sources.append(source)

                events.append({
                    "type": "tool_done",
                    "tool": name,
                    "preview": result[:200],
                    "agent": agent_config.agent_id,
                })

                messages.append(ToolMessage(
                    content=result,
                    tool_call_id=tc["id"],
                ))

        final = response.content or ""

        # Post-processing (format_response)
        if hasattr(agent_config, "format_response"):
            final = agent_config.format_response(
                final, messages,
            )

        return {
            "messages": [AIMessage(content=final)],
            "tool_events": events,
            "final_response": final,
            "data_sources_used": data_sources,
            "current_agent": agent_config.agent_id,
        }

    return node
```

### 4.6 Sub-Agent Tool Assignments

| Agent | Tools | Data Source Priority |
|-------|-------|---------------------|
| **Portfolio** | `get_portfolio_holdings`, `get_portfolio_performance`, `get_sector_allocation`, `get_risk_metrics`, `get_dividend_projection`, `suggest_rebalancing` | Iceberg only |
| **Stock Analyst** | `fetch_stock_data`, `get_stock_info`, `load_stock_data`, `analyse_stock_price`, `fetch_multiple_stocks`, `list_available_stocks` | Iceberg → yfinance |
| **Forecaster** | `forecast_stock`, `get_forecast_summary`, `get_portfolio_forecast` | Iceberg → Prophet (if stale) |
| **Research** | `get_ticker_news`, `get_analyst_recommendations`, `search_financial_news` | yfinance .news → Google News RSS → SerpAPI |

### 4.7 Synthesis Node

```python
def synthesis(state: AgentState) -> dict:
    """Final response formatting.

    If the sub-agent already produced a complete response,
    pass through. Otherwise, use the synthesis LLM cascade
    to produce a polished answer.
    """
    final = state.get("final_response", "")

    if final and len(final) > 100:
        # Sub-agent produced a complete response
        return {"final_response": final}

    # Need synthesis — gather all tool results
    llm = _build_synthesis_llm()
    synthesis_prompt = (
        "Synthesize a clear, actionable financial "
        "analysis from the following data. Include "
        "specific numbers and recommendations.\n\n"
        f"{final}"
    )
    resp = llm.invoke([
        SystemMessage(content=synthesis_prompt),
        *state["messages"],
    ])

    return {"final_response": resp.content}
```

### 4.8 Query Logger Node

```python
def log_query(state: AgentState) -> dict:
    """Persist query metadata to Iceberg query_log table."""
    from stocks.repository import StockRepository

    repo = StockRepository()
    repo.insert_query_log({
        "timestamp": datetime.utcnow(),
        "user_id": state["user_id"],
        "query_text": state["user_input"],
        "classified_intent": state["intent"],
        "sub_agent_invoked": state["current_agent"],
        "tools_used": [
            e["tool"] for e in state["tool_events"]
            if e["type"] == "tool_start"
        ],
        "data_sources_used": state["data_sources_used"],
        "was_local_sufficient": (
            "yfinance" not in state["data_sources_used"]
            and "serpapi" not in state["data_sources_used"]
        ),
        "gap_tickers": [
            t for t in state["tickers"]
            if not _has_fresh_local_data(repo, t)
        ],
    })

    return {}
```

### 4.9 Decline Node

```python
_DECLINE_MSG = (
    "I'm specialized in stock analysis and portfolio "
    "management. I can help with market data, stock "
    "analysis, forecasts, and portfolio questions. "
    "What would you like to know about your "
    "investments?"
)

def decline_node(state: AgentState) -> dict:
    """Polite decline for non-financial queries."""
    return {
        "final_response": _DECLINE_MSG,
        "tool_events": [],
    }
```

---

## 5. Graph Construction

```python
"""backend/agents/graph.py"""

from langgraph.graph import StateGraph, START, END

def build_supervisor_graph(
    tool_registry: ToolRegistry,
    fallback_llm_factory: Callable,
    settings: Settings,
) -> CompiledGraph:
    """Build the LangGraph supervisor graph."""

    # Create sub-agent nodes from configs
    portfolio_node = _make_sub_agent_node(
        PORTFOLIO_CONFIG, tool_registry,
        fallback_llm_factory,
    )
    stock_node = _make_sub_agent_node(
        STOCK_ANALYST_CONFIG, tool_registry,
        fallback_llm_factory,
    )
    forecast_node = _make_sub_agent_node(
        FORECASTER_CONFIG, tool_registry,
        fallback_llm_factory,
    )
    research_node = _make_sub_agent_node(
        RESEARCH_CONFIG, tool_registry,
        fallback_llm_factory,
    )

    # Build graph
    g = StateGraph(AgentState)

    # Add all nodes
    g.add_node("guardrail", guardrail)
    g.add_node("router", router)
    g.add_node("llm_classifier", llm_classifier)
    g.add_node("supervisor", supervisor)
    g.add_node("portfolio", portfolio_node)
    g.add_node("stock_analyst", stock_node)
    g.add_node("forecaster", forecast_node)
    g.add_node("research", research_node)
    g.add_node("synthesis", synthesis)
    g.add_node("log_query", log_query)
    g.add_node("decline", decline_node)

    # ── Edges ──────────────────────────────────
    g.add_edge(START, "guardrail")

    # Guardrail → router or decline
    g.add_conditional_edges(
        "guardrail",
        lambda s: s["next_agent"],
        {
            "router": "router",
            "decline": "decline",
        },
    )

    # Router → supervisor or LLM classifier
    g.add_conditional_edges(
        "router",
        lambda s: s["next_agent"],
        {
            "supervisor": "supervisor",
            "llm_classifier": "llm_classifier",
        },
    )

    # LLM classifier → supervisor or decline
    g.add_conditional_edges(
        "llm_classifier",
        lambda s: s["next_agent"],
        {
            "supervisor": "supervisor",
            "decline": "decline",
        },
    )

    # Supervisor → sub-agent
    g.add_conditional_edges(
        "supervisor",
        lambda s: s["next_agent"],
        {
            "portfolio": "portfolio",
            "stock_analyst": "stock_analyst",
            "forecaster": "forecaster",
            "research": "research",
        },
    )

    # All sub-agents → synthesis
    g.add_edge("portfolio", "synthesis")
    g.add_edge("stock_analyst", "synthesis")
    g.add_edge("forecaster", "synthesis")
    g.add_edge("research", "synthesis")

    # Synthesis → log → END
    g.add_edge("synthesis", "log_query")
    g.add_edge("log_query", END)

    # Decline → log → END
    g.add_edge("decline", "log_query")
    g.add_edge("log_query", END)

    return g.compile()
```

---

## 6. New Iceberg Tables

### 6.1 `stocks.query_log`

| Column | Type | Description |
|--------|------|-------------|
| `id` | string | UUID |
| `timestamp` | timestamp | Query time (UTC) |
| `user_id` | string | Authenticated user |
| `query_text` | string | Raw user message |
| `classified_intent` | string | portfolio/stock_analysis/forecast/research/decline |
| `sub_agent_invoked` | string | Agent that handled the query |
| `tools_used` | string (JSON) | `["fetch_stock_data", "analyse_stock_price"]` |
| `data_sources_used` | string (JSON) | `["iceberg", "yfinance"]` |
| `was_local_sufficient` | boolean | True if no external API called |
| `response_time_ms` | int | End-to-end latency |
| `gap_tickers` | string (JSON) | Tickers that triggered external fetch |

**Partition**: `user_id` (matches chat_audit_log pattern)

### 6.2 `stocks.data_gaps`

| Column | Type | Description |
|--------|------|-------------|
| `id` | string | UUID |
| `detected_at` | timestamp | When gap was found |
| `ticker` | string | Missing ticker |
| `data_type` | string | ohlcv/company_info/dividends/quarterly |
| `query_count` | int | How many times this gap was hit |
| `resolved_at` | timestamp | Null | When gap was filled |
| `resolution` | string | Null | "yfinance_fetch" / "manual" |

**Partition**: None (small table, full-scan OK)

---

## 7. New Tools

### 7.1 Portfolio Agent Tools (NEW)

```python
# backend/tools/portfolio_tools.py

@tool
def get_portfolio_holdings(user_id: str) -> str:
    """Get user's portfolio holdings with current values.

    Returns: ticker, quantity, avg_price, current_price,
    market_value, unrealized_pnl, weight_pct
    Source: Iceberg portfolio_transactions + ohlcv
    """

@tool
def get_sector_allocation(user_id: str) -> str:
    """Get portfolio sector breakdown by market value.

    Returns: sector, weight_pct, market_value, ticker_count
    Source: Iceberg portfolio_transactions + company_info
    """

@tool
def get_risk_metrics(user_id: str) -> str:
    """Compute portfolio risk metrics.

    Returns: beta, sharpe_ratio, var_95, max_drawdown,
    annualized_volatility, correlation_matrix
    Source: Iceberg ohlcv (daily returns computation)
    """

@tool
def get_dividend_projection(user_id: str) -> str:
    """Project annual dividend income from holdings.

    Returns: ticker, annual_dividend, yield_pct, total
    Source: Iceberg dividends + portfolio_transactions
    """

@tool
def suggest_rebalancing(user_id: str) -> str:
    """Suggest portfolio rebalancing actions.

    Identifies over-concentrated positions (>30% sector),
    high-correlation pairs, and suggests adjustments.
    Source: Iceberg (all local computation)
    """

@tool
def get_portfolio_performance(
    user_id: str, period: str = "6M",
) -> str:
    """Get portfolio performance over a period.

    Returns: total_return, annualized_return, vs_benchmark,
    best_day, worst_day, max_drawdown
    Source: Iceberg ohlcv + portfolio_transactions
    """
```

### 7.2 Research Agent Tools (NEW)

```python
# backend/tools/news_tools.py

@tool
def get_ticker_news(ticker: str) -> str:
    """Get latest news for a ticker.

    Priority: 1) Redis cache (1h TTL)
              2) yfinance .news (free)
              3) Google News RSS (free)
    Never calls SerpAPI.
    """

@tool
def get_analyst_recommendations(ticker: str) -> str:
    """Get analyst buy/hold/sell recommendations.

    Source: yfinance .recommendations (free)
    Cached in Redis (24h TTL)
    """

@tool
def search_financial_news(query: str) -> str:
    """Search for financial news across sources.

    Priority: 1) Redis cache
              2) yfinance .news for extracted tickers
              3) Google News RSS feed parse
              4) SerpAPI (LAST RESORT, paid)

    SerpAPI only called if free sources return <3 results.
    """
```

---

## 8. Streaming Integration

### 8.1 HTTP Streaming (`POST /v1/chat/stream`)

```python
# backend/routes.py — updated _chat_stream

async def _chat_stream(req: ChatRequest):
    """NDJSON streaming via LangGraph."""
    input_state = {
        "messages": _build_messages(
            req.message, req.history,
        ),
        "user_input": req.message,
        "user_id": req.user_id,
        "history": req.history,
        "tickers": [],
        "data_sources_used": [],
        "was_local_sufficient": True,
        "tool_events": [],
        "final_response": "",
        "error": None,
        "intent": "",
        "next_agent": "",
        "current_agent": "",
    }

    def generate():
        event_queue = queue.Queue()

        def run():
            set_current_user(req.user_id)
            # Run graph synchronously
            result = graph.invoke(input_state)

            # Emit collected tool events
            for event in result.get("tool_events", []):
                event_queue.put(
                    json.dumps(event) + "\n"
                )

            # Emit final
            event_queue.put(json.dumps({
                "type": "final",
                "response": result["final_response"],
                "agent": result.get(
                    "current_agent", ""
                ),
            }) + "\n")

            event_queue.put(None)

        worker = threading.Thread(
            target=run, daemon=True,
        )
        worker.start()

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                event_queue.put(json.dumps({
                    "type": "error",
                    "message": "Agent timeout",
                }) + "\n")
                break
            try:
                item = event_queue.get(
                    timeout=min(remaining, 1.0),
                )
            except queue.Empty:
                continue
            if item is None:
                break
            yield item

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
    )
```

### 8.2 Event Format (backward compatible)

```jsonl
{"type": "thinking", "iteration": 1, "agent": "stock_analyst"}
{"type": "tool_start", "tool": "fetch_stock_data", "args": {"ticker": "AAPL"}, "agent": "stock_analyst"}
{"type": "tool_done", "tool": "fetch_stock_data", "preview": "AAPL: 7837 rows...", "agent": "stock_analyst"}
{"type": "thinking", "iteration": 2, "agent": "stock_analyst"}
{"type": "tool_start", "tool": "analyse_stock_price", "args": {"ticker": "AAPL"}, "agent": "stock_analyst"}
{"type": "tool_done", "tool": "analyse_stock_price", "preview": "RSI: 55.3...", "agent": "stock_analyst"}
{"type": "final", "response": "## AAPL Analysis\n...", "agent": "stock_analyst"}
```

**New field**: `"agent"` added to every event so the frontend can
show which sub-agent is working. Backward compatible — existing
frontend ignores unknown fields.

---

## 9. Daily Gap Filler

```python
"""backend/jobs/gap_filler.py"""

import schedule
from stocks.repository import StockRepository

def fill_data_gaps():
    """Daily job: fetch missing data for queried tickers."""
    repo = StockRepository()

    # Read unfilled gaps
    gaps = repo.get_unfilled_data_gaps()

    for gap in gaps:
        ticker = gap["ticker"]
        data_type = gap["data_type"]

        try:
            if data_type == "ohlcv":
                _fetch_ohlcv(ticker)
            elif data_type == "company_info":
                _fetch_company_info(ticker)
            elif data_type == "dividends":
                _fetch_dividends(ticker)
            elif data_type == "quarterly":
                _fetch_quarterly(ticker)

            repo.resolve_data_gap(
                gap["id"], "yfinance_fetch",
            )
        except Exception:
            logger.warning(
                "Gap fill failed: %s/%s",
                ticker, data_type,
            )

# Schedule: 6 PM IST (12:30 UTC) for NSE close
# Schedule: 9 PM IST (15:30 UTC) for NYSE close
schedule.every().day.at("12:30").do(fill_data_gaps)
schedule.every().day.at("15:30").do(fill_data_gaps)
```

---

## 10. File Structure (New/Modified)

```
backend/agents/
├── graph_state.py          NEW — AgentState TypedDict
├── graph.py                NEW — build_supervisor_graph()
├── sub_agents.py           NEW — SubAgentConfig + node factory
├── nodes/
│   ├── guardrail.py        NEW — finance filter + ticker extraction
│   ├── router_node.py      NEW — two-tier intent router
│   ├── supervisor.py       NEW — intent → agent mapping
│   ├── synthesis.py        NEW — final response formatting
│   ├── log_query.py        NEW — query_log persistence
│   └── decline.py          NEW — polite decline
├── router.py               KEEP — keyword matching (used by router_node)
├── config.py               MODIFY — add SubAgentConfig
├── base.py                 KEEP — BaseAgent (used by sub_agents)
├── general_agent.py        DEPRECATE (replaced by graph)
├── stock_agent.py          DEPRECATE (replaced by graph)
├── report_builder.py       KEEP — used by synthesis node

backend/tools/
├── portfolio_tools.py      NEW — 6 portfolio tools
├── news_tools.py           NEW — 3 research tools (yfinance/RSS/SerpAPI tiered)
├── stock_tools.py          KEEP — existing stock tools
├── search_tool.py          DEPRECATE — SerpAPI general search removed
├── time_tool.py            DEPRECATE — non-financial, removed

backend/jobs/
├── gap_filler.py           NEW — daily data gap resolution

stocks/
├── repository.py           MODIFY — add query_log + data_gaps methods
├── schemas.py              MODIFY — add query_log + data_gaps schemas
```

---

## 11. Migration Strategy

### Phase 1 (Sprint 4): Foundation
- Build `graph_state.py`, `graph.py`, node stubs
- Wire graph into `routes.py` and `ws.py`
- Migrate Stock Analyst + Research agents first
- Keep old agents as fallback (feature flag)

### Phase 2 (Sprint 5): Full Migration
- Add Portfolio Agent + Forecaster Agent
- Add portfolio_tools.py + news_tools.py
- Remove old general_agent.py + stock_agent.py
- Add query_log + data_gaps Iceberg tables

### Phase 3 (Sprint 5): Intelligence
- Daily gap filler job
- Admin query-gap dashboard endpoint
- Local-data-first checks in all tools

### Phase 4 (Sprint 6): Portfolio Analytics
- Risk metrics (beta, Sharpe, VaR, correlation)
- Rebalancing suggestions
- Dividend income projection

---

## 12. Testing Strategy

| Layer | Test Type | Files |
|-------|-----------|-------|
| Nodes | Unit tests (mock LLM + tools) | `tests/backend/test_graph_nodes.py` |
| Graph | Integration tests (full graph, mocked external APIs) | `tests/backend/test_graph_integration.py` |
| Tools | Unit tests (mock Iceberg + yfinance) | `tests/backend/test_portfolio_tools.py`, `test_news_tools.py` |
| Streaming | E2E (live services) | `e2e/tests/frontend/chat.spec.ts` (updated) |
| Gap filler | Unit test (mock repo) | `tests/backend/test_gap_filler.py` |

---

*Generated: 2026-03-21 | Type: Architecture | Format: Spec*
*Prereq: agentic-framework-requirements.md*
*Next: `/sc:workflow` for sprint stories + Jira tickets*
