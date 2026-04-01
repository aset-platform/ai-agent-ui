# bind_tools Model Lookup Stale After Binding

## Problem
When using pool-aware routing in FallbackLLM, the `_model_lookup` dict
(model_name → (name, raw, bound) tuple) was built in `__init__()` from
`self._groq_tiers`. But `bind_tools()` is called LATER (by sub-agents),
which updates `self._groq_tiers[i]` with bound LLMs.

The `_model_lookup` still held references to the **unbound** raw LLMs.
Pool routing sent Groq requests without any `tools` in the payload.
The model returned text ("I will call tool X") instead of actual tool calls.

## Symptom
- Agent says "I will call get_portfolio_summary" but never actually calls it
- Groq request JSON has `messages` and `model` but no `tools` field
- ReAct loop exits after 1 iteration (no tool_calls on response)
- Synthesis runs on the empty text response

## Fix
Rebuild `_model_lookup` at the end of `bind_tools()`:

```python
def bind_tools(self, tools, **kwargs):
    for i, (name, raw, _bound) in enumerate(self._groq_tiers):
        bound = raw.bind_tools(tools, **kwargs)
        self._groq_tiers[i] = (name, raw, bound)

    # Critical: rebuild lookup with bound LLMs
    self._model_lookup = {
        name: (name, raw, bound)
        for name, raw, bound in self._groq_tiers
    }
```

## File
- `backend/llm_fallback.py` — `bind_tools()` method + `_model_lookup`
