# Per-Request Model Pinning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix model switching within ReAct agent loops so all iterations of a single user request use the same LLM, while preserving round-robin distribution across independent requests.

**Architecture:** Add `_pinned_model` state to `FallbackLLM` that locks model selection after the first successful invoke. The pin is reset per sub-agent node execution via `pin_reset()`. Two complementary prompt fixes improve period parsing and table preservation.

**Tech Stack:** Python, LangChain, FallbackLLM, LangGraph

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/llm_fallback.py` | Modify | Add `_pinned_model`, `pin_reset()`, pinned invoke path |
| `backend/agents/sub_agents.py` | Modify | Call `pin_reset()` before iteration loop |
| `backend/agents/configs/portfolio.py` | Modify | Add period parsing few-shot examples |
| `backend/agents/nodes/synthesis.py` | Modify | Add table preservation directive |
| `tests/backend/test_model_pinning.py` | Create | Tests for pinning behavior |

---

### Task 1: Model Pinning in FallbackLLM

**Files:**
- Modify: `backend/llm_fallback.py:246-268` (constructor), `backend/llm_fallback.py:623-671` (invoke pool routing)
- Create: `tests/backend/test_model_pinning.py`

- [ ] **Step 1: Write failing tests for pinning behavior**

Create `tests/backend/test_model_pinning.py`:

```python
"""Tests for per-request model pinning in FallbackLLM."""

import threading
from unittest.mock import MagicMock, patch

import pytest


class TestModelPinning:
    """FallbackLLM pins model across iterations."""

    def _make_fallback(self, models=None):
        """Build a minimal FallbackLLM with mocked deps."""
        from llm_fallback import FallbackLLM
        from token_budget import TokenBudget

        budget = TokenBudget()
        models = models or [
            "llama-3.3-70b-versatile",
            "qwen/qwen3-32b",
        ]
        pool_groups = [models]

        with patch(
            "llm_fallback.ChatAnthropic",
        ):
            llm = FallbackLLM(
                groq_models=models,
                anthropic_model=None,
                temperature=0,
                agent_id="test",
                token_budget=budget,
                compressor=MagicMock(),
                cascade_profile="tool",
                pool_groups=pool_groups,
            )
        return llm

    def test_pin_reset_clears_pinned_model(self):
        """pin_reset sets _pinned_model to None."""
        llm = self._make_fallback()
        llm._pinned_model = "some-model"
        llm.pin_reset()
        assert llm._pinned_model is None

    def test_pinned_model_starts_none(self):
        """New FallbackLLM has no pinned model."""
        llm = self._make_fallback()
        assert llm._pinned_model is None

    def test_pin_set_after_first_invoke(self):
        """After successful invoke, model is pinned."""
        llm = self._make_fallback()

        mock_response = MagicMock()
        mock_response.content = "hello"

        with patch.object(
            llm, "_try_model", return_value=mock_response,
        ):
            llm.invoke(
                [MagicMock()], iteration=1,
            )

        assert llm._pinned_model is not None

    def test_pinned_model_reused_on_second_invoke(self):
        """Second invoke reuses pinned model."""
        llm = self._make_fallback()
        llm._pinned_model = "llama-3.3-70b-versatile"

        mock_response = MagicMock()
        mock_response.content = "hello"

        with patch.object(
            llm, "_try_model", return_value=mock_response,
        ) as mock_try:
            llm.invoke(
                [MagicMock()], iteration=2,
            )

        # Should have been called with the pinned model
        call_args = mock_try.call_args
        assert call_args[0][0] == "llama-3.3-70b-versatile"

    def test_pin_cleared_on_budget_exhaust(self):
        """If pinned model fails, pin is cleared."""
        llm = self._make_fallback()
        llm._pinned_model = "llama-3.3-70b-versatile"

        # Pinned model fails, cascade succeeds
        def side_effect(model_name, *args, **kwargs):
            if model_name == "llama-3.3-70b-versatile":
                return None  # budget exhausted
            return MagicMock(content="ok")

        with patch.object(
            llm, "_try_model", side_effect=side_effect,
        ):
            llm.invoke(
                [MagicMock()], iteration=2,
            )

        # Pin should be updated to the model that
        # actually succeeded
        assert (
            llm._pinned_model
            != "llama-3.3-70b-versatile"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest tests/backend/test_model_pinning.py -v
```

Expected: FAIL (pin_reset, _pinned_model don't exist yet)

- [ ] **Step 3: Implement _pinned_model and pin_reset() in FallbackLLM**

In `backend/llm_fallback.py`, after line 268 (end of pool registration block), add:

```python
        # Per-request model pinning — prevents
        # round-robin rotation within a single ReAct
        # iteration loop.  Call pin_reset() before
        # each new request context.
        self._pinned_model: str | None = None
```

Add `pin_reset()` method after `bind_tools()` (after line 312):

```python
    def pin_reset(self) -> None:
        """Reset model pin for a new request context.

        Call this before each sub-agent iteration loop
        so the first ``invoke()`` selects a fresh model
        via round-robin, then subsequent iterations
        reuse it.
        """
        self._pinned_model = None
```

- [ ] **Step 4: Implement pinned invoke path**

In `backend/llm_fallback.py`, in the `invoke()` method, BEFORE the existing "Step 3: Try Groq tiers" block (line 623), insert:

```python
        # Step 2b: Try pinned model first (per-request
        # affinity).  Avoids round-robin rotation within
        # a single ReAct iteration loop.
        if self._pinned_model is not None:
            info = self._model_lookup.get(
                self._pinned_model,
            )
            if info is not None:
                _, _, bound_llm = info
                result = self._try_model(
                    self._pinned_model,
                    bound_llm,
                    compressed,
                    est,
                    messages,
                    iteration,
                    _tier_offset,
                    _trace_cbs,
                    _user,
                    **kwargs,
                )
                if result is not None:
                    return result
            # Pinned model failed (budget or error) —
            # clear pin and fall through to normal
            # cascade below.
            _logger.info(
                "Pinned model %s failed, "
                "unpinning (agent=%s)",
                self._pinned_model,
                self._agent_id,
            )
            self._pinned_model = None
```

Then modify the existing pool-aware routing to set the pin on success. In the block starting at line 627, after `if result is not None:` (line 650), add pin assignment before the return:

```python
                    if result is not None:
                        self._pinned_model = model_name
                        return result
```

Also add the same pin in the legacy flat path after line 670:

```python
                if result is not None:
                    self._pinned_model = model_name
                    return result
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest tests/backend/test_model_pinning.py -v
```

Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/llm_fallback.py tests/backend/test_model_pinning.py
git commit -m "feat: per-request model pinning in FallbackLLM (ASETPLTFRM-305)

Pin LLM model after first successful invoke within a ReAct loop.
Subsequent iterations reuse the same model without incrementing
the round-robin counter. Pin cleared on budget exhaustion.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 2: Wire pin_reset() in Sub-Agent Loop

**Files:**
- Modify: `backend/agents/sub_agents.py:288-292`

- [ ] **Step 1: Add pin_reset() call before iteration loop**

In `backend/agents/sub_agents.py`, after line 292 (`llm_with_tools = llm.bind_tools(tools)`), add:

```python
        # Pin model for this request — all iterations
        # use the same LLM via round-robin affinity.
        llm.pin_reset()
```

- [ ] **Step 2: Rebuild and verify in Docker logs**

```bash
./run.sh rebuild backend
```

Send a test chat message. Check logs — all iterations should show the same model:

```bash
docker compose logs backend --tail 30 | grep "Route →"
```

Expected: All `Route →` lines for the same agent show the same model name.

- [ ] **Step 3: Commit**

```bash
git add backend/agents/sub_agents.py
git commit -m "feat: call pin_reset() before ReAct iteration loop

Ensures all tool-calling iterations within a single user request
use the same model. Round-robin advances once per request, not
per iteration.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 3: Portfolio Prompt — Period Parsing Examples

**Files:**
- Modify: `backend/agents/configs/portfolio.py:31-32`

- [ ] **Step 1: Add period parsing examples to system prompt**

In `backend/agents/configs/portfolio.py`, after line 32 (the line ending with `"get_portfolio_comparison with both periods.\n"`), insert:

```python
    "  Period mapping:\n"
    "  - 'last week vs this week' → period1=\"2W\", "
    "period2=\"1W\"\n"
    "  - 'this month vs last month' → period1=\"2M\", "
    "period2=\"1M\"\n"
    "  - 'this week vs last 3 months' → "
    "period1=\"3M\", period2=\"1W\"\n"
    "  - Explicit dates → period1=\"2026-04-01:"
    "2026-04-07\", period2=\"2026-04-08:2026-04-14\"\n"
    "  - Single period → period1=\"1W\" (omit "
    "period2)\n"
```

- [ ] **Step 2: Verify prompt compiles**

```bash
docker compose exec backend python -c "from agents.configs.portfolio import PORTFOLIO_CONFIG; print(len(PORTFOLIO_CONFIG.system_prompt), 'chars')"
```

Expected: prints char count (no import error)

- [ ] **Step 3: Commit**

```bash
git add backend/agents/configs/portfolio.py
git commit -m "feat: add period parsing examples to portfolio agent prompt

Few-shot examples for 'last week vs this week' style queries so
the LLM correctly maps natural language to period1/period2 params.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 4: Synthesis Prompt — Table Preservation

**Files:**
- Modify: `backend/agents/nodes/synthesis.py:61-71`

- [ ] **Step 1: Add table preservation directive**

In `backend/agents/nodes/synthesis.py`, replace lines 61-71:

```python
_SYNTHESIS_PROMPT = (
    "You are a financial analyst on the ASET Platform. "
    "Synthesize a clear, actionable response from the "
    "data provided. Include specific numbers, dates, "
    "and actionable recommendations where applicable. "
    "Be concise but thorough.\n\n"
    "FORMAT: Use Markdown — **bold** for key figures, "
    "bullet points for lists, tables for comparisons, "
    "### headings for sections. Keep paragraphs short "
    "(2-3 sentences max).\n\n"
    "TABLE PRESERVATION: When the agent response "
    "contains markdown tables, comparison grids, or "
    "structured data — preserve them exactly. Do not "
    "collapse tables into prose. Add narrative context "
    "around tables but never remove or flatten them."
)
```

- [ ] **Step 2: Verify prompt compiles**

```bash
docker compose exec backend python -c "from agents.nodes.synthesis import _SYNTHESIS_PROMPT; print(len(_SYNTHESIS_PROMPT), 'chars')"
```

Expected: prints char count (no import error)

- [ ] **Step 3: Commit**

```bash
git add backend/agents/nodes/synthesis.py
git commit -m "feat: add table preservation directive to synthesis prompt

Prevents synthesis LLM from collapsing markdown comparison tables
into flat prose summaries.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 5: End-to-End Verification

- [ ] **Step 1: Rebuild backend**

```bash
./run.sh rebuild backend
sleep 8
curl -sf http://localhost:8181/v1/health
```

- [ ] **Step 2: Send portfolio comparison chat**

Send "compare my portfolio last week vs this week" via the chat UI or API. Check:

1. Backend logs: all `Route →` lines for `agent=portfolio` show the **same model**
2. `tool_start` event: `args` contain `period1="2W"` and `period2="1W"`
3. Final response: contains a markdown comparison table (not flat prose)

```bash
docker compose logs backend --tail 50 | grep -E "Route →|tool_start|tool_done"
```

- [ ] **Step 3: Verify round-robin still works across requests**

Send two independent chat requests. The first request should pin one model, the second request (after `pin_reset()`) should advance the round-robin to the next model.

- [ ] **Step 4: Run full test suite**

```bash
docker compose exec backend python -m pytest tests/ -k "fallback or pinning or tier" -v
```

- [ ] **Step 5: Lint**

```bash
flake8 backend/llm_fallback.py backend/agents/sub_agents.py backend/agents/configs/portfolio.py backend/agents/nodes/synthesis.py --max-line-length 79
```

- [ ] **Step 6: Push**

```bash
git push origin feature/sprint7
```
