# Round-Robin Model Switching in ReAct Agent Loops — ASETPLTFRM-305

**Date:** 2026-04-16
**Depth:** Deep (codebase trace + architecture analysis)
**Context:** Portfolio comparison chat loses week-on-week data during multi-iteration tool-calling

---

## Executive Summary

**Root cause confirmed: Round-robin counter increments on EVERY `FallbackLLM.invoke()` call, including within a single ReAct iteration loop.** A user query that takes 3 tool-calling iterations + 1 synthesis pass uses up to 4 different models. The pool counter is a **global singleton** shared across all agents and requests, so even concurrent requests affect each other's model selection.

With `tool_pool_primary` now having only 2 models (`llama-70b`, `qwen3-32b`), iterations alternate between them: iter 1 → llama, iter 2 → qwen, iter 3 → llama. Each model has different strengths at interpreting tool results, causing inconsistent synthesis quality.

**Confidence: Very High** — traced through exact line numbers in 6 source files.

---

## 1. The Problem: Model Switching Within a Single User Request

### Trace: "What is portfolio health from last week to this week?"

```
┌─ User Query ────────────────────────────────────────┐
│                                                      │
│  iter=1: FallbackLLM.invoke()                       │
│    → pool.ordered_models() → counter 0→1            │
│    → SELECT: llama-3.3-70b-versatile                │
│    → Decision: call get_portfolio_comparison          │
│                                                      │
│  iter=2: FallbackLLM.invoke()  [SAME instance]     │
│    → pool.ordered_models() → counter 1→2            │
│    → SELECT: qwen/qwen3-32b  ← DIFFERENT MODEL     │
│    → Processes tool result, decides no more tools    │
│                                                      │
│  synthesis: NEW FallbackLLM instance                │
│    → synthesis pool counter 0→1                      │
│    → SELECT: openai/gpt-oss-120b                    │
│    → Reformats response ← THIRD MODEL              │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**3 different models touched the same conversation in one request.**

### Why This Causes Data Loss

- **Model A** (llama-70b) decides to call `get_portfolio_comparison` with certain params
- **Model B** (qwen3-32b) receives tool results but interprets them through a different "lens" — different tokenization, different attention patterns, different format preferences
- **Model C** (gpt-oss-120b) synthesizes the final response but may collapse structured data (tables, comparisons) into flat prose

The Jira ticket logs confirm this: gpt-oss-120b synthesis collapsed the Period 1 vs Period 2 table into a single-period summary.

---

## 2. Architecture Trace

### Global Singleton Pool (Shared Across ALL Agents)

```
token_budget.py:744 → ONE TokenBudget per process
  ├── _pools["tool:0"] = RoundRobinPool(["llama-70b", "qwen3-32b"])
  │     └── _counter: 0, 1, 2, 3... (increments globally)
  ├── _pools["tool:1"] = RoundRobinPool(["gpt-oss-120b", "gpt-oss-20b"])
  ├── _pools["tool:2"] = RoundRobinPool(["scout-17b"])
  ├── _pools["synthesis:0"] = RoundRobinPool(["gpt-oss-120b", "gpt-oss-20b", "qwen3-32b"])
  └── _pools["synthesis:1"] = RoundRobinPool(["scout-17b"])
```

**Key files:**
- `token_budget.py:119-132` — `RoundRobinPool.ordered_models()` increments counter on every call
- `token_budget.py:263-275` — `register_pool()` is idempotent (all agents share same pool instances)
- `token_budget.py:744-762` — `get_token_budget()` returns process-wide singleton
- `llm_fallback.py:630` — `pool.ordered_models()` called once per `invoke()`
- `sub_agents.py:412-431` — ReAct loop calls `invoke()` N times per request
- `sub_agents.py:535` — Synthesis creates a NEW FallbackLLM with separate pool group

### Counter Increment Points

| Call Site | Counter Affected | Frequency |
|-----------|-----------------|-----------|
| `sub_agents.py:427` — tool iteration invoke | `tool:0` | Per iteration (1-5x per request) |
| `sub_agents.py:548` — synthesis invoke | `synthesis:0` | Once per request (if tools were called) |
| Any concurrent request from another user | `tool:0` (shared!) | Unpredictable |

### Cross-Agent Contamination

Because `register_pool("tool:0", ...)` returns the SAME pool instance for all agents (portfolio, stock, research, etc.), a stock analysis request running in parallel with a portfolio request will increment the same counter:

```
Time  | Agent     | Action               | tool:0 counter
------|-----------|----------------------|---------------
T+0   | portfolio | iter 1 invoke        | 0 → 1 (llama)
T+1   | stock     | iter 1 invoke        | 1 → 2 (qwen)  ← CONCURRENT
T+2   | portfolio | iter 2 invoke        | 2 → 3 (llama)  ← UNEXPECTED
T+3   | portfolio | iter 3 invoke        | 3 → 4 (qwen)
```

---

## 3. The Portfolio Comparison Flow

### Tool: `get_portfolio_comparison()`
- **File:** `backend/tools/portfolio_tools.py:1109-1261`
- **Params:** `period1="1M"`, `period2="1W"` (defaults)
- **Returns:** Markdown table with side-by-side metrics
- **Period parsing:** `_parse_period()` handles `1W/1M/3M/6M/1Y/ALL` + ISO date ranges
- **Data source:** 100% local (Iceberg OHLCV + portfolio_transactions)

### Agent Prompt
- **File:** `backend/agents/configs/portfolio.py`
- System prompt instructs: "If user asks to compare two periods → call get_portfolio_comparison"
- **No few-shot examples** for period parsing (e.g., "last week vs this week")
- 9 tools bound, but no guidance on multi-tool combinations

### Synthesis Node
- **File:** `backend/agents/nodes/synthesis.py`
- Passthroughs if response >100 chars AND tools were called
- Otherwise invokes synthesis LLM (gpt-oss-120b cascade)
- Has hallucination guard for data-heavy patterns

---

## 4. Proposed Solutions

### Solution A: Per-Invocation Model Pinning (Recommended)

Pin the model for the duration of a single `invoke()` chain (all iterations within one sub-agent node execution). The round-robin counter advances **once per request**, not per iteration.

**Mechanism:** Capture the model selection at iteration 1, store it on the FallbackLLM instance, and reuse it for subsequent iterations within the same request.

```python
# In FallbackLLM:
def invoke(self, messages, *, iteration=1, **kwargs):
    if iteration == 1 or self._pinned_model is None:
        # Normal round-robin selection
        ordered = pool.ordered_models()  # counter++
        self._pinned_model = ordered[0]
    else:
        # Reuse pinned model (no counter increment)
        model = self._pinned_model
```

**Pros:** Minimal code change (~10 lines), preserves round-robin across requests
**Cons:** If pinned model hits budget limit mid-chain, need fallback logic

### Solution B: Batch Round-Robin (Counter Per Request)

Move the round-robin counter increment to the request level instead of the invoke level. Each request gets a model assignment at entry, all iterations use it.

**Mechanism:** `sub_agents.py` calls a new method `pool.select_for_request()` once, passes the selected model to all `invoke()` calls via a `preferred_model` kwarg.

**Pros:** Clean separation of concerns
**Cons:** Requires plumbing a new kwarg through invoke()

### Solution C: Session-Affinity Pools

Track the last model used per `session_id` and prefer it for follow-up requests in the same session.

**Pros:** Multi-turn consistency
**Cons:** Over-engineered for the immediate problem, doesn't fix per-request switching

### Solution D: Synthesis Prompt Fix (Complementary)

Add explicit instructions to the synthesis prompt to preserve structured data (tables, comparisons) rather than collapsing to prose. This doesn't fix the root cause but mitigates the worst symptom.

---

## 5. Recommendations

| Priority | Fix | Effort | Impact |
|----------|-----|--------|--------|
| **P0** | **Solution A: Per-invocation pinning** | ~20 lines in `llm_fallback.py` | Fixes model switching within requests |
| **P1** | Portfolio prompt: add period parsing examples | ~10 lines in `configs/portfolio.py` | Fixes "last week vs this week" params |
| **P2** | Solution D: synthesis prompt preservation | ~5 lines in synthesis prompt | Preserves tables through synthesis |
| **P3** | Tool call logging at INFO level | Already exists in `tool_start`/`tool_done` events | Better debugging |

**Expected outcome:** Same model handles all iterations within a request. Round-robin still distributes load across requests. Synthesis model (separate pool) unchanged — it's intentionally different quality tier.

---

## Sources

All findings from codebase trace:
- `backend/token_budget.py` — lines 119-132 (counter), 263-275 (registration), 744-762 (singleton)
- `backend/llm_fallback.py` — lines 246-268 (pool init), 464-671 (invoke), 630 (ordered_models call)
- `backend/agents/sub_agents.py` — lines 286-590 (ReAct loop), 412-431 (iteration), 535-549 (synthesis)
- `backend/agents/configs/portfolio.py` — lines 1-95 (system prompt)
- `backend/tools/portfolio_tools.py` — lines 1109-1261 (comparison tool), 847-876 (period parsing)
- `backend/bootstrap.py` — lines 247-284 (llm_factory)
- `backend/config.py` — lines 278-308 (get_pool_groups)
- ASETPLTFRM-305 Jira ticket — original root cause analysis and backend logs
