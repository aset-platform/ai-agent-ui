# TokenBudget Concurrency — Reserve/Release Pattern

## Problem
TOCTOU race: `can_afford()` and `record()` each acquire/release the lock independently. Between them, a second thread passes `can_afford`, causing double-spend and triggering Groq 429s.

## Solution: Atomic Reserve/Release

### `reserve(model, tokens) -> bool`
Holds the lock while checking ALL budget dimensions AND tentatively recording the spend. Eliminates the TOCTOU window.

### `release(model, tokens)`
Rolls back a prior `reserve()` on LLM failure, freeing the budget for the next cascade tier.

### Usage in LLM Cascade
```python
if not budget.reserve(model, estimated_tokens):
    continue  # skip to next tier

try:
    result = llm.invoke(messages)
    # reserve already recorded — no separate record() needed
except (RateLimitError, ConnectionError):
    budget.release(model, estimated_tokens)
    continue  # cascade to next tier
```

### Key Invariant
- `reserve()` is the ONLY way to claim budget in the hot path
- `can_afford()` still exists for read-only checks (e.g., `best_available()`)
- `record()` still exists for backward compatibility but is not used in the cascade

## Key Files
- `backend/token_budget.py` — `reserve()` (line ~260), `release()` (line ~347)
- `backend/llm_fallback.py` — cascade loop using reserve/release
