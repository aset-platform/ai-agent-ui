# Backend Overview

The backend is a Python FastAPI server that runs an agentic loop powered by LangChain. All server-level state is encapsulated in a single `ChatServer` class rather than living in module-level globals.

---

## Module Map

```
main.py              Entry point. ChatServer class owns all state.
 ├── config.py       Pydantic Settings — env vars + .env file
 ├── logging_config.py setup_logging() — console + rotating file
 ├── llm_fallback.py  FallbackLLM — N-tier Groq cascade + Anthropic fallback
 ├── token_budget.py  Sliding-window TPM/RPM budget tracker
 ├── observability.py Thread-safe metrics + tier health monitoring
 ├── message_compressor.py  3-stage message compression
 ├── tools/
 │    ├── registry.py   ToolRegistry — maps names → BaseTool instances
 │    ├── time_tool.py  get_current_time @tool
 │    └── search_tool.py search_web @tool
 └── agents/
      ├── registry.py      AgentRegistry — maps agent_id → BaseAgent instances
      ├── base.py          BaseAgent ABC
      ├── config.py        AgentConfig dataclass
      ├── loop.py          Agentic loop logic (MAX_ITERATIONS=15)
      ├── stream.py        NDJSON streaming support
      ├── general_agent.py GeneralAgent(BaseAgent) + create_general_agent factory
      └── stock_agent.py   StockAgent(BaseAgent) + create_stock_agent factory
```

### Dependency Rules

- `config.py` and `logging_config.py` have **no internal imports** — they only use the stdlib and third-party libraries.
- `tools/*` modules do not import from `agents/*`.
- `agents/base.py` imports `tools.registry.ToolRegistry` (for tool lookup during the loop).
- `agents/general_agent.py` imports from `agents/base.py` and `llm_fallback.py`.
- `main.py` is the only module that imports from all layers and wires them together.

---

## Startup Sequence

When uvicorn runs `uvicorn main:app --port 8181 --reload`, it imports `main.py`. Three module-level statements at the bottom of the file execute in order:

```python
settings = get_settings()                            # 1
setup_logging(level=settings.log_level,              # 2
              log_to_file=settings.log_to_file)
server = ChatServer(settings)                        # 3
app = server.app
```

### Inside `ChatServer.__init__()`

```
ChatServer.__init__(settings)
 │
 ├── ToolRegistry()                    ← empty registry created
 ├── _register_tools()
 │    ├── tool_registry.register(get_current_time)
 │    └── tool_registry.register(search_web)
 │
 ├── AgentRegistry()                   ← empty registry created
 ├── _register_agents()
 │    └── create_general_agent(tool_registry)
 │         ├── AgentConfig(agent_id="general",
 │         │    groq_model_tiers=["llama-3.3-70b-versatile", ...],
 │         │    tool_names=["get_current_time", "search_web"], ...)
 │         └── GeneralAgent(config, tool_registry)
 │              └── _setup()
 │                   ├── _build_llm() → FallbackLLM(groq_models=...,
 │                   │     anthropic_model="claude-sonnet-4-6")
 │                   ├── tool_registry.get_tools(["get_current_time", "search_web"])
 │                   └── llm.bind_tools(tools)  ← bakes tool schemas into LLM
 │
 └── _create_app()
      ├── FastAPI(title="AI Agent API")
      ├── CORSMiddleware(allow_origins=["*"], ...)
      ├── app.post("/chat")(self._chat_handler)    ← bound method
      └── app.get("/agents")(self._list_agents_handler)
```

!!! note "Tools must be registered before agents"
    `_register_tools()` is always called before `_register_agents()`. Agents fetch their tools from the registry during `_setup()`, so the tools must already be present.

---

## Agentic Loop

The complete loop lives in `BaseAgent.run()` in `agents/base.py`. Every HTTP request to `POST /chat` triggers one call to `agent.run()`.

```
agent.run(user_input, history)
 │
 ├── _build_messages(user_input, history)
 │    ├── For each item in history:
 │    │    "user"      → HumanMessage(content)
 │    │    "assistant" → AIMessage(content)
 │    │    (other)     → silently ignored
 │    └── Append HumanMessage(user_input) at the end
 │
 └── while True:
      │
      ├── response = llm_with_tools.invoke(messages)
      ├── messages.append(response)           ← AIMessage added to context
      │
      ├── if not response.tool_calls → break  ← LLM is done
      │
      └── for tc in response.tool_calls:
           ├── tool_registry.invoke(tc["name"], tc["args"]) → result str
           └── messages.append(
                   ToolMessage(content=result, tool_call_id=tc["id"])
               )
           (loop back to invoke)

 └── return response.content or "No response"
```

### Message Array Evolution

**Single-tool example** ("What time is it?"):

| Step | Message added |
|------|--------------|
| Start | `HumanMessage("What time is it?")` |
| Iteration 1 invoke | `AIMessage(content="", tool_calls=[{name:"get_current_time", id:"call_abc"}])` |
| Tool result | `ToolMessage(content="2026-02-22 14:37:55", tool_call_id="call_abc")` |
| Iteration 2 invoke | `AIMessage(content="The current time is 2:37 PM.")` — no tool calls → break |

### Key LangChain Protocol Details

- The `tool_call_id` in `ToolMessage` **must match** the `id` in the corresponding `AIMessage.tool_calls` entry. LangChain uses this to associate results with calls.
- Multiple tools can be called in a single iteration — the loop creates one `ToolMessage` per tool call.
- When `response.tool_calls` is an empty list (`[]`), the loop exits and `response.content` is returned as the final answer.

---

## Route Binding Pattern

Routes are registered using bound class methods instead of decorated module-level functions:

```python
def _create_app(self) -> FastAPI:
    app = FastAPI(title="AI Agent API")
    # ...
    app.post("/chat", response_model=ChatResponse)(self._chat_handler)
    app.get("/agents")(self._list_agents_handler)
    return app
```

This gives each handler access to `self.agent_registry` and `self.tool_registry` without any global state. To add a new route, define a new method on `ChatServer` and register it in `_create_app()`.

---

## Adding New Agents or Tools

**New tool:**

1. Create `backend/tools/my_tool.py` with a `@tool`-decorated function.
2. Import it in `main.py`.
3. Register it in `ChatServer._register_tools()`:
   ```python
   self.tool_registry.register(my_tool)
   ```
4. Add the tool name to the `tool_names` list in the relevant `AgentConfig`.

**New agent:**

1. Create a subclass of `BaseAgent` and implement `_build_llm()`.
2. Write a factory function that builds the `AgentConfig` and returns the agent.
3. Register it in `ChatServer._register_agents()`:
   ```python
   my_agent = create_my_agent(self.tool_registry)
   self.agent_registry.register(my_agent)
   ```
4. Clients can then target it by sending `"agent_id": "my-agent-id"` in the request body.

---

## Context-Aware Chat

Conversation context is tracked across turns so that follow-up
questions ("what about its PE ratio?") are resolved against the
previous exchange rather than treated as isolated queries.

### Key files

| File | Purpose |
|------|---------|
| `backend/agents/conversation_context.py` | `ConversationContext` dataclass + in-memory session store |
| `backend/agents/nodes/topic_classifier.py` | Follow-up vs new-topic detection node |

### How it works

**Session store** — `ConversationContext` holds a rolling summary,
the last agent used, and the tickers referenced in recent turns.
Instances are keyed by `session_id` in a module-level dict.

**Topic classifier** — after the cache check and before the
guardrail, `topic_classifier` runs a 1-shot LLM call to decide
whether the incoming message continues the previous topic. If the
LLM call fails for any reason, the node degrades gracefully to
`"new_topic"` so the request still proceeds.

**Rolling summary** — after every turn `_update_conversation_context()`
re-summarises the exchange via the Ollama/Groq cascade and stores
the new summary back in the session store.

**Context injection** — `_build_messages(session_id)` in
`BaseAgent` prepends a `[Conversation Context]` block to the
system prompt when a non-empty session summary exists, giving
every sub-agent awareness of what was discussed earlier.

---

## Recency-Aware News

All news and sentiment tools filter and weight articles by age so
that stale headlines do not distort analysis.

### Date utilities — `backend/tools/_date_utils.py`

| Function | Description |
|----------|-------------|
| `parse_published(value)` | Parses Unix timestamps, RFC 2822, and ISO 8601 strings into `datetime` |
| `is_within_window(dt, days_back)` | Returns `True` if `dt` is within the last `days_back` days |
| `time_decay_weight(dt)` | Returns a float weight based on article age (see table below) |

**Conservative policy:** if a date cannot be parsed, the article
is **kept** (weight 1.0) rather than dropped. This avoids silently
discarding valid headlines from sources with non-standard date
formats.

### `days_back` parameter

`get_ticker_news`, `search_financial_news`, and
`score_ticker_sentiment` all accept a `days_back=7` keyword
argument. Articles outside the window are excluded before
sentiment scoring begins.

### Time-decay weight table

| Age | Weight |
|-----|--------|
| 0–2 days | 1.0 |
| 3–7 days | 0.5 |
| 8–30 days | 0.25 |
| > 30 days | 0.1 |

Weights are applied as multipliers when computing the weighted
average sentiment score in `_sentiment_scorer.py`.

---

## Observability & Tier Health

The `ObservabilityCollector` in `observability.py` provides thread-safe metrics collection for the N-tier LLM cascade:

### Metrics tracked

| Metric | Description |
|--------|-------------|
| Requests | Total requests per model (success + failure) |
| Cascades | How often each model triggered a cascade to the next tier |
| Latency | Per-request latency with avg and p95 stats (sliding window of 100 values) |
| Failures | Timestamped failure events per model (5-minute sliding window) |

### Health classification

Each Groq tier is classified based on recent failures within a 5-minute window:

| Status | Condition | Dashboard color |
|--------|-----------|-----------------|
| `healthy` | 0 failures | Green |
| `degraded` | 1–3 failures | Yellow |
| `down` | 4+ failures | Red |
| `disabled` | Manually disabled via admin API | Grey |

### Admin endpoints

- `GET /v1/admin/tier-health` — returns per-tier health, latency stats, and a summary
- `POST /v1/admin/tier-health/{model}/toggle` — enable/disable a tier (superuser only)

### Dashboard integration

The LLM Observability tab in the Dash admin panel shows:

- **Health cards** — color-coded status per tier with cascade count and latency
- **Budget cards** — TPM/RPM usage per active model
- **Cascade log** — recent cascade events with timestamps

---

## API Versioning

All API routes are mounted under the `/v1/` prefix. WebSocket (`/ws/chat`) and static file mounts (`/avatars/`) remain at root.

```
/v1/chat           POST    Chat endpoint
/v1/chat/stream    POST    NDJSON streaming endpoint
/v1/agents         GET     List agents
/v1/health         GET     Health check
/v1/auth/*                 Auth endpoints (login, register, etc.)
/v1/admin/*                Admin endpoints (superuser only)
/ws/chat                   WebSocket streaming (not versioned)
/avatars/*                 Static avatar files (not versioned)
```
