---
paths:
  - "backend/agents/**"
  - "backend/llm_fallback.py"
  - "backend/token_budget.py"
  - "backend/ws.py"
  - "backend/tools/agent_tool.py"
  - "backend/tools/sentiment_agent.py"
---

# Chat agent rules (auto-loads when touching chat-agent code)

> Lazy-loaded via `paths:` frontmatter. Mirrors what was CLAUDE.md §5.2.
> Symbols span `backend/agents/`, `llm_fallback.py`, `token_budget.py`, `ws.py`.
> Deep detail → Serena memories `summary-based-context`, `llm-truncation-hallucination`,
> `byom-cascade-override`, `groq-chunking-strategy`, `llm-cascade-profiles`,
> `agent-init-pattern`, `streaming-protocol`, `iceberg-freshness-checks`.

- **Cascade routing**: chat = BYO + platform fallback; batch (recommendations/sentiment/forecast) = platform-only; superusers always platform. → `byom-cascade-override`
- **Sentiment**: `finbert` → ProsusAI (CPU, free) for batch; LLM cascade only for chat or FinBERT failure.
- **Tool-result truncation**: `max_tool_result_chars=4000` (pass 1), 2500 (2), 1500 (3). Synthesis prompt must include "NO HALLUCINATION ON TRUNCATION". → `llm-truncation-hallucination`
- **Hallucination guardrail**: `_is_hallucinated()` rejects 3+ stock patterns with zero `tool_done` events.
- **Groq tool-call ID sanitization** before Anthropic fallback (`_sanitize_tool_ids()`).
- **Per-request model pinning**: `_pinned_model` locks after first invoke. Budget exhaust → compress, unpin, cascade. `pin_reset()` per ReAct loop.
- **TokenBudget**: atomic `reserve()`/`release()` (NOT `can_afford()`/`record()` — TOCTOU). Singleton via `get_token_budget()`; Iceberg seed on restart.
- **`bind_tools` rebuild lookup**: `_model_lookup` must rebuild after `FallbackLLM.bind_tools()`.
- **`max_retries=0` on `ChatGroq`** — SDK retries caused 45-56s pre-cascade delays.
- **Iteration counter** MUST flow into `FallbackLLM.invoke(messages, *, iteration=...)` — else progressive compression never engages.
- **Cascade profile at startup**: `tool` (loop) vs `synthesis` (final) vs `test` (no Anthropic). Route to synthesis cascade after first tool iteration. → `groq-chunking-strategy`, `llm-cascade-profiles`
- **New agent class**: subclass `BaseAgent`, override `format_response()`. Attributes used in `_build_llm()` MUST exist BEFORE constructor. → `agent-init-pattern`
- **Sub-agent message construction** — 3 regimes: (1) first = `prompt+query`; (2) same-intent follow-up = `prompt+summary+query`; (3) **intent switch = `prompt+query` only** (no history → no cross-intent contamination). → `summary-based-context`
- **Chat tool fetching new ticker** MUST call `_ensure_stock_master(ticker, info)`. → `stock-master-auto-insert`
- **Chat clarification gate**: `?` ending bypasses keyword gate.
- **Currency-aware prompt**: `_build_context_block()` injects portfolio currency mix.
- **Tool-forcing prompts**: directive ("YOUR FIRST RESPONSE MUST ONLY be a tool call"). → `llm-tool-forcing`
- **WebSocket**: auth-first handshake; events `thinking`/`tool_start`/`tool_done`/`warning`/`final`/`error`/`timeout`; close codes 4001/4002/4003. `_handle_chat` MUST send `error` + `final` events (not just return queue). → `streaming-protocol`
