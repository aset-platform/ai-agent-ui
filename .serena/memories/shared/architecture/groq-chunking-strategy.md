# Groq Rate-Limit Chunking Strategy

## N-Tier LLM Cascade (FallbackLLM)

Ordered list of Groq models tried sequentially, with Anthropic as final
paid fallback. Configured via `GROQ_MODEL_TIERS` env var (comma-separated).

**Default tier order:**

| Tier | Model | TPM | Notes |
|------|-------|-----|-------|
| 1 | `llama-3.3-70b-versatile` | 12K | Reliable tool-calling |
| 2 | `moonshotai/kimi-k2-instruct` | 10K | Parallel tools |
| 3 | `openai/gpt-oss-120b` | 8K | Quality |
| 4 | `meta-llama/llama-4-scout-17b-16e-instruct` | 30K | Fast, small |
| 5 | `claude-sonnet-4-6` | unlimited | Paid Anthropic fallback |

## Key Files
- `backend/llm_fallback.py` — `FallbackLLM` class: N-tier cascade with
  budget-aware routing and progressive compression
- `backend/token_budget.py` — Sliding-window deque tracker, 80% threshold,
  thread-safe per-model locks, `get_tpm()` for progressive compression
- `backend/message_compressor.py` — 3 stages: system prompt condensing
  (iter 2+), history truncation (3 turns), tool result truncation (2K chars)
- `backend/config.py` — `groq_model_tiers` (CSV), `max_history_turns`,
  `max_tool_result_chars`
- `backend/agents/config.py` — `AgentConfig.groq_model_tiers: List[str]`
- `docs/design/groq-chunking-strategy.md` — Original design document

## Config (env vars)
- `GROQ_MODEL_TIERS` — comma-separated ordered model list
- `MAX_HISTORY_TURNS` — default: 3
- `MAX_TOOL_RESULT_CHARS` — default: 2000

## Routing Logic (per invoke call)
1. Compress messages (3 stages)
2. Estimate tokens
3. For each Groq tier in order:
   - Check `can_afford(model, est)` (80% threshold)
   - If unaffordable, try progressive compression at 70% of model's TPM
   - If still unaffordable, skip to next tier
   - Invoke; on `RateLimitError`/`APIStatusError`/`APIConnectionError`,
     cascade to next tier
4. Anthropic as final fallback (no budget check)

## Key Design Decisions
- `max_retries=0` on ChatGroq — disables Groq SDK internal retries
  (was causing 45-56s delays before cascade)
- `APIStatusError` (413) caught alongside `RateLimitError` (429)
- Progressive compression targets 70% of TPM (not 100%) for headroom
- Tier 1 is largest model — small models may skip tool calls on
  complex prompts

## Groq Free Tier Limits (March 2026)
See `token_budget._DEFAULT_LIMITS` for all models. Key ones:
- llama-3.3-70b-versatile: 30 RPM, 12K TPM, 1K RPD
- kimi-k2-instruct: 30 RPM, 10K TPM
- gpt-oss-120b: 30 RPM, 8K TPM, 1K RPD, 200K TPD
- scout-17b: 30 RPM, 30K TPM, 1K RPD, 500K TPD
