# Agent Initialization Pattern

## Critical: BaseAgent.__init__ calls _setup() → _build_llm()

Any attributes used in `_build_llm()` MUST exist before the constructor runs.

### Problem
Factory functions (`create_general_agent`, `create_stock_agent`) set `agent.token_budget` and `agent.compressor` AFTER construction. But `__init__` calls `_setup()` which calls `_build_llm()` which reads those attrs.

### Solution
`BaseAgent.__init__` sets defaults via `hasattr` check before calling `_setup()`:
```python
if not hasattr(self, "token_budget"):
    from token_budget import TokenBudget
    self.token_budget = TokenBudget()
if not hasattr(self, "compressor"):
    from message_compressor import MessageCompressor
    self.compressor = MessageCompressor()
```

Factory functions then override with shared instances after construction.

### FallbackLLM Interface Change
`invoke()` now accepts `iteration=` kwarg (passed from loop.py/stream.py).
Old: `invoke(messages, **kwargs)`
New: `invoke(messages, *, iteration=1, **kwargs)`
