# Per-Request Model Pinning — ASETPLTFRM-305

**Date:** 2026-04-16
**Status:** Approved
**Ticket:** ASETPLTFRM-305 — Fix portfolio comparison chat: round-robin synthesis loses week-on-week data

---

## Problem

When a user asks "What is portfolio health from last week to this week?", the ReAct agent loop calls `FallbackLLM.invoke()` multiple times (once per iteration). Each call increments the global round-robin counter, causing different models per iteration within the same request:

- iter 1: `llama-3.3-70b-versatile` — decides to call tool
- iter 2: `qwen/qwen3-32b` — processes tool result (different model)
- iter 3: `openai/gpt-oss-120b` — synthesis (third model)

This causes:
1. **Context fragmentation** — each model interprets the conversation differently
2. **Data loss** — synthesis model collapses structured comparison tables into flat prose
3. **Inconsistent tool parameter inference** — no period parsing examples in prompt

---

## Fix 1: Per-Request Model Pinning

### Mechanism

`FallbackLLM` gets a `_pinned_model` attribute that locks model selection after the first successful invoke within a request.

### API

```python
class FallbackLLM:
    _pinned_model: str | None = None

    def pin_reset(self) -> None:
        """Reset model pin for a new request context."""
        self._pinned_model = None

    def invoke(self, messages, *, iteration=1, **kwargs):
        # If pinned and model still has budget → reuse
        if self._pinned_model is not None:
            success = self._try_pinned(messages, ...)
            if success is not None:
                return success
            # Pinned model exhausted after compression →
            # unpin and fall through to normal cascade
            self._pinned_model = None

        # Normal round-robin selection (counter++)
        for pool in self._pool_groups:
            ordered = pool.ordered_models()
            for model_name in ordered:
                result = self._try_model(...)
                if result is not None:
                    self._pinned_model = model_name
                    return result
```

### Call site

```python
# sub_agents.py — before iteration loop
llm = llm_factory(agent_id=config.agent_id)
llm_with_tools = llm.bind_tools(tools)
llm.pin_reset()  # Reset for this request

for iteration in range(MAX_ITERATIONS):
    response = llm_with_tools.invoke(messages, iteration=iteration+1)
    # All iterations use the same model (pinned after iter 1)
```

### Budget exhaustion mid-chain

If the pinned model's TPM is exhausted during iteration 2+:
1. Attempt progressive compression (target 70% of pinned model's TPM)
2. Re-estimate tokens and retry `reserve()` on the pinned model
3. If still over budget: unpin (`_pinned_model = None`) and cascade normally
4. This is the existing compression logic — just applied to the pinned path first

### What stays the same

- Synthesis LLM: separate instance, separate pool, unaffected by tool pinning
- Round-robin across requests: counter increments once per request (on first invoke)
- Budget tracking: `reserve()`/`release()` still called per invoke
- API error handling: on retriable error, release budget and try next model (respects pin if possible, unpins if pinned model is down)

### Files

- `backend/llm_fallback.py` — `_pinned_model`, `pin_reset()`, pinned path in `invoke()` (~20 lines)
- `backend/agents/sub_agents.py` — call `pin_reset()` before iteration loop (~2 lines)

---

## Fix 2: Portfolio Prompt — Period Parsing Examples

Add few-shot examples to the portfolio agent system prompt so the LLM correctly maps natural language to period parameters.

### Addition to system prompt

```
## Period parameter examples for get_portfolio_comparison:
- "last week vs this week" → period1="2W", period2="1W"
- "this month vs last month" → period1="2M", period2="1M"
- "this week vs last 3 months" → period1="3M", period2="1W"
- "April 1-7 vs April 8-14" → period1="2026-04-01:2026-04-07", period2="2026-04-08:2026-04-14"
- "how did I do this week" → period1="1W" (single period, omit period2)
```

### File

- `backend/agents/configs/portfolio.py` — add ~8 lines to system prompt string

---

## Fix 3: Synthesis Table Preservation

Add a directive to the synthesis system prompt to preserve structured data formats.

### Addition to synthesis prompt

```
IMPORTANT: When the agent response contains markdown tables,
comparison grids, or structured data — preserve them exactly.
Do not collapse tables into prose summaries. Add narrative
context around tables but never remove or flatten them.
```

### File

- `backend/agents/nodes/synthesis.py` — add ~3 lines to synthesis system message

---

## Verification

1. **Model pinning test:** Send a chat message, check backend logs — all tool iterations should show the same model name
2. **Period parsing test:** Ask "compare my portfolio last week vs this week" — verify tool params are `period1="2W", period2="1W"` in `tool_start` event
3. **Table preservation test:** Verify the final response contains a markdown comparison table, not flat prose
4. **Round-robin test:** Send two independent chat requests — verify they use different models (counter still advances across requests)
5. **Budget exhaustion test:** Artificially lower a model's TPM limit, send a long multi-iteration query — verify graceful cascade after compression attempt
6. **Existing tests:** `python -m pytest tests/ -k "fallback"` — verify no regression

---

## Scope

| Fix | File | Lines | Risk |
|-----|------|-------|------|
| Model pinning | `llm_fallback.py` + `sub_agents.py` | ~22 | Low |
| Period examples | `configs/portfolio.py` | ~8 | Zero |
| Table preservation | `nodes/synthesis.py` | ~3 | Zero |
| **Total** | **4 files** | **~33** | **Low** |
