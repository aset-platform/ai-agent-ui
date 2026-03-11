# Architecture Design: Groq Rate-Limit Chunking Strategy

> Status: DRAFT | Author: Claude | Date: 2026-03-09

## 1. Problem Statement

The Groq free tier imposes strict per-model rate limits:

| Model | RPM | TPM | TPD |
|-------|-----|-----|-----|
| openai/gpt-oss-120b (current) | 30 | 8K | 200K |
| llama-4-scout-17b-16e-instruct | 30 | 30K | 500K |

The agentic loop (`agents/loop.py`, `agents/stream.py`) makes **multiple
LLM calls per user request** (up to 15 iterations). Each iteration sends
the full message list — system prompt + history + all prior tool
calls/results — which grows unboundedly. The StockAgent's 6-tool pipeline
routinely exceeds 8K TPM, triggering 429 errors and falling back to
Anthropic (paid).

## 2. Design Goals

1. **Maximize Groq free tier utilization** — stay under TPM/RPM/TPD limits
2. **Minimize Anthropic fallback** — Anthropic is last resort, not default
3. **Zero user-visible degradation** — compression is invisible to the user
4. **Backward compatible** — without `GROQ_API_KEY`, behavior is unchanged

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  BaseAgent                          │
│  _build_messages() → messages[]                     │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│              Agentic Loop (loop.py / stream.py)     │
│                                                     │
│  for each iteration:                                │
│    ┌─────────────────────────────────────────┐      │
│    │  MessageCompressor.compress(             │      │
│    │    messages, iteration, is_final=False   │      │
│    │  )                                       │      │
│    └──────────────┬──────────────────────────┘      │
│                   ▼                                  │
│    ┌─────────────────────────────────────────┐      │
│    │  FallbackLLM.invoke(                     │      │
│    │    compressed_messages,                   │      │
│    │    iteration=N, is_final=False            │      │
│    │  )                                       │      │
│    │  ┌───────────────────────────────────┐   │      │
│    │  │  TokenBudget.request(model, est)  │   │      │
│    │  │  → route decision                 │   │      │
│    │  │    ├─ router_model (budget OK)     │   │      │
│    │  │    ├─ responder_model (router OOM) │   │      │
│    │  │    └─ anthropic (all exhausted)    │   │      │
│    │  └───────────────────────────────────┘   │      │
│    └──────────────┬──────────────────────────┘      │
│                   ▼                                  │
│    Tool execution → ToolMessage appended             │
│    (tool results summarized before append)           │
└─────────────────────────────────────────────────────┘
```

### Three Layers

| Layer | Module | Responsibility |
|-------|--------|----------------|
| **Token Budget** | `backend/token_budget.py` | Sliding-window TPM/RPM/TPD tracking per model |
| **Message Compressor** | `backend/message_compressor.py` | History truncation, tool result summarization, system prompt tiering |
| **Tiered Router** | `backend/llm_fallback.py` (rewrite) | Model selection based on iteration type + budget state |

## 4. Component Design

### 4.1 TokenBudget (`backend/token_budget.py`)

**Purpose**: Track token and request consumption per model with sliding
windows. Provide a `can_afford(model, tokens)` check and `record(model,
tokens)` method.

```python
@dataclass
class ModelLimits:
    """Rate limits for a single Groq model."""
    rpm: int          # requests per minute
    tpm: int          # tokens per minute
    rpd: int          # requests per day
    tpd: int          # tokens per day

class TokenBudget:
    """Sliding-window rate tracker for multiple Groq models.

    Uses collections.deque of (timestamp, count) tuples.
    Thread-safe via threading.Lock (agentic loop runs
    in a thread per request).
    """

    # Class-level defaults loaded from GROQ_MODEL_LIMITS or hardcoded
    DEFAULT_LIMITS: Dict[str, ModelLimits] = { ... }

    def __init__(self, limits: Dict[str, ModelLimits] | None = None):
        ...

    def estimate_tokens(self, messages: List[BaseMessage]) -> int:
        """Fast token estimate: sum(len(msg.content)) // 4.

        Adds a 20% safety margin. No external dependencies.
        """

    def can_afford(self, model: str, estimated_tokens: int) -> bool:
        """Check if model has budget for estimated_tokens.

        Uses 80% threshold (not 100%) to leave headroom.
        Returns False if ANY limit would be breached.
        """

    def record(self, model: str, tokens_used: int) -> None:
        """Record a completed request's token usage."""

    def best_available_model(
        self,
        estimated_tokens: int,
        prefer: str,
        fallbacks: List[str],
    ) -> str | None:
        """Return the first model from [prefer] + fallbacks
        that can afford the tokens. None if all exhausted."""

    def get_status(self) -> Dict[str, Dict[str, float]]:
        """Return utilization percentages for logging."""
```

**Sliding window implementation**:
```python
# Per-model, per-window tracking
_minute_log: Dict[str, deque]  # deque of (timestamp, token_count)
_day_log: Dict[str, deque]

def _window_total(self, log: deque, window_seconds: int) -> int:
    """Sum tokens in the sliding window, pruning expired entries."""
    cutoff = time.monotonic() - window_seconds
    while log and log[0][0] < cutoff:
        log.popleft()
    return sum(count for _, count in log)
```

**Thread safety**: `threading.Lock` per model (one lock per model entry).
The agentic loop runs one thread per HTTP request, so contention is low.

**Default model limits** (hardcoded, overridable via constructor):
```python
DEFAULT_LIMITS = {
    "meta-llama/llama-4-scout-17b-16e-instruct": ModelLimits(
        rpm=30, tpm=30_000, rpd=1_000, tpd=500_000,
    ),
    "openai/gpt-oss-120b": ModelLimits(
        rpm=30, tpm=8_000, rpd=1_000, tpd=200_000,
    ),
}
```

---

### 4.2 MessageCompressor (`backend/message_compressor.py`)

**Purpose**: Reduce token count of the message list before each LLM call
through three techniques applied in order.

```python
class MessageCompressor:
    """Compress LangChain message lists to fit within token budgets.

    Three compression stages applied in order:
    1. System prompt tiering
    2. History truncation
    3. Tool result summarization
    """

    def __init__(
        self,
        max_history_turns: int = 3,
        max_tool_result_chars: int = 2000,
        condensed_prompt_ratio: float = 0.4,
    ):
        ...

    def compress(
        self,
        messages: List[BaseMessage],
        iteration: int,
        target_tokens: int | None = None,
    ) -> List[BaseMessage]:
        """Return a compressed copy of messages.

        Does NOT mutate the input list. Returns a new list.

        Args:
            messages: Full message list from the agentic loop.
            iteration: Current loop iteration (1-based).
            target_tokens: Optional target token count. If set,
                applies progressively aggressive compression
                until under target.
        """
```

#### Stage 1: System Prompt Tiering

```
Iteration 1:  Full system prompt (as-is)
Iteration 2+: Condensed prompt — strip examples, keep core rules only
```

Implementation: The compressor receives the full `SystemMessage` and
produces a condensed version by extracting lines matching key patterns
(PIPELINE, RULES, numbered steps) and dropping verbose explanations.

```python
def _condense_system_prompt(
    self, msg: SystemMessage
) -> SystemMessage:
    """Extract core instructions, drop examples/elaboration.

    Strategy: keep lines starting with digits, dashes,
    or ALL-CAPS keywords. Drop paragraphs > 2 sentences.
    Target: ~40% of original length.
    """
```

#### Stage 2: History Truncation

For intermediate iterations (not the first call), keep only the last
`max_history_turns` user/assistant exchanges from the conversation
history portion of the message list.

```
Messages structure:
  [SystemMessage, ...history..., HumanMessage(current), ...loop messages...]
                  ^^^^^^^^^^^
                  Truncate this section

Keep: SystemMessage + last N history turns + current HumanMessage +
      all loop messages (AIMessage + ToolMessage pairs from this request)
```

```python
def _truncate_history(
    self, messages: List[BaseMessage], max_turns: int
) -> List[BaseMessage]:
    """Keep system + last N history turns + current request messages.

    A 'turn' is a (HumanMessage, AIMessage) pair from the
    pre-loop history. Loop-generated messages (tool calls,
    tool results) are always preserved.
    """
```

#### Stage 3: Tool Result Summarization

Tool results (especially from stock tools) can be thousands of characters.
Truncate to `max_tool_result_chars` with a trailing `... [truncated]`
marker.

```python
def _summarize_tool_results(
    self, messages: List[BaseMessage], max_chars: int
) -> List[BaseMessage]:
    """Truncate ToolMessage.content to max_chars.

    Preserves the first max_chars characters (which typically
    contain the most important summary data) and appends
    '... [truncated N chars]'.
    """
```

#### Progressive Compression

When `target_tokens` is set, apply stages with increasing aggression:

```
Pass 1: Stage 1 + Stage 3 (2000 chars)
  → if still over target:
Pass 2: + Stage 2 (3 turns)
  → if still over target:
Pass 3: Stage 2 (1 turn) + Stage 3 (1000 chars)
  → if still over target:
Pass 4: Stage 2 (0 history turns) + Stage 3 (500 chars)
```

---

### 4.3 FallbackLLM Rewrite (`backend/llm_fallback.py`)

**Current**: Binary Groq → Anthropic fallback on `RateLimitError`.

**New**: Three-tier routing with proactive budget checks.

```python
class FallbackLLM:
    """Three-tier LLM router: router → responder → anthropic.

    Tier 1 (Router):  High-TPM model for tool-calling iterations
    Tier 2 (Responder): Best model for final synthesis
    Tier 3 (Fallback): Anthropic Claude (paid, no rate limits)

    The router/responder split is invisible to the caller — the
    same bind_tools / invoke interface is preserved.
    """

    def __init__(
        self,
        router_model: str,
        responder_model: str,
        anthropic_model: str,
        temperature: float,
        agent_id: str,
        token_budget: TokenBudget,
        compressor: MessageCompressor,
    ):
        # Build three inner LLMs
        self._router_llm = ChatGroq(model=router_model, ...)
        self._responder_llm = ChatGroq(model=responder_model, ...)
        self._anthropic_llm = ChatAnthropic(model=anthropic_model, ...)

        # Bound versions (after bind_tools)
        self._router_bound = self._router_llm
        self._responder_bound = self._responder_llm
        self._anthropic_bound = self._anthropic_llm

        self._budget = token_budget
        self._compressor = compressor

    def bind_tools(self, tools, **kwargs) -> "FallbackLLM":
        """Bind tools to ALL three inner LLMs."""
        self._router_bound = self._router_llm.bind_tools(tools, **kwargs)
        self._responder_bound = self._responder_llm.bind_tools(tools, **kwargs)
        self._anthropic_bound = self._anthropic_llm.bind_tools(tools, **kwargs)
        return self

    def invoke(
        self,
        messages: List[Any],
        *,
        iteration: int = 1,
        is_final: bool = False,
        **kwargs,
    ) -> Any:
        """Route to the best available model.

        Decision flow:
        1. Compress messages via MessageCompressor
        2. Estimate tokens
        3. If is_final → prefer responder, then router, then anthropic
        4. If not is_final → prefer router, then responder, then anthropic
        5. On RateLimitError from chosen model → cascade to next tier
        """
```

**Routing decision matrix**:

```
┌─────────────┬────────────────────┬───────────────────┐
│ Condition   │ Tool-calling iter  │ Final synthesis    │
├─────────────┼────────────────────┼───────────────────┤
│ Router OK   │ → router           │ → responder        │
│ Router OOM  │ → responder        │ → responder        │
│ Both OOM    │ → anthropic        │ → anthropic        │
│ 429 caught  │ cascade to next    │ cascade to next    │
└─────────────┴────────────────────┴───────────────────┘
```

**invoke() pseudocode**:
```python
def invoke(self, messages, *, iteration=1, is_final=False, **kwargs):
    # Step 1: Compress
    compressed = self._compressor.compress(
        messages, iteration,
        target_tokens=self._get_target(is_final),
    )

    # Step 2: Estimate
    est = self._budget.estimate_tokens(compressed)

    # Step 3: Build priority list
    if is_final:
        priority = [
            (self._responder_model, self._responder_bound),
            (self._router_model, self._router_bound),
        ]
    else:
        priority = [
            (self._router_model, self._router_bound),
            (self._responder_model, self._responder_bound),
        ]

    # Step 4: Try each Groq model
    for model_name, bound_llm in priority:
        if not self._budget.can_afford(model_name, est):
            _logger.info(
                "Skip %s: budget exhausted (est=%d)", model_name, est
            )
            continue
        try:
            result = bound_llm.invoke(compressed, **kwargs)
            self._budget.record(model_name, est)
            _logger.info(
                "Route → %s | iter=%d final=%s tokens≈%d",
                model_name, iteration, is_final, est,
            )
            return result
        except (RateLimitError, APIConnectionError) as exc:
            _logger.warning(
                "Groq %s failed (%s), trying next tier",
                model_name, exc,
            )
            continue

    # Step 5: Anthropic fallback
    _logger.warning(
        "All Groq models exhausted → Anthropic | iter=%d", iteration
    )
    return self._anthropic_bound.invoke(compressed, **kwargs)
```

---

### 4.4 Agentic Loop Changes

Both `loop.py` and `stream.py` need two small changes:

1. **Pass `iteration` and `is_final` to `invoke()`**
2. **Summarize tool results before appending to messages**

#### loop.py changes (diff sketch):

```python
# BEFORE:
response = agent.llm_with_tools.invoke(messages)

# AFTER:
response = agent.llm_with_tools.invoke(
    messages, iteration=iteration, is_final=False,
)

# After the loop ends (no tool_calls), the response was already
# generated. No second call needed — the is_final=True call
# happens naturally when the LLM returns no tool_calls.
```

Wait — `is_final` can't be known *before* the call (we don't know if
the LLM will return tool calls). Better approach:

**Revised**: Don't use `is_final`. Instead, use iteration number as a
heuristic:
- **iteration == 1**: likely the first tool-routing call → router model
- **iteration > 1 and < MAX**: still tool-routing → router model
- **Any iteration where LLM returns no tool_calls**: this was the final
  call, but we can't predict it

**Alternative**: Use a two-pass approach for the final response:
After the loop detects no tool_calls, if the response came from the
router model, optionally re-invoke with the responder model using the
full context. This guarantees the final answer uses the best model.

**Decision**: Use the **re-synthesis approach** — it's cleaner:

```python
# In loop.py / stream.py:
while True:
    iteration += 1
    response = agent.llm_with_tools.invoke(
        messages, iteration=iteration,
    )
    messages.append(response)

    if not response.tool_calls:
        # If this came from the router model, re-synthesize
        # with the responder model for better quality
        if hasattr(agent.llm_with_tools, 'maybe_resynthesize'):
            better = agent.llm_with_tools.maybe_resynthesize(
                messages, iteration=iteration,
            )
            if better is not None:
                response = better
        break
    # ... tool execution ...
```

**Actually, this adds complexity.** Simpler approach: the FallbackLLM
internally detects "this looks like a final call" if the response has
no tool_calls. But it can't know before invoking.

**Final decision**: Keep it simple. The FallbackLLM picks the model
based solely on budget. The router model (scout-17b) is capable enough
for final synthesis too. The responder model (gpt-oss-120b) is preferred
when budget allows. The `invoke()` method receives `iteration` only:

- **iteration 1-2**: prefer router (save responder budget for later)
- **iteration 3+**: prefer router (responder budget is precious)
- FallbackLLM can optionally attempt responder for the *first* call if
  the conversation is short (low token estimate)

This keeps the loop changes minimal.

#### Tool result summarization in the loop:

```python
# In loop.py / stream.py, after tool execution:
result = agent.tool_registry.invoke(tool_name, tool_args)

# Summarize before adding to message list
if len(result) > 2000:
    result = result[:2000] + f"\n... [truncated {len(result) - 2000} chars]"

messages.append(
    ToolMessage(content=result, tool_call_id=tc["id"])
)
```

This is simpler than doing it in the compressor and prevents the message
list from growing unboundedly regardless of compression.

---

### 4.5 Config Changes (`backend/config.py`)

```python
class Settings(BaseSettings):
    # ... existing fields ...

    # Groq model routing
    groq_router_model: str = (
        "meta-llama/llama-4-scout-17b-16e-instruct"
    )
    groq_responder_model: str = "openai/gpt-oss-120b"

    # Compression settings
    max_history_turns: int = 3
    max_tool_result_chars: int = 2000
```

### 4.6 AgentConfig Changes

Add `router_model` field alongside existing `model` (which becomes the
responder model):

```python
@dataclass
class AgentConfig:
    # ... existing fields ...
    router_model: str = ""  # empty = use model for everything
```

Both `create_general_agent()` and `create_stock_agent()` set
`router_model` from settings.

---

## 5. Data Flow: Complete Request Lifecycle

```
User sends "Analyse AAPL"
│
├─ BaseAgent._build_messages()
│  → [SystemMessage, ...5 history turns..., HumanMessage("Analyse AAPL")]
│  → ~2000 tokens estimated
│
├─ Iteration 1: FallbackLLM.invoke(messages, iteration=1)
│  ├─ Compressor: full system prompt, full history (iteration 1)
│  ├─ Budget check: scout-17b has 30K TPM, est=2000 → OK
│  ├─ Route → scout-17b (router)
│  ├─ Response: tool_call(fetch_stock_data, {ticker: "AAPL"})
│  ├─ Tool executes → 5000 char result
│  ├─ Truncate to 2000 chars before appending ToolMessage
│  └─ Budget records: scout-17b used ~2500 tokens
│
├─ Iteration 2: FallbackLLM.invoke(messages, iteration=2)
│  ├─ Compressor: condensed system prompt, last 3 history turns
│  ├─ Budget check: scout-17b at 2500/30000 TPM → OK
│  ├─ Route → scout-17b
│  ├─ Response: tool_call(analyse_stock_price, {ticker: "AAPL"})
│  ├─ Tool executes → 8000 char result → truncated to 2000
│  └─ Budget records: scout-17b used ~5000 total
│
├─ ... iterations 3-5 (more tools) ...
│  Each uses scout-17b, budget grows but stays under 30K TPM
│
├─ Iteration 6: FallbackLLM.invoke(messages, iteration=6)
│  ├─ Compressor: condensed prompt, 3 history turns, truncated tools
│  ├─ Budget check: scout-17b at ~18K/30K TPM → OK
│  ├─ Route → scout-17b
│  ├─ Response: no tool_calls → final synthesis
│  └─ Budget records: scout-17b used ~22K total
│
└─ Return final response to user
   Total Groq cost: $0.00
   Anthropic calls: 0
```

**Worst case** (scout-17b exhausted mid-conversation):
```
Iteration 4: scout-17b at 29K/30K → can't afford 3K estimate
  → Try gpt-oss-120b: at 0/8K → OK, route there
  → Budget records: gpt-oss-120b used 3K

Iteration 5: scout-17b still over → gpt-oss-120b at 3K/8K → OK
  → Route to gpt-oss-120b

Iteration 6: gpt-oss-120b at 7K/8K → can't afford 4K
  → Anthropic fallback (single call, not entire conversation)
```

## 6. File Inventory

| File | Action | Description |
|------|--------|-------------|
| `backend/token_budget.py` | **NEW** | Sliding-window rate tracker |
| `backend/message_compressor.py` | **NEW** | Message list compression |
| `backend/llm_fallback.py` | **REWRITE** | Three-tier model routing |
| `backend/config.py` | **EDIT** | Add router/responder/compression settings |
| `backend/agents/config.py` | **EDIT** | Add `router_model` field |
| `backend/agents/general_agent.py` | **EDIT** | Wire router_model + shared budget |
| `backend/agents/stock_agent.py` | **EDIT** | Wire router_model + shared budget |
| `backend/agents/loop.py` | **EDIT** | Pass iteration, truncate tool results |
| `backend/agents/stream.py` | **EDIT** | Pass iteration, truncate tool results |
| `backend/main.py` | **EDIT** | Create shared TokenBudget + Compressor |

## 7. Dependency Analysis

**New dependencies**: NONE. Uses only:
- `collections.deque` (stdlib)
- `threading.Lock` (stdlib)
- `time.monotonic()` (stdlib)
- `dataclasses` (stdlib)

**Token estimation**: `len(content) // 4` with 20% margin. No `tiktoken`
needed — accuracy within 15% is sufficient for budget decisions.

## 8. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Token estimate too low → 429 anyway | Medium | 20% safety margin + catch-and-cascade |
| Router model quality insufficient | Low | scout-17b is capable; responder used when budget allows |
| Thread contention on budget lock | Low | One lock per model, short critical section |
| Message compression loses critical context | Medium | Only compress history + tool results, never current request |
| Budget state lost on restart | Low | Acceptable — limits reset within 1 minute anyway |

## 9. Testing Strategy

| Test | Type | Description |
|------|------|-------------|
| `test_token_budget.py` | Unit | Window expiry, can_afford thresholds, thread safety |
| `test_message_compressor.py` | Unit | Each compression stage independently |
| `test_fallback_llm_routing.py` | Unit | Routing decisions based on mock budget state |
| `test_loop_integration.py` | Integration | Full loop with mocked LLMs verifying iteration/routing |
| Manual | E2E | Stock analysis request with Groq API, verify zero Anthropic calls |
