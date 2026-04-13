# Conversation Context PG Persistence (ASETPLTFRM-303)

## Overview
ConversationContext is now persisted to PostgreSQL for cross-session resume.
Previously in-memory only (lost on restart/page refresh).

## Table: `conversation_contexts`
- PK: `session_id` (VARCHAR 36)
- Indexed: `user_id`, `updated_at`
- Fields: last_agent, last_intent, summary, last_response, tickers_mentioned[], user_tickers[], market_preference, subscription_tier, turn_count, last_updated

## Architecture
- `ConversationContextStore` in `backend/agents/conversation_context.py`
- In-memory dict (fast) + async PG persistence (durable)
- `upsert()` saves to PG synchronously (not daemon thread — daemon threads fail inside uvicorn)
- `get()` checks in-memory first, falls back to PG on miss
- `get_latest_for_user(user_id)` — cross-session resume: loads most recent context for a user when frontend generates new session_id

## Integration Points
- `backend/routes.py` — HTTP handler: resume from user's last session on new session_id
- `backend/ws.py` — WebSocket handler: same resume logic + user_id tracking
- Uses async NullPool engine (`_run_async` pattern) for PG access from sync contexts

## Key Decisions
- Daemon thread PG save failed (event loop conflicts with uvicorn) — switched to synchronous save (~5ms with NullPool)
- Only asyncpg available in container (no psycopg2) — all PG ops use async engine
- Migration: `f1a2b3c4d5e6_add_conversation_contexts.py` + direct DDL (Alembic not in container)
