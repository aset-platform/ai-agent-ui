# Round-Robin Model Pool Cascade

## Overview
Replaces sequential tier-first cascade with pool-aware round-robin. Spreads Groq TPD load across models instead of draining llama-3.3-70b first.

## Pool Configuration

### Tool Profile (sub-agents with tool calls)
- Pool 1 "tool": llama-3.3-70b, qwen3-32b (round-robin)
- Pool 2 "quality": gpt-oss-120b, gpt-oss-20b (round-robin)
- Pool 3 "fast": llama-4-scout-17b (single)
- Anthropic fallback

### Synthesis Profile (text-only final response)
- Pool 1 "quality": gpt-oss-120b, gpt-oss-20b, qwen3-32b (round-robin)
- Pool 2 "fast": llama-4-scout-17b (single)

## Algorithm
Per-pool atomic counter (thread-safe via lock). On each invoke():
1. `start_idx = counter % len(pool)`, counter++
2. Try models: start → wrap around
3. Budget exhausted or API error → next model in pool
4. All pool models exhausted → cascade to next pool

## Key Components
- `RoundRobinPool` class in `token_budget.py`
- `_try_model()` extraction in `llm_fallback.py`
- `pool_groups` parameter on FallbackLLM.__init__
- `_model_lookup` rebuilt after bind_tools() (critical fix)
- `get_pool_groups(profile)` helper in `config.py`

## TokenBudget Singleton
- `get_token_budget()` replaces 10+ independent TokenBudget() instances
- `seed_daily_from_iceberg()` on creation: TPD/RPD persist across restarts
- Est. queries: per-model sum (not misleading global average)

## Synthesis Pass
After ReAct tool loop in sub_agents.py, final text re-invoked with synthesis-tier FallbackLLM. Uses synthesis pool (gpt-oss-120b first). Falls back to tool-tier response if synthesis fails.

## Rollback
Set `ROUND_ROBIN_ENABLED=false` → reverts to legacy sequential cascade.

## Combined Free-Tier TPD: ~2.0M (was ~2.3M before kimi-k2 decommission)

## Added
2026-04-01
