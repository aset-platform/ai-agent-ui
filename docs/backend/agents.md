# Agents

The agent framework lives in `backend/agents/`. It uses a **LangGraph supervisor graph** that automatically routes user queries to specialised sub-agents via a two-tier intent classifier (keyword match → LLM fallback). Legacy `GeneralAgent` and `StockAgent` classes are retained as fallback but are unused when `use_langgraph=True` (default).

---

## File Structure

| File | Purpose |
|------|---------|
| `agents/graph.py` | `build_supervisor_graph()` — 11-node LangGraph StateGraph |
| `agents/graph_state.py` | `AgentState` TypedDict (messages, intent, user_context, etc.) |
| `agents/sub_agents.py` | `_make_sub_agent_node()` factory + `_build_context_block()` for dynamic context injection |
| `agents/configs/portfolio.py` | Portfolio Agent config (currency-aware, mandatory tool-use) |
| `agents/configs/stock_analyst.py` | Stock Analyst config (pipeline: fetch → analyse → verdict) |
| `agents/configs/forecaster.py` | Forecaster config (Prophet models) |
| `agents/configs/research.py` | Research Agent config (news + sentiment) |
| `agents/configs/sentiment.py` | Sentiment Agent config (3-source headlines, market mood) |
| `agents/nodes/guardrail.py` | Content safety + financial relevance gate |
| `agents/nodes/router_node.py` | Tier 1 keyword-based intent classifier (zero LLM) |
| `agents/nodes/llm_classifier.py` | Tier 2 LLM fallback classifier (1 cheap call) |
| `agents/nodes/supervisor.py` | Intent → sub-agent mapper |
| `agents/nodes/synthesis.py` | Output formatting |
| `agents/nodes/log_query.py` | Audit logging to Iceberg |
| `agents/nodes/decline.py` | Polite refusal for non-financial queries |
| `agents/config.py` | `AgentConfig` dataclass + `MAX_ITERATIONS` constant |
| `agents/registry.py` | `AgentRegistry` — maps agent IDs to agent instances (legacy) |
| `agents/base.py` | `BaseAgent` ABC (legacy fallback) |

---

## AgentConfig

`AgentConfig` is a plain dataclass defined in `agents/config.py`. It carries every piece of configuration an agent needs and is passed to the agent's constructor.

```python
@dataclass
class AgentConfig:
    agent_id: str                    # Unique ID for routing, e.g. "general"
    name: str                        # Human-readable name
    description: str                 # One sentence exposed via GET /agents
    groq_model_tiers: List[str]      # Ordered Groq model names (tried first→last)
    temperature: float               # Sampling temperature (default 0.0)
    system_prompt: str               # Optional system message (default "")
    tool_names: list[str]            # Tools this agent may call (default [])
```

The `groq_model_tiers` list defines the Groq model cascade order. Each model is tried in order; on budget exhaustion or API error, the next tier is attempted. Anthropic Claude Sonnet 4.6 is always the final fallback (hardcoded in `FallbackLLM`).

---

## LangGraph Supervisor Graph

The primary execution path (`use_langgraph=True`, default). Built in `agents/graph.py` via `build_supervisor_graph()`.

### Graph Flow

```
User Message
  → guardrail (content safety + finance relevance)
  → router_node (Tier 1: keyword match, zero LLM)
  → llm_classifier (Tier 2: LLM fallback if router uncertain)
  → supervisor (intent → sub-agent mapper)
  → sub_agent node (portfolio | stock_analyst | forecaster | research | sentiment)
  → log_query (audit to Iceberg)
  → synthesis (output formatting)
  → Response
```

If the guardrail rejects → `decline` node (polite refusal). If router is confident → skips `llm_classifier`.

### AgentState (TypedDict)

Defined in `agents/graph_state.py`:

```python
class AgentState(TypedDict):
    messages: list           # LangChain message history
    intent: str              # Classified intent
    current_agent: str       # Active sub-agent ID
    tool_events: list        # Streaming tool events
    final_response: str      # Output text
    user_context: dict       # Portfolio currency/market context
    error: str | None
    start_time_ns: int
```

The `user_context` field is populated from the user's portfolio holdings at request time, injected into the sub-agent's system prompt via `_build_context_block()` for currency-aware responses.

### Sub-Agent Configs

Each sub-agent is configured via a dataclass in `agents/configs/`:

| Config | Agent | Key Behaviour |
|--------|-------|---------------|
| `portfolio.py` | Portfolio Agent | Mandatory tool-use, currency rules, dynamic context |
| `stock_analyst.py` | Stock Analyst | Pipeline: fetch → analyse → verdict |
| `forecaster.py` | Forecaster | Prophet models, horizon selection |
| `research.py` | Research Agent | News search + analyst recommendations |
| `sentiment.py` | Sentiment Agent | 3-source headlines, market mood, hybrid cached/live UX |

### Dynamic Context Injection

`_build_context_block()` in `agents/sub_agents.py` detects the user's currency/market mix from portfolio holdings and injects it into the LLM system prompt. This ensures the portfolio agent uses correct currency symbols (₹/$) and market conventions.

---

## BaseAgent (Legacy Fallback)

`BaseAgent` is an abstract base class (ABC) defined in `agents/base.py`. It implements everything except the LLM construction — subclasses only need to override `_build_llm()`.

### Initialization

```python
class BaseAgent(ABC):
    def __init__(self, config: AgentConfig, tool_registry: ToolRegistry) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.logger = logging.getLogger(f"agent.{config.agent_id}")
        self._setup()
```

`_setup()` is called automatically and does three things:

1. Calls `_build_llm()` (abstract — implemented by subclass).
2. Calls `tool_registry.get_tools(config.tool_names)` to fetch the agent's permitted tools.
3. Calls `llm.bind_tools(tools)` to bake tool schemas into the LLM, storing the result as `self.llm_with_tools`.

### The Agentic Loop — `run()`

```python
def run(self, user_input: str, history: list[dict] = []) -> str:
```

This is the primary public method. It is called once per HTTP request.

**Step 1 — Build messages:**

`_build_messages(user_input, history)` converts the raw history list from the HTTP request into LangChain message objects and appends the new user input at the end:

```python
# Input history from HTTP:
[{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi!"}]

# Resulting messages list:
[HumanMessage("Hello"), AIMessage("Hi!"), HumanMessage(user_input)]
```

Roles other than `"user"` and `"assistant"` are silently dropped.

**Step 2 — Invoke the LLM:**

```python
response = self.llm_with_tools.invoke(messages)
messages.append(response)  # AIMessage appended to history
```

The response is an `AIMessage` with two relevant attributes:
- `response.content` — the text answer (may be empty if a tool is being called)
- `response.tool_calls` — list of tool call requests (empty list when the LLM is done)

**Step 3 — Execute tool calls:**

If `response.tool_calls` is non-empty, the loop iterates over each call:

```python
for tc in response.tool_calls:
    # tc = {"id": "call_abc", "name": "get_current_time", "args": {}}
    result = self.tool_registry.invoke(tc["name"], tc.get("args", {}))
    messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
```

!!! warning "tool_call_id is mandatory"
    `ToolMessage.tool_call_id` must match the `id` of the corresponding entry in `response.tool_calls`. LangChain uses this association to build the correct message structure for the next LLM call. Mismatched IDs will cause errors or incorrect model behaviour.

**Step 4 — Repeat or exit:**

After all tool results are appended, the loop invokes the LLM again with the expanded message list. It keeps iterating until `response.tool_calls` is empty, then returns `response.content`.

### Iteration Cap — MAX_ITERATIONS

The loop is bounded by `MAX_ITERATIONS = 15` (module-level constant in `base.py`). If the counter exceeds this value the loop logs a `WARNING`, breaks, and returns the last available response. 15 is well above any legitimate tool chain observed in practice.

### Streaming Loop — `stream()`

```python
def stream(self, user_input: str, history: list[dict] = []) -> Iterator[str]:
```

A parallel method to `run()` that yields NDJSON status events instead of returning a single string. Each yielded value is a JSON-encoded object followed by `\n`.

**Event types yielded:**

| Event | Fields | When |
|-------|--------|------|
| `thinking` | `iteration` | Before each LLM invocation |
| `tool_start` | `tool`, `args` | Before each tool call |
| `tool_done` | `tool`, `preview` (≤ 300 chars) | After each tool result |
| `warning` | `message` | On `MAX_ITERATIONS` hit |
| `final` | `response`, `iterations` | Loop complete |
| `error` | `message` | On exception (also re-raised) |

The generator is consumed by `_chat_stream_handler` in `main.py`, which runs it in a daemon thread and passes events through a `queue.Queue` to the `StreamingResponse`.

### Abstract Method — `_build_llm()`

```python
@abstractmethod
def _build_llm(self):
    ...
```

Subclasses must override this to return a LangChain-compatible chat model (any object that supports `.bind_tools()` and `.invoke()`).

---

## AgentRegistry

`AgentRegistry` is defined in `agents/registry.py`. It is a simple dict-backed store that maps `agent_id` strings to `BaseAgent` instances.

### Methods

```python
registry.register(agent: BaseAgent) -> None
```
Adds an agent. If an agent with the same `agent_id` is already registered, it is **silently overwritten**.

```python
registry.get(agent_id: str) -> Optional[BaseAgent]
```
Returns the agent for the given ID, or `None` if not found. Logs a `WARNING` when an ID is missing (so operators can detect misconfigured clients without the registry itself raising).

```python
registry.list_agents() -> list[dict]
```
Returns a serialisable list of `{"id", "name", "description"}` dicts — exactly what `GET /agents` returns.

### Usage in ChatServer

```python
# Registration (at startup)
general = create_general_agent(self.tool_registry)
self.agent_registry.register(general)

# Dispatch (per request)
agent = self.agent_registry.get(req.agent_id)
if agent is None:
    raise HTTPException(status_code=404, detail=f"Agent '{req.agent_id}' not found")
result = agent.run(req.message, req.history)
```

---

## GeneralAgent

`GeneralAgent` is defined in `agents/general_agent.py`. It overrides `_build_llm()` to supply `FallbackLLM` with the N-tier Groq cascade and Anthropic fallback.

```python
class GeneralAgent(BaseAgent):
    def _build_llm(self) -> FallbackLLM:
        return FallbackLLM(
            groq_models=self.config.groq_model_tiers,
            anthropic_model="claude-sonnet-4-6",
            temperature=self.config.temperature,
            agent_id=self.config.agent_id,
            token_budget=self.token_budget,
            compressor=self.compressor,
        )
```

All loop logic is inherited from `BaseAgent`.

### Factory Function — `create_general_agent()`

```python
def create_general_agent(tool_registry, token_budget=None, compressor=None):
    settings = get_settings()
    config = AgentConfig(
        agent_id="general",
        name="General Agent",
        description="A general-purpose agent that can answer questions and search the web.",
        groq_model_tiers=_parse_tiers(settings.groq_model_tiers),
        temperature=0.0,
        tool_names=["get_current_time", "search_web"],
    )
    agent = GeneralAgent(config=config, tool_registry=tool_registry)
    agent.token_budget = token_budget or TokenBudget()
    agent.compressor = compressor or MessageCompressor(...)
    return agent
```

The factory reads the tier order from `settings.groq_model_tiers` (a comma-separated env var) and injects shared `TokenBudget` and `MessageCompressor` instances.

---

## StockAgent

`StockAgent` is defined in `agents/stock_agent.py`. Identical structure to `GeneralAgent` but with a detailed system prompt and 9 stock analysis tools. Uses the same `FallbackLLM` cascade.

---

## N-tier LLM Cascade (FallbackLLM)

Both agents use `FallbackLLM` from `backend/llm_fallback.py`, which cascades through Groq models before falling back to Anthropic:

```
Tier 1: llama-3.3-70b-versatile   (12K TPM, reliable tool-calling)
Tier 2: kimi-k2-instruct          (10K TPM, parallel tools)
Tier 3: gpt-oss-120b              (8K TPM, quality)
Tier 4: llama-4-scout-17b         (30K TPM, fast)
Tier 5: claude-sonnet-4-6         (paid, unlimited — Anthropic fallback)
```

For each tier, `FallbackLLM` checks the token budget, applies progressive compression if needed (targeting 70% of the model's TPM), and cascades on `RateLimitError`, `APIConnectionError`, or `APIStatusError` (413). When `GROQ_API_KEY` is not set, all Groq tiers are skipped and requests go directly to Anthropic.

---

## Extending the Agent Framework

To add a new agent type (e.g. a code-review agent with a different system prompt and different tools):

```python
# agents/code_agent.py
from agents.base import AgentConfig, BaseAgent
from config import get_settings
from llm_fallback import FallbackLLM
from tools.registry import ToolRegistry

class CodeAgent(BaseAgent):
    def _build_llm(self) -> FallbackLLM:
        return FallbackLLM(
            groq_models=self.config.groq_model_tiers,
            anthropic_model="claude-sonnet-4-6",
            temperature=self.config.temperature,
            agent_id=self.config.agent_id,
            token_budget=self.token_budget,
            compressor=self.compressor,
        )

def create_code_agent(tool_registry: ToolRegistry) -> CodeAgent:
    settings = get_settings()
    config = AgentConfig(
        agent_id="code",
        name="Code Agent",
        description="Specialised agent for code review and debugging.",
        groq_model_tiers=_parse_tiers(settings.groq_model_tiers),
        system_prompt="You are an expert software engineer...",
        tool_names=["search_web"],
    )
    return CodeAgent(config=config, tool_registry=tool_registry)
```

Then register it in `ChatServer._register_agents()`:

```python
from agents.code_agent import create_code_agent

def _register_agents(self) -> None:
    self.agent_registry.register(create_general_agent(self.tool_registry))
    self.agent_registry.register(create_code_agent(self.tool_registry))
```

Clients can now target it with `"agent_id": "code"`.
