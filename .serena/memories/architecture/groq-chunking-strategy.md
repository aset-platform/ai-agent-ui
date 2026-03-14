# Groq Rate-Limit Chunking Strategy

## Three-Tier Routing
- **Router**: `meta-llama/llama-4-scout-17b-16e-instruct` (30K TPM, 500K TPD)
- **Responder**: `openai/gpt-oss-120b` (8K TPM, 200K TPD)
- **Fallback**: Anthropic Claude (paid, no rate limits)
- Cascade: router → responder → anthropic per-call (not per-conversation)

## Key Files
- `backend/token_budget.py` — Sliding-window deque tracker, 80% threshold, thread-safe per-model locks
- `backend/message_compressor.py` — 3 stages: system prompt condensing (iter 2+), history truncation (3 turns), tool result truncation (2K chars)
- `backend/llm_fallback.py` — Rewritten: three-tier FallbackLLM with budget-aware routing
- `backend/config.py` — `groq_router_model`, `groq_responder_model`, `max_history_turns`, `max_tool_result_chars`
- `docs/design/groq-chunking-strategy.md` — Full design document

## Config (env vars)
- `GROQ_ROUTER_MODEL` — default: `meta-llama/llama-4-scout-17b-16e-instruct`
- `GROQ_RESPONDER_MODEL` — default: `openai/gpt-oss-120b`
- `MAX_HISTORY_TURNS` — default: 3
- `MAX_TOOL_RESULT_CHARS` — default: 2000

## Groq Free Tier Limits (March 2026)
See `token_budget._DEFAULT_LIMITS` for all models. Key ones:
- scout-17b: 30 RPM, 30K TPM, 1K RPD, 500K TPD
- gpt-oss-120b: 30 RPM, 8K TPM, 1K RPD, 200K TPD
