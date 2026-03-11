# Tools

The tool framework lives in `backend/tools/`. It provides a registry that decouples tool storage from agent code, plus concrete tool modules for general use and stock analysis.

---

## File Structure

| File | Purpose |
|------|---------|
| `tools/registry.py` | `ToolRegistry` — maps tool names to `BaseTool` instances |
| `tools/time_tool.py` | `get_current_time` — returns current system datetime |
| `tools/search_tool.py` | `search_web` — queries SerpAPI for live search results |
| `tools/agent_tool.py` | `create_search_market_news_tool` — wraps `GeneralAgent` as a `@tool` |
| `tools/stock_data_tool.py` | 6 stock data tools (delta fetch, parquet, registry) |
| `tools/price_analysis_tool.py` | `analyse_stock_price` — technical indicators + chart + same-day cache |
| `tools/forecasting_tool.py` | `forecast_stock` — Prophet forecast + chart + same-day cache |
| `tools/__init__.py` | Empty (marks directory as a Python package) |
| `validation.py` | Shared input validators: `validate_ticker`, `validate_search_query`, `validate_ticker_batch` |

---

## ToolRegistry

`ToolRegistry` is a dict-backed service locator defined in `tools/registry.py`. Agents never import tool modules directly — they request tools from the registry by name. This means:

- Tools can be registered or swapped at startup without changing agent code.
- The same tool instance can be shared across multiple agents.
- Unknown tool names return a graceful error string instead of raising.

### Methods

```python
registry.register(tool: BaseTool) -> None
```
Adds a tool. Uses `tool.name` as the key. Duplicate names silently overwrite.

```python
registry.get(name: str) -> Optional[BaseTool]
```
Returns the `BaseTool` for the given name, or `None` if not found.

```python
registry.get_tools(names: list[str]) -> list[BaseTool]
```
Returns a list of tools matching the given names, in the same order. Names not found are silently skipped. Used by `BaseAgent._setup()` to fetch the tools it will bind to the LLM.

```python
registry.invoke(name: str, args: dict) -> str
```
Executes a registered tool and returns its output as a string. This is the path used by `BaseAgent.run()` during the agentic loop.

If the tool is not found, returns `"Unknown tool: <name>"` instead of raising — so the LLM receives a meaningful `ToolMessage` and can recover gracefully.

```python
registry.list_names() -> list[str]
```
Returns all registered tool names in insertion order. Used for startup logging.

### invoke() vs direct tool calls

| Aspect | `registry.invoke()` | Direct `tool.invoke()` |
|--------|---------------------|----------------------|
| Error if not found | Returns error string | `AttributeError` / `KeyError` |
| Logging | Debug logs before + after | None |
| LLM recovery | Graceful (gets a ToolMessage) | Loop raises, 500 returned |
| Coupling | Loose (name-based) | Tight (must import tool module) |

---

## get_current_time

Defined in `tools/time_tool.py`.

```python
@tool
def get_current_time() -> str:
    """Return the current local date and time as an ISO-format string."""
    return str(datetime.datetime.now())
```

The `@tool` decorator from `langchain.tools` wraps the function in a `StructuredTool`, extracting the name (`"get_current_time"`) and docstring (used as the tool description the LLM sees when deciding whether to call it).

**Example output:**
```
2026-02-22 14:37:55.993716
```

**Takes no arguments.** The LLM should never pass arguments to this tool, and it has no try/except — unexpected arguments would raise a `TypeError` that propagates up to `ChatServer._chat_handler()` and becomes a `500`.

---

## search_web

Defined in `tools/search_tool.py`.

```python
@tool
def search_web(query: str) -> str:
    """Search the web for up-to-date information on the given query."""
    try:
        search = SerpAPIWrapper()
        return search.run(query)
    except Exception as e:
        return f"Search failed: {e}"
```

`SerpAPIWrapper` from `langchain-community` reads the `SERPAPI_API_KEY` environment variable automatically. The search is delegated to SerpAPI's Google Search endpoint and returns an organic-result summary string.

**Arguments:**

| Argument | Type | Description |
|----------|------|-------------|
| `query` | `str` | A natural-language or keyword search query |

**Return value:** A summary string of top search results, or `"Search failed: <reason>"` on any exception (missing API key, network error, quota exceeded, etc.).

**Why the try/except matters:** Without it, a failed SerpAPI call would raise inside the agentic loop. The `BaseAgent.run()` would re-raise and the HTTP handler would catch it and return a `500`. With the try/except, the error becomes a `ToolMessage` string, the LLM receives it, and can respond gracefully (e.g. "I wasn't able to search the web, but I can tell you that...").

**Requires:** `SERPAPI_API_KEY` set in the environment. Get a key at [serpapi.com](https://serpapi.com) — the free tier allows 100 searches/month.

---

## search_market_news

Defined in `tools/agent_tool.py` via the `create_search_market_news_tool(general_agent)` factory.

This tool lets the stock agent delegate web searches to the general agent, which already has `search_web` bound to its LLM. The factory creates a `@tool`-decorated function that calls `general_agent.run(query, history=[])` and returns the result string.

```python
@tool
def search_market_news(query: str) -> str:
    """Search the web for recent news, earnings, analyst reports, or macro
    developments relevant to a stock or market topic."""
    try:
        return general_agent.run(user_input=query, history=[])
    except Exception as exc:
        return f"News search failed: {exc}"
```

**Why a factory?** The tool needs a reference to the general agent, which is only available after `create_general_agent()` has been called. A factory function receives the agent as an argument and captures it in the closure, making the dependency explicit and testable.

**Registration order** (enforced in `ChatServer._register_agents()`):

```python
general = create_general_agent(self.tool_registry)
self.agent_registry.register(general)

news_tool = create_search_market_news_tool(general)   # ← depends on general
self.tool_registry.register(news_tool)

stock = create_stock_agent(self.tool_registry)         # ← depends on news_tool
self.agent_registry.register(stock)
```

**Arguments:**

| Argument | Type | Description |
|----------|------|-------------|
| `query` | `str` | Search query, e.g. `"AAPL earnings Q1 2026 analyst outlook"` |

**Return value:** The general agent's synthesised answer string (which itself called `search_web` internally), or `"News search failed: <reason>"` on exception.

---

## Same-day cache (analyse_stock_price and forecast_stock)

Both analysis tools cache their text output to avoid re-running expensive pipelines (30–90 s) when the same ticker is requested more than once in a day.

**Cache location:** `~/.ai-agent-ui/data/cache/`

**Key format:**

| Tool | File |
|------|------|
| `analyse_stock_price(ticker)` | `~/.ai-agent-ui/data/cache/{TICKER}_analysis_{YYYY-MM-DD}.txt` |
| `forecast_stock(ticker, months)` | `~/.ai-agent-ui/data/cache/{TICKER}_forecast_{N}m_{YYYY-MM-DD}.txt` |

**Logic (identical in both tools):**

```python
cached = _load_cache(ticker, "analysis")
if cached:
    logger.info("Returning cached analysis for %s", ticker)
    return cached

# ... full pipeline ...

_save_cache(ticker, "analysis", report)
return report
```

The cache key includes today's date, so files from previous days are silently ignored and the full pipeline runs again — no manual cache invalidation required. If a ticker is re-analysed on the same day, the cached file is returned in milliseconds.

---

## How Tools Are Bound to the LLM

Tool binding happens once at agent startup, not per-request:

```python
# In BaseAgent._setup():
tools = self.tool_registry.get_tools(self.config.tool_names)
# → [<StructuredTool get_current_time>, <StructuredTool search_web>]

self.llm_with_tools = self.llm.bind_tools(tools)
# → LangChain sends the tool schemas (name, description, parameters)
#   to the LLM so it knows what tools are available
```

At inference time, the LLM decides whether to respond directly or request a tool call based on the tool descriptions in its context. The docstring of each `@tool` function is the description the LLM reads.

---

## Adding a New Tool

1. Create `backend/tools/my_tool.py`:

```python
from langchain.tools import tool

@tool
def my_tool(input: str) -> str:
    """One sentence that tells the LLM when to use this tool."""
    # implementation
    return result
```

2. Import it in `backend/main.py`:

```python
from tools.my_tool import my_tool
```

3. Register it in `ChatServer._register_tools()`:

```python
def _register_tools(self) -> None:
    self.tool_registry.register(get_current_time)
    self.tool_registry.register(search_web)
    self.tool_registry.register(my_tool)          # ← add this
```

4. Add the tool name to the relevant agent's `tool_names` list in its factory function:

```python
config = AgentConfig(
    ...
    tool_names=["get_current_time", "search_web", "my_tool"],
)
```

The tool is now available for that agent to call.
