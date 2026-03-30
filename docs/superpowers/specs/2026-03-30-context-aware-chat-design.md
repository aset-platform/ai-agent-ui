# Context-Aware Chat — Phase 1 Design Spec

**Date:** 2026-03-30
**Branch:** `feature/sprint4`
**Status:** Draft
**Phase:** 1 of 3

---

## Context

The chat agent is stateless per-request. Each message gets the
last 3 turns of history (compressed), but the agent cannot:

1. Detect follow-up questions ("And what about dividends?")
2. Maintain topic continuity across turns
3. Remember context pruned by compression (turns 4+)
4. Carry user portfolio/market preferences automatically

Users experience this as: agent treats every message as brand
new, switches agents unexpectedly, forgets what was discussed
5 messages ago, and doesn't understand "that" or "it" references.

**Goal (Phase 1):** Make the chat agent context-aware within a
single session — detect follow-ups, track the current topic,
maintain a rolling summary, and inject user context.

**Phases:**
- **Phase 1** (this spec): Follow-up detection + rolling
  summary + topic tracking
- **Phase 2** (future): Summarization replacing lossy truncation
- **Phase 3** (future): Session resume from audit log

---

## Approach: ConversationContext Manager (Approach B)

A server-side `ConversationContext` object persists across
messages within a session. A topic classifier detects follow-ups.
A rolling summary captures key context that survives compression.

---

## Components

### 1. ConversationContext Data Model

**New file:** `backend/agents/conversation_context.py`

```python
@dataclass
class ConversationContext:
    session_id: str
    current_topic: str = ""
    last_agent: str = ""
    last_intent: str = ""
    summary: str = ""
    tickers_mentioned: list[str] = field(default_factory=list)
    user_tickers: list[str] = field(default_factory=list)
    market_preference: str = ""    # "india" | "us" | "all"
    subscription_tier: str = ""    # "free" | "pro" | "premium"
    turn_count: int = 0
    last_updated: float = 0.0
```

**In-memory store** with TTL-based cleanup:

```python
class ConversationContextStore:
    _store: dict[str, ConversationContext]
    _ttl: int = 3600  # 1 hour idle

    def get(self, session_id: str) -> ConversationContext | None
    def upsert(self, session_id: str, ctx: ConversationContext) -> None
    def cleanup(self) -> None  # evict expired
```

**Why in-memory (not Redis)?** Context is small (~500 bytes),
session-scoped, and single-instance. Redis would add latency
for every turn. Cleanup runs every 5 minutes via background task.

### 2. Topic Classifier in Guardrail Node

**Modified file:** `backend/agents/nodes/guardrail.py`

Before routing, classify the user message as `follow_up` or
`new_topic` using a 1-shot LLM prompt:

```
Given the conversation summary and the new user message,
classify as "follow_up" or "new_topic".

Summary: {context.summary}
Last question topic: {context.current_topic}
New message: {user_input}

Answer with ONLY "follow_up" or "new_topic".
```

**Routing logic:**

```
If context exists AND turn_count > 0:
    classify(user_input, context)
    If follow_up:
        → reuse context.last_agent + context.last_intent
        → skip router node (direct to sub-agent)
    If new_topic:
        → clear current_topic
        → proceed to router as normal
If no context (first message):
    → proceed to router as normal
```

**Model:** Uses cheapest available — Groq scout-17b (30K TPM).
Cost: ~50 tokens per classification.

### 3. Rolling Summary Generator

**In:** `backend/agents/conversation_context.py`

After each assistant response completes, a background task
updates the conversation summary.

**Summary prompt** (~200 tokens):

```
Update this conversation summary given the latest exchange.
Keep it under 3 sentences. Include: topic discussed, key
tickers/numbers mentioned, and any decisions or conclusions.

Previous summary: {context.summary or "No previous context."}
User asked: {user_input}
Assistant answered: {response_text[:500]}

Updated summary:
```

**Model cascade:**
1. Ollama local (`gpt-oss:20b`) — zero cost, preferred
2. Groq `llama-4-scout-17b` — fallback if Ollama unavailable
3. Groq `llama-3.3-70b-versatile` — second fallback
4. Skip summary update — graceful degradation

**Timing:** Async, fires after response is fully streamed.
Does NOT block the user's response delivery.

**Graceful degradation:** If all models unavailable, summary
is not updated. The system falls back to current 3-turn-only
behavior — no regression.

### 4. Context Injection into System Prompt

**Modified file:** `backend/agents/base.py`

Prepend a `[Conversation Context]` block to the system prompt
before the agent's regular instructions:

```
[Conversation Context]
This is turn {turn_count} of an ongoing conversation.
Summary: {context.summary}
Current topic: {context.current_topic}
User's portfolio: {user_tickers}
Market preference: {market_preference}

---
{original_system_prompt}
```

This is injected in `_build_messages()` so every agent
(sentiment, research, forecaster, portfolio) automatically
sees the context without per-agent changes.

**Token cost:** ~50-80 tokens added to system prompt.
Well within the existing compression budget.

### 5. Session ID Passthrough

**Modified files:**
- `backend/models.py` — add `session_id: str | None = None`
  to `ChatRequest`
- `frontend/hooks/useSendMessage.ts` — include `session_id`
  from `ChatProvider` in the request body
- `backend/routes.py` / `backend/ws.py` — extract `session_id`
  and pass to agent graph

**Frontend already generates** `session_id` via
`crypto.randomUUID()` in `ChatProvider.tsx` — just needs
to pass it through.

### 6. User Context Enrichment

**Modified file:** `backend/agents/sub_agents.py`

On first message of a session, populate `ConversationContext`
with user profile data:

- `user_tickers` — from `UserRepository.get_user_tickers()`
- `market_preference` — derived from ticker suffixes
  (`.NS`/`.BO` → India, else US)
- `subscription_tier` — from JWT claims

This reuses the existing `_build_context_block()` function
(already in `sub_agents.py` lines 112-170).

---

## Data Flow

```
Turn 1: "What is the sentiment for RELIANCE.NS?"
  → No context → router → sentiment agent
  → Response streamed → summary generated (async):
    "User asked about RELIANCE.NS sentiment. Score: 0.62
     (bullish). Based on 7-day news headlines."
  → Context saved: topic="RELIANCE.NS sentiment",
    agent="sentiment", tickers=["RELIANCE.NS"]

Turn 2: "And what about the forecast?"
  → Context loaded → classify: "follow_up"
  → Skip router → reuse sentiment? No — "forecast" is
    different agent. Classify recognizes topic continuity
    (RELIANCE.NS) but different capability → route to
    forecaster with context injected
  → Response: "RELIANCE.NS 9-month forecast..."
  → Summary updated: "Discussed RELIANCE.NS. Sentiment
     bullish (0.62). Forecast: 9-month target ₹2,650."

Turn 3: "How does that compare to TCS?"
  → Context loaded → classify: "follow_up"
  → "that" resolves to forecast (from summary)
  → Route to forecaster with both tickers in context
  → Response compares RELIANCE.NS vs TCS.NS

Turn 4: "Switch to my portfolio overview"
  → Context loaded → classify: "new_topic"
  → Clear current_topic → route to portfolio agent
  → Summary updated with new topic
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/agents/conversation_context.py` | NEW |
| `backend/agents/nodes/guardrail.py` | MODIFY |
| `backend/agents/base.py` | MODIFY |
| `backend/agents/sub_agents.py` | MODIFY |
| `backend/models.py` | MODIFY |
| `backend/routes.py` | MODIFY |
| `backend/ws.py` | MODIFY |
| `frontend/hooks/useSendMessage.ts` | MODIFY |
| `tests/backend/test_conversation_context.py` | NEW |
| `tests/backend/test_guardrail_followup.py` | NEW |

---

## Edge Cases

- **First message:** No context exists. Proceed to router.
  Initialize context after response.
- **Ollama + Groq both down:** Summary not updated. System
  degrades to current 3-turn behavior. No crash.
- **Very long sessions (50+ turns):** Summary stays 2-3
  sentences regardless — LLM is instructed to condense.
  In-memory context is ~500 bytes, no growth concern.
- **Concurrent sessions (same user):** Different session_ids,
  different contexts. No conflict.
- **Session timeout (1h idle):** Context evicted. Next message
  starts fresh. Matches user expectation.
- **Agent mismatch on follow-up:** Classifier may detect
  follow-up but the topic requires a different agent
  (e.g., "And the forecast?" after sentiment). The classifier
  returns `follow_up` but the router still decides the agent
  based on intent. Context (summary + topic) is injected
  into whichever agent is selected.

---

## Verification

1. **Unit tests:** ConversationContext CRUD, store TTL eviction,
   topic classifier with mock LLM responses
2. **Integration:** Multi-turn conversation via HTTP:
   - Turn 1: "Sentiment for AAPL" → sentiment agent
   - Turn 2: "And the forecast?" → forecaster with AAPL context
   - Turn 3: "New topic: my portfolio" → portfolio agent
3. **Regression:** Existing chat tests pass (no breaking changes)
4. **Manual:** Test via UI — verify follow-ups stay on topic,
   new topics route correctly, summary visible in backend logs

---

## Token Budget Impact

| Component | Tokens/turn | Who pays |
|-----------|------------|----------|
| Context injection (system prompt) | ~50-80 | Main LLM call |
| Topic classification | ~50 | Groq scout-17b |
| Summary generation | ~200 | Ollama (free) or Groq fallback |
| **Total overhead** | **~300** | **Negligible on free tier** |

At 300 tokens/turn overhead with Groq scout-17b (30K TPM),
this supports ~100 turns/minute — well above any realistic
chat volume.
