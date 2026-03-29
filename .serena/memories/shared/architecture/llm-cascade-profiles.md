# LLM Cascade — Ollama + Groq + Anthropic Profiles

## Overview
N-tier fallback with local Ollama (Tier 0), Groq cloud (Tiers 1-4), and Anthropic (paid fallback). The `ollama_first` flag controls whether Ollama is tried before or after Groq.

## Cascade Order

### Sentiment/Batch (`ollama_first=True`)
```
Tier 0: Ollama gpt-oss:20b (local, zero cost)
Tier 1-4: Groq (llama-3.3-70b → kimi-k2 → gpt-oss-120b → llama-4-scout)
Tier 5: Anthropic claude-sonnet-4-6 (paid)
```

### Interactive Chat (`ollama_first=False`)
```
Tier 1-4: Groq (same order)
Tier 5: Ollama gpt-oss:20b (local, before paid)
Tier 6: Anthropic claude-sonnet-4-6 (paid)
```

### Test Profile (`AI_AGENT_UI_ENV=test`)
- Free-tier Groq only (no Anthropic, no Ollama)

## Ollama Integration
- `OllamaManager` (`backend/ollama_manager.py`): TTL-cached health probe (30s), load/unload
- `ollama-profile` CLI: coding (Qwen 14B), reasoning (GPT-OSS 20B), unload, status
- Admin API: GET/POST /v1/admin/ollama/{status,load,unload}
- Performance tuning: flash attention, KV cache q8_0, num_ctx 8192
- Graceful degradation: if Ollama unavailable, cascade skips it entirely
- Context check: est tokens > num_ctx → skip with `context_exceeded` reason

## Configuration
- `backend/config.py`: ollama_enabled, ollama_model, ollama_base_url, ollama_num_ctx
- `backend/llm_fallback.py`: FallbackLLM with `ollama_first` flag
- `backend/bootstrap.py`: llm_factory passes ollama_first=False (chat)
- `backend/tools/sentiment_agent.py`: _get_llm passes ollama_first=True
- `backend/jobs/gap_filler.py`: batch with auto-load/unload

## Observability
- provider="ollama" in ObservabilityCollector
- LLM Usage widget: provider from Iceberg data (not hardcoded)
- Cascade reasons: ollama_error, context_exceeded, ollama_unavailable