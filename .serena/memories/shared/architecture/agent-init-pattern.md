# Agent Initialization Pattern

## BaseAgent.__init__ calls _setup() → _build_llm()

Any attributes used in `_build_llm()` MUST exist before the constructor runs.

### Constructor Flow
```python
BaseAgent.__init__(config, tool_registry, token_budget, compressor, obs_collector)
  → sets self.token_budget, self.compressor (with defaults)
  → calls _setup()
    → _build_llm() → FallbackLLM (N-tier Groq cascade + Anthropic)
    → bind_tools(tools)
    → _build_synthesis_llm() → FallbackLLM (synthesis cascade)
```

### LLM Builders in BaseAgent (Mar 19, 2026)
`_build_llm()` and `_build_synthesis_llm()` are now in BaseAgent
(extracted from identical copies in GeneralAgent + StockAgent).

- `GeneralAgent` — inherits both, no override needed (`pass`)
- `StockAgent` — inherits both, only overrides `format_response()`

### FallbackLLM Interface
`invoke()` accepts `iteration=` kwarg (passed from loop.py/stream.py).
```python
invoke(messages, *, iteration=1, **kwargs)
```

### Test Mode
When `AI_AGENT_UI_ENV=test`:
- Uses `test_model_tiers` (free Groq only)
- No Anthropic fallback (`anthropic_model=None`)
- No synthesis LLM (returns `None`)
- `cascade_profile="test"`
