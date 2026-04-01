# Memory-Augmented Chat Architecture

## Overview
pgvector-backed per-user semantic memory layer. Hybrid chunks: session summaries + structured facts + preferences. Embedded via Ollama nomic-embed-text (768 dim).

## Components

### Write Path (per response, async fire-and-forget)
- `memory_extractor.py`: upserts session summary + extracts structured facts via LLM
- `audit_persistence.py`: per-answer Iceberg chat_audit_log write
- Both scheduled via `asyncio.run_coroutine_threadsafe()` from ws.py worker thread
- Skips responses < 50 chars (avoids wasting calls on "I will call tool X")

### Read Path (per message, sync before graph.invoke)
- `memory_retriever.py`: cosine similarity top-5 from pgvector
- Query embedded via EmbeddingService, 3s timeout
- Results formatted as `[Memory context]` block (~200 tokens)
- Injected into sub-agent system prompt before `[Prior conversation]`

### Embedding Service
- `embedding_service.py`: wraps Ollama `/api/embed`
- Model: nomic-embed-text (768 dim, 8K context)
- Sync HTTP (runs in worker thread), 5s timeout
- Health check with TTL cache (30s)
- Singleton via `@lru_cache`

### Database
- Table: `public.user_memories` (PostgreSQL + pgvector extension)
- Docker: `pgvector/pgvector:pg16` image
- Columns: memory_id, user_id (FK CASCADE), session_id, memory_type (summary/fact/preference), content, structured (JSONB), embedding (vector(768)), turn_number, created_at, expires_at
- Indexes: user_id, (user_id, session_id), IVFFlat cosine (lists=20)
- ORM: `backend/db/models/memory.py` with extend_existing=True

### Frontend
- Session resume: "Start new session from this" button in PastSessionsTab
- Memory indicator: violet "memory" chip below timestamp in MessageBubble
- WS final event: `memory_used: true` flag

## Graceful Degradation
- Ollama down → embed returns None → retrieval returns [] → falls back to ConversationContext.summary
- pgvector query fails → empty memories, no crash
- memory_enabled=False → entire pipeline disabled, zero overhead

## Files
- `backend/embedding_service.py` (new)
- `backend/memory_extractor.py` (new)
- `backend/memory_retriever.py` (new)
- `backend/audit_persistence.py` (new)
- `backend/db/models/memory.py` (new)
- `backend/ws.py` (modified: retrieval + extraction hooks)
- `backend/agents/sub_agents.py` (modified: memory injection)
- `backend/agents/graph_state.py` (modified: retrieved_memories field)
- `frontend/providers/ChatProvider.tsx` (modified: startFromSession)
- `frontend/components/PastSessionsTab.tsx` (modified: resume button)
- `frontend/components/MessageBubble.tsx` (modified: memory indicator)

## Added
2026-04-01
