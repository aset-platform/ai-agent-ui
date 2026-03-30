# Context-Aware Chat — Architecture (Phase 1)

## Overview
Multi-turn conversation awareness via server-side session context.
Follow-up detection, rolling summary, topic tracking, user context injection.

## Components

### ConversationContext (`backend/agents/conversation_context.py`)
- Dataclass: session_id, current_topic, last_agent, last_intent, summary,
  tickers_mentioned, user_tickers, market_preference, subscription_tier,
  turn_count, last_updated
- ConversationContextStore: thread-safe in-memory dict, 1hr TTL, singleton `context_store`
- update_summary(): Ollama→Groq cascade, 2-3 sentence rolling summary
- _get_summary_llm(): FallbackLLM(max_tiers=2, ollama_first=True)

### Topic Classifier (`backend/agents/nodes/topic_classifier.py`)
- classify_followup(user_input, ctx) → "follow_up" | "new_topic"
- 1-shot LLM prompt (~50 tokens, Groq scout-17b)
- Returns "new_topic" if no context, first turn, or LLM failure

### Guardrail Integration (`backend/agents/nodes/guardrail.py`)
- After cache check, before content safety
- Loads context → classifies → if follow_up with known agent, returns
  next_agent=ctx.last_agent (skips router)
- Backward compatible: no session_id → current behavior

### Context Injection (`backend/agents/base.py`)
- _build_messages(user_input, history, session_id="")
- Prepends [Conversation Context] block to system prompt
- Includes: turn count, summary, topic, user portfolio, market preference

### Post-Response Update (`backend/routes.py`)
- _update_conversation_context() called after graph.invoke()
- Creates context on first turn, populates user profile from _build_user_context()
- Updates: last_agent, intent, topic, tickers, calls update_summary()

### Frontend (`frontend/hooks/useSendMessage.ts`)
- session_id passed in HTTP body + WebSocket payload
- sessionId from ChatProvider (crypto.randomUUID())

## Data Flow
```
Turn N arrives → load ConversationContext[session_id]
  → classify: follow_up / new_topic
  → route (skip router on follow-up)
  → inject summary into system prompt
  → agent processes → stream response
  → update context: agent, intent, topic, tickers, summary
```

## Design Specs
- `docs/superpowers/specs/2026-03-30-context-aware-chat-design.md`
- `docs/superpowers/plans/2026-03-30-context-aware-chat.md`

## Future Phases
- Phase 2: History summarization replacing lossy truncation
- Phase 3: Session resume from Iceberg audit log
