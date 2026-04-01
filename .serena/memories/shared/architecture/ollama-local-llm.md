# Ollama Local LLM Integration

## Overview
Host-native Ollama provides zero-cost local inference as Tier 0 in the
FallbackLLM cascade. NOT containerized — runs directly on the host.

## Profiles (via `ollama-profile` CLI)
| Profile | Model | Keep-Alive | Context | Use Case |
|---------|-------|------------|---------|----------|
| coding | qwen2.5-coder:14b | 2h | 16K | Code generation (Qwen delegation) |
| reasoning | gpt-oss:20b | 1h | 8K | Sentiment, planning, chat fallback |
| embedding | nomic-embed-text | 4h | 8K | Memory vectors (pgvector) |

Only one LLM model at a time (RAM constraint). Embedding model (274MB)
coexists with LLM models in theory but gets evicted by larger models.

## Cascade Position
- `ollama_first=True`: tried BEFORE Groq (sentiment, summary, batch jobs)
- `ollama_first=False`: tried AFTER Groq exhaustion (chat fallback)
- If Ollama unavailable, cascade skips it transparently

## Configuration
```
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434  # host-native
OLLAMA_MODEL=gpt-oss:20b
OLLAMA_NUM_CTX=8192
OLLAMA_TIMEOUT=120
OLLAMA_HEALTH_CACHE_TTL=30
```

In Docker: backend reaches host Ollama via `host.docker.internal:11434`.

## Key Components
- `backend/ollama_manager.py`: OllamaManager (health probe, model load/unload)
- `~/.local/bin/ollama-profile`: CLI script for profile switching
- `backend/embedding_service.py`: EmbeddingService wrapping /api/embed
- Admin API: GET/POST /v1/admin/ollama/{status,load,unload}

## Embedding Service
- Model: nomic-embed-text (768 dim, 8K context)
- Sync HTTP calls (runs in worker threads)
- 5s timeout, singleton via @lru_cache
- Used by memory_extractor (write) and memory_retriever (query)

## Gotchas
- Ollama not containerized — no Docker service, host.docker.internal only
- embedding profile uses /api/embed (not /api/generate) for warmup
- Models auto-load on demand but first call is slow (~2-5s cold start)
