# Context-Aware Chat (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the chat agent detect follow-up questions, track the current topic, and maintain a rolling conversation summary that survives compression.

**Architecture:** A `ConversationContext` object lives in a server-side in-memory store keyed by `session_id`. The guardrail node classifies each message as `follow_up` or `new_topic`. A post-response background task updates a rolling summary via Ollama/Groq. The summary is injected into every agent's system prompt.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, LangChain, Ollama/Groq for summary generation, TypeScript/Next.js for frontend session_id passthrough.

**Spec:** `docs/superpowers/specs/2026-03-30-context-aware-chat-design.md`

---

### Task 1: ConversationContext Data Model + Store

**Files:**
- Create: `backend/agents/conversation_context.py`
- Test: `tests/backend/test_conversation_context.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for ConversationContext and ContextStore."""

import time

import pytest

from agents.conversation_context import (
    ConversationContext,
    ConversationContextStore,
)


class TestConversationContext:
    def test_default_values(self):
        ctx = ConversationContext(session_id="s1")
        assert ctx.session_id == "s1"
        assert ctx.current_topic == ""
        assert ctx.summary == ""
        assert ctx.turn_count == 0
        assert ctx.tickers_mentioned == []

    def test_update_fields(self):
        ctx = ConversationContext(session_id="s1")
        ctx.current_topic = "AAPL sentiment"
        ctx.turn_count = 3
        ctx.tickers_mentioned = ["AAPL"]
        assert ctx.current_topic == "AAPL sentiment"
        assert ctx.turn_count == 3


class TestConversationContextStore:
    def test_get_missing_returns_none(self):
        store = ConversationContextStore()
        assert store.get("missing") is None

    def test_upsert_and_get(self):
        store = ConversationContextStore()
        ctx = ConversationContext(session_id="s1")
        ctx.summary = "Discussed AAPL"
        store.upsert("s1", ctx)
        result = store.get("s1")
        assert result is not None
        assert result.summary == "Discussed AAPL"

    def test_cleanup_evicts_expired(self):
        store = ConversationContextStore(ttl=1)
        ctx = ConversationContext(session_id="s1")
        ctx.last_updated = time.time() - 10
        store.upsert("s1", ctx)
        store.cleanup()
        assert store.get("s1") is None

    def test_cleanup_keeps_fresh(self):
        store = ConversationContextStore(ttl=3600)
        ctx = ConversationContext(session_id="s1")
        ctx.last_updated = time.time()
        store.upsert("s1", ctx)
        store.cleanup()
        assert store.get("s1") is not None

    def test_delete(self):
        store = ConversationContextStore()
        ctx = ConversationContext(session_id="s1")
        store.upsert("s1", ctx)
        store.delete("s1")
        assert store.get("s1") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/backend/test_conversation_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.conversation_context'`

- [ ] **Step 3: Implement ConversationContext + Store**

Create `backend/agents/conversation_context.py`:

```python
"""Conversation context for multi-turn awareness.

Tracks current topic, rolling summary, and session
metadata. In-memory store with TTL-based eviction.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """Per-session conversation state."""

    session_id: str
    current_topic: str = ""
    last_agent: str = ""
    last_intent: str = ""
    summary: str = ""
    tickers_mentioned: list[str] = field(
        default_factory=list,
    )
    user_tickers: list[str] = field(
        default_factory=list,
    )
    market_preference: str = ""
    subscription_tier: str = ""
    turn_count: int = 0
    last_updated: float = 0.0


class ConversationContextStore:
    """Thread-safe in-memory store with TTL eviction."""

    def __init__(self, ttl: int = 3600) -> None:
        self._store: dict[str, ConversationContext] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def get(
        self, session_id: str,
    ) -> ConversationContext | None:
        with self._lock:
            ctx = self._store.get(session_id)
            if ctx is None:
                return None
            age = time.time() - ctx.last_updated
            if age > self._ttl:
                del self._store[session_id]
                return None
            return ctx

    def upsert(
        self,
        session_id: str,
        ctx: ConversationContext,
    ) -> None:
        with self._lock:
            ctx.last_updated = time.time()
            self._store[session_id] = ctx

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                k for k, v in self._store.items()
                if now - v.last_updated > self._ttl
            ]
            for k in expired:
                del self._store[k]
            if expired:
                _logger.debug(
                    "Evicted %d expired contexts",
                    len(expired),
                )


# Module-level singleton.
context_store = ConversationContextStore()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/backend/test_conversation_context.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agents/conversation_context.py tests/backend/test_conversation_context.py
git commit -m "feat: ConversationContext data model + in-memory store

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Topic Classifier — Follow-up Detection

**Files:**
- Create: `backend/agents/nodes/topic_classifier.py`
- Test: `tests/backend/test_topic_classifier.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for topic classifier — follow-up detection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.conversation_context import (
    ConversationContext,
)
from agents.nodes.topic_classifier import (
    classify_followup,
)


class TestClassifyFollowup:
    def test_no_context_returns_new_topic(self):
        result = classify_followup(
            "What is AAPL price?", None,
        )
        assert result == "new_topic"

    def test_first_turn_returns_new_topic(self):
        ctx = ConversationContext(session_id="s1")
        ctx.turn_count = 0
        result = classify_followup(
            "What is AAPL price?", ctx,
        )
        assert result == "new_topic"

    @patch(
        "agents.nodes.topic_classifier._classify_via_llm",
    )
    def test_follow_up_detected(self, mock_llm):
        mock_llm.return_value = "follow_up"
        ctx = ConversationContext(session_id="s1")
        ctx.turn_count = 2
        ctx.summary = "Discussed AAPL sentiment."
        ctx.current_topic = "AAPL sentiment"
        result = classify_followup(
            "And what about the forecast?", ctx,
        )
        assert result == "follow_up"

    @patch(
        "agents.nodes.topic_classifier._classify_via_llm",
    )
    def test_new_topic_detected(self, mock_llm):
        mock_llm.return_value = "new_topic"
        ctx = ConversationContext(session_id="s1")
        ctx.turn_count = 3
        ctx.summary = "Discussed AAPL sentiment."
        ctx.current_topic = "AAPL sentiment"
        result = classify_followup(
            "Show me my portfolio", ctx,
        )
        assert result == "new_topic"

    @patch(
        "agents.nodes.topic_classifier._classify_via_llm",
    )
    def test_llm_failure_defaults_new_topic(
        self, mock_llm,
    ):
        mock_llm.side_effect = Exception("LLM down")
        ctx = ConversationContext(session_id="s1")
        ctx.turn_count = 2
        ctx.summary = "Discussed AAPL."
        result = classify_followup(
            "And dividends?", ctx,
        )
        assert result == "new_topic"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/backend/test_topic_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement topic classifier**

Create `backend/agents/nodes/topic_classifier.py`:

```python
"""Follow-up vs new-topic classification.

Uses a 1-shot LLM prompt to decide if the user's
message is a continuation of the current topic or
a brand-new question.
"""

from __future__ import annotations

import logging

from agents.conversation_context import (
    ConversationContext,
)

_logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = (
    "Given the conversation summary and the new "
    "user message, classify as 'follow_up' or "
    "'new_topic'.\n\n"
    "Summary: {summary}\n"
    "Last topic: {topic}\n"
    "New message: {message}\n\n"
    "Answer with ONLY 'follow_up' or 'new_topic'."
)


def classify_followup(
    user_input: str,
    ctx: ConversationContext | None,
) -> str:
    """Classify user message as follow-up or new topic.

    Returns ``"new_topic"`` if no context, first turn,
    or classification fails.
    """
    if ctx is None or ctx.turn_count == 0:
        return "new_topic"

    try:
        return _classify_via_llm(user_input, ctx)
    except Exception:
        _logger.debug(
            "Topic classification failed, "
            "defaulting to new_topic",
            exc_info=True,
        )
        return "new_topic"


def _classify_via_llm(
    user_input: str,
    ctx: ConversationContext,
) -> str:
    """Call LLM to classify follow-up vs new topic."""
    from llm_fallback import FallbackLLM
    from langchain_core.messages import HumanMessage

    prompt = _CLASSIFY_PROMPT.format(
        summary=ctx.summary or "No previous context.",
        topic=ctx.current_topic or "None",
        message=user_input,
    )

    llm = FallbackLLM(max_tiers=2)
    result = llm.invoke([HumanMessage(content=prompt)])
    text = (
        result.content
        if hasattr(result, "content")
        else str(result)
    ).strip().lower()

    if "follow" in text:
        return "follow_up"
    return "new_topic"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/backend/test_topic_classifier.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agents/nodes/topic_classifier.py tests/backend/test_topic_classifier.py
git commit -m "feat: topic classifier — follow-up vs new-topic detection

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Rolling Summary Generator

**Files:**
- Add to: `backend/agents/conversation_context.py`
- Test: `tests/backend/test_conversation_context.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/backend/test_conversation_context.py`:

```python
from unittest.mock import MagicMock, patch


class TestUpdateSummary:
    @patch(
        "agents.conversation_context._get_summary_llm",
    )
    def test_updates_summary(self, mock_get_llm):
        from agents.conversation_context import (
            update_summary,
        )

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content="User asked about AAPL sentiment. "
            "Score was bullish at 0.62.",
        )
        mock_get_llm.return_value = mock_llm

        ctx = ConversationContext(session_id="s1")
        update_summary(
            ctx,
            user_input="What is AAPL sentiment?",
            response="Sentiment is bullish, score 0.62",
        )
        assert "AAPL" in ctx.summary
        assert ctx.turn_count == 1

    @patch(
        "agents.conversation_context._get_summary_llm",
    )
    def test_llm_failure_keeps_old_summary(
        self, mock_get_llm,
    ):
        from agents.conversation_context import (
            update_summary,
        )

        mock_get_llm.return_value = None

        ctx = ConversationContext(session_id="s1")
        ctx.summary = "Previous summary"
        update_summary(ctx, "test", "test")
        assert ctx.summary == "Previous summary"
        assert ctx.turn_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/backend/test_conversation_context.py::TestUpdateSummary -v`
Expected: FAIL — `ImportError: cannot import name 'update_summary'`

- [ ] **Step 3: Implement summary generator**

Add to `backend/agents/conversation_context.py`:

```python
_SUMMARY_PROMPT = (
    "Update this conversation summary given the "
    "latest exchange. Keep it under 3 sentences. "
    "Include: topic discussed, key tickers/numbers "
    "mentioned, and any conclusions.\n\n"
    "Previous summary: {prev}\n"
    "User asked: {user_input}\n"
    "Assistant answered: {response}\n\n"
    "Updated summary:"
)


def _get_summary_llm():
    """Get cheapest available LLM for summarization.

    Cascade: Ollama → Groq scout → Groq versatile.
    Returns None if all unavailable.
    """
    try:
        from llm_fallback import FallbackLLM
        return FallbackLLM(max_tiers=2, ollama_first=True)
    except Exception:
        return None


def update_summary(
    ctx: ConversationContext,
    user_input: str,
    response: str,
) -> None:
    """Update rolling summary in-place.

    Increments turn_count regardless of LLM availability.
    """
    ctx.turn_count += 1

    llm = _get_summary_llm()
    if llm is None:
        _logger.debug("No LLM for summary update")
        return

    from langchain_core.messages import HumanMessage

    prompt = _SUMMARY_PROMPT.format(
        prev=ctx.summary or "No previous context.",
        user_input=user_input[:300],
        response=response[:500],
    )

    try:
        result = llm.invoke(
            [HumanMessage(content=prompt)],
        )
        text = (
            result.content
            if hasattr(result, "content")
            else str(result)
        ).strip()
        if text:
            ctx.summary = text
    except Exception:
        _logger.debug(
            "Summary update failed",
            exc_info=True,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/backend/test_conversation_context.py -v`
Expected: All 9 tests PASS (7 from Task 1 + 2 new)

- [ ] **Step 5: Commit**

```bash
git add backend/agents/conversation_context.py tests/backend/test_conversation_context.py
git commit -m "feat: rolling summary generator via Ollama/Groq cascade

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Integrate into Guardrail Node

**Files:**
- Modify: `backend/agents/nodes/guardrail.py:79-151`
- Modify: `backend/agents/graph_state.py:35-63`
- Modify: `backend/models.py:28-37`

- [ ] **Step 1: Add `session_id` to ChatRequest**

In `backend/models.py`, add after `user_id` field (line 37):

```python
    session_id: str | None = Field(
        default=None,
        description="Session ID for context tracking.",
    )
```

- [ ] **Step 2: Add `session_id` to AgentState**

In `backend/agents/graph_state.py`, add to `__annotations__` dict after `"user_id": str` (line 41):

```python
        "session_id": str,
```

- [ ] **Step 3: Pass `session_id` through graph input**

In `backend/routes.py`, modify `_build_graph_input()` (line 491-507) to include:

```python
            "session_id": req.session_id or "",
```

Add this line after `"user_id": req.user_id or "",` (line 494).

- [ ] **Step 4: Add follow-up detection to guardrail**

In `backend/agents/nodes/guardrail.py`, modify the `guardrail()` function. Add after the cache check block (after line 107) and before content safety (line 110):

```python
    # ── Follow-up detection ────────────────────────
    session_id = state.get("session_id", "")
    _followup_result = "new_topic"
    if session_id:
        from agents.conversation_context import (
            context_store,
        )
        from agents.nodes.topic_classifier import (
            classify_followup,
        )

        _ctx = context_store.get(session_id)
        _followup_result = classify_followup(
            user_input, _ctx,
        )
        if _followup_result == "follow_up" and _ctx:
            _logger.debug(
                "Follow-up detected for session %s"
                " — reusing agent=%s intent=%s",
                session_id,
                _ctx.last_agent,
                _ctx.last_intent,
            )
```

Then modify the final return (line 147-151) to include follow-up routing:

```python
    # If follow-up with known agent, skip router.
    if (
        _followup_result == "follow_up"
        and _ctx
        and _ctx.last_agent
    ):
        return {
            "tickers": tickers or _ctx.tickers_mentioned,
            "next_agent": _ctx.last_agent,
            "intent": _ctx.last_intent,
            "start_time_ns": start_ns,
        }

    return {
        "tickers": tickers,
        "next_agent": "router",
        "start_time_ns": start_ns,
    }
```

- [ ] **Step 5: Run existing guardrail tests**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/backend/ -k "guardrail" -v`
Expected: Existing tests still pass (new code only activates when session_id is present)

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/agents/graph_state.py backend/agents/nodes/guardrail.py backend/routes.py
git commit -m "feat: integrate follow-up detection into guardrail node

Adds session_id to ChatRequest, AgentState, and graph input.
Guardrail classifies follow-ups and reuses last agent.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Context Injection into System Prompt

**Files:**
- Modify: `backend/agents/base.py:192-216`

- [ ] **Step 1: Modify `_build_messages` to inject context**

In `backend/agents/base.py`, modify `_build_messages()` (line 192-216):

```python
    def _build_messages(
        self,
        user_input: str,
        history: list[dict],
        session_id: str = "",
    ) -> list[BaseMessage]:
        messages: list[BaseMessage] = []

        # Build system prompt with context injection.
        system = self.config.system_prompt or ""
        if session_id:
            from agents.conversation_context import (
                context_store,
            )

            ctx = context_store.get(session_id)
            if ctx and ctx.summary:
                context_block = (
                    "[Conversation Context]\n"
                    f"Turn {ctx.turn_count} of an "
                    "ongoing conversation.\n"
                    f"Summary: {ctx.summary}\n"
                    f"Current topic: "
                    f"{ctx.current_topic}\n"
                )
                if ctx.user_tickers:
                    tickers = ", ".join(
                        ctx.user_tickers,
                    )
                    context_block += (
                        f"User portfolio: {tickers}\n"
                    )
                if ctx.market_preference:
                    context_block += (
                        f"Market: "
                        f"{ctx.market_preference}\n"
                    )
                context_block += "\n---\n"
                system = context_block + system

        if system:
            messages.append(
                SystemMessage(content=system),
            )

        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                messages.append(
                    HumanMessage(content=content),
                )
            elif role == "assistant":
                messages.append(
                    AIMessage(content=content),
                )
        messages.append(
            HumanMessage(content=user_input),
        )
        return messages
```

- [ ] **Step 2: Update callers to pass session_id**

Grep for `_build_messages(` in `backend/agents/` and update callers to pass `session_id` from the agent's state or kwargs. The primary caller is `run()` in `base.py` — add `session_id=""` parameter and pass through.

- [ ] **Step 3: Run full test suite**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/ --tb=short -q`
Expected: All tests pass (new parameter has default `""` — backward compatible)

- [ ] **Step 4: Commit**

```bash
git add backend/agents/base.py
git commit -m "feat: inject conversation context into agent system prompts

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Post-Response Context Update

**Files:**
- Modify: `backend/routes.py:509-570`

- [ ] **Step 1: Add post-response context update to LangGraph stream**

In `backend/routes.py`, modify `_stream_langgraph()`. After the `graph.invoke()` call completes (around line 529-544), add context update logic before the final event is emitted:

```python
                    # Update conversation context.
                    _update_conversation_context(
                        session_id=req.session_id or "",
                        user_input=req.message,
                        response=result.get(
                            "final_response", "",
                        ),
                        agent=result.get(
                            "current_agent", "",
                        ),
                        intent=result.get("intent", ""),
                        tickers=result.get("tickers", []),
                        user_id=req.user_id or "",
                    )
```

- [ ] **Step 2: Implement `_update_conversation_context` helper**

Add this function in `routes.py` near the other helpers:

```python
def _update_conversation_context(
    session_id: str,
    user_input: str,
    response: str,
    agent: str,
    intent: str,
    tickers: list[str],
    user_id: str,
) -> None:
    """Update or create conversation context."""
    if not session_id:
        return

    from agents.conversation_context import (
        ConversationContext,
        context_store,
        update_summary,
    )

    ctx = context_store.get(session_id)
    if ctx is None:
        ctx = ConversationContext(
            session_id=session_id,
        )
        # Populate user profile on first turn.
        try:
            user_ctx = _build_user_context(user_id)
            ctx.user_tickers = user_ctx.get(
                "tickers", [],
            )
            ctx.market_preference = user_ctx.get(
                "market", "",
            )
            ctx.subscription_tier = user_ctx.get(
                "tier", "",
            )
        except Exception:
            pass

    ctx.last_agent = agent
    ctx.last_intent = intent
    ctx.current_topic = (
        f"{', '.join(tickers)} {intent}"
        if tickers else intent
    )
    for t in tickers:
        if t not in ctx.tickers_mentioned:
            ctx.tickers_mentioned.append(t)

    # Update summary (async-tolerant, non-blocking).
    try:
        update_summary(ctx, user_input, response)
    except Exception:
        ctx.turn_count += 1

    context_store.upsert(session_id, ctx)
```

- [ ] **Step 3: Run full test suite**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/ --tb=short -q`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add backend/routes.py
git commit -m "feat: post-response context update with rolling summary

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Frontend — Pass session_id

**Files:**
- Modify: `frontend/hooks/useSendMessage.ts:140-145`

- [ ] **Step 1: Add session_id to chat request body**

In `frontend/hooks/useSendMessage.ts`, modify the request body (line 140-145):

```typescript
          body: JSON.stringify({
            message: userMessage.content,
            history: messages.map((m) => ({
              role: m.role,
              content: m.content,
            })),
            agent_id: agentId,
            user_id: getUserIdFromToken(),
            session_id: sessionId,
          }),
```

The `sessionId` comes from the `ChatProvider` context. Check if `useSendMessage` already has access to it. If not, add it as a parameter from the provider.

- [ ] **Step 2: Verify sessionId is available in the hook**

Check that the `useSendMessage` hook receives `sessionId` from `ChatProvider.tsx`. The provider already exposes `sessionId` (line 66 in `ChatProvider.tsx`). Ensure the hook destructures it or receives it as a prop.

- [ ] **Step 3: Test manually**

1. Open the app at `http://localhost:3000`
2. Open browser DevTools → Network tab
3. Send a chat message
4. Verify the request body includes `session_id: "<uuid>"`

- [ ] **Step 4: Commit**

```bash
git add frontend/hooks/useSendMessage.ts
git commit -m "feat: pass session_id from frontend to backend chat API

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Integration Test + Full Verification

**Files:**
- Test: `tests/backend/test_context_integration.py`

- [ ] **Step 1: Write integration test**

```python
"""Integration test for multi-turn context awareness."""

from unittest.mock import MagicMock, patch

from agents.conversation_context import (
    ConversationContext,
    ConversationContextStore,
    update_summary,
)
from agents.nodes.topic_classifier import (
    classify_followup,
)


class TestMultiTurnFlow:
    """Simulate a 3-turn conversation."""

    def test_full_flow(self):
        store = ConversationContextStore()

        # Turn 1: New topic.
        ctx = ConversationContext(session_id="s1")
        result = classify_followup("AAPL price?", ctx)
        assert result == "new_topic"

        ctx.current_topic = "AAPL price"
        ctx.last_agent = "stock_analyst"
        ctx.last_intent = "stock_analysis"
        ctx.tickers_mentioned = ["AAPL"]
        ctx.turn_count = 1
        ctx.summary = "User asked about AAPL price."
        store.upsert("s1", ctx)

        # Turn 2: Follow-up.
        ctx2 = store.get("s1")
        with patch(
            "agents.nodes.topic_classifier"
            "._classify_via_llm",
            return_value="follow_up",
        ):
            result2 = classify_followup(
                "And the forecast?", ctx2,
            )
        assert result2 == "follow_up"
        assert ctx2.last_agent == "stock_analyst"

        # Turn 3: New topic.
        ctx3 = store.get("s1")
        with patch(
            "agents.nodes.topic_classifier"
            "._classify_via_llm",
            return_value="new_topic",
        ):
            result3 = classify_followup(
                "Show my portfolio", ctx3,
            )
        assert result3 == "new_topic"
```

- [ ] **Step 2: Run integration test**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/backend/test_context_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest tests/ --tb=short -q`
Expected: All 690+ tests pass (685 existing + new context tests)

- [ ] **Step 4: Manual E2E test**

1. Open `http://localhost:3000`, log in
2. Ask: "What is the sentiment for RELIANCE.NS?"
3. Wait for response
4. Ask: "And what about the forecast?"
5. Verify: agent uses RELIANCE.NS context (not treated as standalone)
6. Ask: "Show me my portfolio"
7. Verify: routes to portfolio agent (new topic detected)
8. Check backend logs: `docker compose logs backend --since 5m | grep "Follow-up\|context\|summary"`

- [ ] **Step 5: Final commit**

```bash
git add tests/backend/test_context_integration.py
git commit -m "test: multi-turn context awareness integration test

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Verification Checklist

- [ ] All unit tests pass (`pytest tests/ -q`)
- [ ] `ConversationContext` store TTL eviction works
- [ ] Topic classifier detects follow-ups correctly
- [ ] Rolling summary updates after each turn
- [ ] Summary injected into system prompt (visible in logs)
- [ ] Frontend sends `session_id` in request body
- [ ] Follow-up routes to same agent (skip router)
- [ ] New topic routes normally
- [ ] Ollama unavailable → graceful degradation (no crash)
- [ ] No performance regression (LHCI scores unchanged)
