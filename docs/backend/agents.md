# Agents

The agent framework lives in `backend/agents/`. It provides a base class with the full agentic loop, a registry for lookup and routing, and a concrete implementation backed by Groq.

---

## File Structure

| File | Purpose |
|------|---------|
| `agents/base.py` | `AgentConfig` dataclass + `BaseAgent` ABC (owns the agentic loop) |
| `agents/registry.py` | `AgentRegistry` — maps agent IDs to agent instances |
| `agents/general_agent.py` | `GeneralAgent` concrete class + `create_general_agent` factory |
| `agents/__init__.py` | Empty (marks directory as a Python package) |

---

## AgentConfig

`AgentConfig` is a plain dataclass defined in `agents/base.py`. It carries every piece of configuration an agent needs and is passed to the agent's constructor.

```python
@dataclass
class AgentConfig:
    agent_id: str         # Unique ID for routing, e.g. "general"
    name: str             # Human-readable name, e.g. "General Agent"
    description: str      # One sentence exposed via GET /agents
    model: str            # LLM model identifier, e.g. "openai/gpt-oss-120b"
    temperature: float    # Sampling temperature (default 0.0)
    system_prompt: str    # Optional system message (default "")
    tool_names: list[str] # Tools this agent may call (default [])
```

---

## BaseAgent

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

`GeneralAgent` is defined in `agents/general_agent.py`. It is the only concrete agent currently registered.

```python
class GeneralAgent(BaseAgent):
    def _build_llm(self) -> ChatGroq:
        return ChatGroq(
            model=self.config.model,
            temperature=self.config.temperature
        )
```

That is the entire class. All loop logic is inherited from `BaseAgent`.

### Factory Function — `create_general_agent()`

```python
def create_general_agent(tool_registry: ToolRegistry) -> GeneralAgent:
    config = AgentConfig(
        agent_id="general",
        name="General Agent",
        description="A general-purpose agent that can answer questions and search the web.",
        model="openai/gpt-oss-120b",
        temperature=0.0,
        tool_names=["get_current_time", "search_web"],
    )
    return GeneralAgent(config=config, tool_registry=tool_registry)
```

The factory is the only place where the model name and tool list are defined for this agent. To change either, edit this function.

---

## Switching to Claude Sonnet 4.6

`GeneralAgent` currently uses Groq as a temporary workaround. Switching back to Claude requires three changes in `agents/general_agent.py`:

**1. Change the import:**
```python
# Before
from langchain_groq import ChatGroq

# After
from langchain_anthropic import ChatAnthropic
```

**2. Change `_build_llm()`:**
```python
# Before
return ChatGroq(model=self.config.model, temperature=self.config.temperature)

# After
return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)
```

**3. Change the model name in `create_general_agent()`:**
```python
# Before
model="openai/gpt-oss-120b",

# After
model="claude-sonnet-4-6",
```

**4. Update environment variable:**
```bash
# Before
export GROQ_API_KEY=...

# After
export ANTHROPIC_API_KEY=...
```

No other files need to change.

---

## Extending the Agent Framework

To add a new agent type (e.g. a code-review agent with a different system prompt and different tools):

```python
# agents/code_agent.py
from langchain_groq import ChatGroq
from agents.base import AgentConfig, BaseAgent
from tools.registry import ToolRegistry

class CodeAgent(BaseAgent):
    def _build_llm(self):
        return ChatGroq(model=self.config.model, temperature=self.config.temperature)

def create_code_agent(tool_registry: ToolRegistry) -> CodeAgent:
    config = AgentConfig(
        agent_id="code",
        name="Code Agent",
        description="Specialised agent for code review and debugging.",
        model="openai/gpt-oss-120b",
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
