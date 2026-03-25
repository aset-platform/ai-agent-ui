"""Unit tests for the N-tier FallbackLLM cascade.

Tests cover:
- Tier 1 succeeds — later tiers and Anthropic never called.
- Groq RateLimitError cascades through tiers to Anthropic.
- Groq APIConnectionError cascades to Anthropic.
- Groq APIStatusError (413) cascades to Anthropic.
- All providers fail — re-raises the Anthropic error.
- bind_tools() stores bound LLMs and returns ``self``.
- Budget exhaustion skips tier and tries next.
- Progressive compression shrinks messages to fit a tier.
- Progressive compression still too big — cascade continues.
- No Groq key — goes straight to Anthropic.
"""

from unittest.mock import MagicMock, patch

import pytest

# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


def _make_fallback(
    groq_mocks,
    anthropic_mock,
    *,
    budget_can_afford=True,
):
    """Build a FallbackLLM with patched inner LLMs.

    Args:
        groq_mocks: List of MagicMock instances, one per tier.
        anthropic_mock: MagicMock for Anthropic LLM.
        budget_can_afford: If True, budget always allows.
            If callable, used as ``can_afford`` side_effect.
    """
    import llm_fallback

    budget_mock = MagicMock()
    budget_mock.estimate_tokens.return_value = 100
    if callable(budget_can_afford):
        budget_mock.can_afford.side_effect = budget_can_afford
        budget_mock.reserve.side_effect = budget_can_afford
    else:
        budget_mock.can_afford.return_value = budget_can_afford
        budget_mock.reserve.return_value = budget_can_afford

    compressor_mock = MagicMock()
    compressor_mock.compress.side_effect = lambda msgs, *a, **kw: msgs

    groq_iter = iter(groq_mocks)

    model_names = [f"model-tier-{i}" for i in range(len(groq_mocks))]

    with (
        patch.object(
            llm_fallback,
            "ChatGroq",
            side_effect=lambda **kw: next(groq_iter),
        ),
        patch.object(
            llm_fallback,
            "ChatAnthropic",
            return_value=anthropic_mock,
        ),
        patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}),
    ):
        llm = llm_fallback.FallbackLLM(
            groq_models=model_names,
            anthropic_model="claude-sonnet-4-6",
            temperature=0.0,
            agent_id="test",
            token_budget=budget_mock,
            compressor=compressor_mock,
        )
    return llm


# ------------------------------------------------------------------
# Tests — Primary path
# ------------------------------------------------------------------


class TestTier1Succeeds:
    """Tier 1 succeeds — no cascade needed."""

    def test_returns_tier1_response(self):
        """First Groq tier responds, others untouched."""
        t1 = MagicMock()
        t2 = MagicMock()
        anth = MagicMock()
        t1.invoke.return_value = "tier1_ok"

        llm = _make_fallback([t1, t2], anth)
        result = llm.invoke("hello")

        assert result == "tier1_ok"
        t1.invoke.assert_called_once()
        t2.invoke.assert_not_called()
        anth.invoke.assert_not_called()


# ------------------------------------------------------------------
# Tests — Groq error cascading
# ------------------------------------------------------------------


class TestRateLimitCascade:
    """RateLimitError cascades through tiers to Anthropic."""

    def test_all_groq_rate_limited(self):
        """All Groq tiers rate-limited → Anthropic used."""
        from groq import RateLimitError

        t1 = MagicMock()
        t2 = MagicMock()
        anth = MagicMock()
        err = RateLimitError("rate limit", response=MagicMock(), body={})
        t1.invoke.side_effect = err
        t2.invoke.side_effect = err
        anth.invoke.return_value = "anthropic_ok"

        llm = _make_fallback([t1, t2], anth)
        result = llm.invoke("hello")

        assert result == "anthropic_ok"
        anth.invoke.assert_called_once()

    def test_tier1_fails_tier2_succeeds(self):
        """Tier 1 rate-limited, tier 2 succeeds."""
        from groq import RateLimitError

        t1 = MagicMock()
        t2 = MagicMock()
        anth = MagicMock()
        t1.invoke.side_effect = RateLimitError(
            "rate limit", response=MagicMock(), body={}
        )
        t2.invoke.return_value = "tier2_ok"

        llm = _make_fallback([t1, t2], anth)
        result = llm.invoke("hello")

        assert result == "tier2_ok"
        anth.invoke.assert_not_called()


class TestConnectionErrorCascade:
    """APIConnectionError cascades to Anthropic."""

    def test_connection_error_triggers_anthropic(self):
        """APIConnectionError skips all Groq tiers."""
        from groq import APIConnectionError

        t1 = MagicMock()
        anth = MagicMock()
        t1.invoke.side_effect = APIConnectionError(request=MagicMock())
        anth.invoke.return_value = "anthropic_ok"

        llm = _make_fallback([t1], anth)
        result = llm.invoke("hello")

        assert result == "anthropic_ok"


class TestAPIStatusErrorCascade:
    """APIStatusError (e.g. 413) cascades to next tier."""

    def test_413_cascades(self):
        """413 Request Too Large cascades to Anthropic."""
        from groq import APIStatusError

        t1 = MagicMock()
        anth = MagicMock()
        resp = MagicMock()
        resp.status_code = 413
        t1.invoke.side_effect = APIStatusError(
            "too large", response=resp, body={}
        )
        anth.invoke.return_value = "anthropic_ok"

        llm = _make_fallback([t1], anth)
        result = llm.invoke("hello")

        assert result == "anthropic_ok"


# ------------------------------------------------------------------
# Tests — Both fail
# ------------------------------------------------------------------


class TestAllProvidersFail:
    """All Groq tiers + Anthropic fail → re-raise."""

    def test_reraises_anthropic_error(self):
        """Anthropic error propagates when all fail."""
        from groq import RateLimitError

        t1 = MagicMock()
        anth = MagicMock()
        t1.invoke.side_effect = RateLimitError(
            "rate limit", response=MagicMock(), body={}
        )
        anth.invoke.side_effect = RuntimeError("anth_fail")

        llm = _make_fallback([t1], anth)

        with pytest.raises(RuntimeError, match="anth_fail"):
            llm.invoke("hello")


# ------------------------------------------------------------------
# Tests — bind_tools
# ------------------------------------------------------------------


class TestBindTools:
    """bind_tools() duck-types LangChain's interface."""

    def test_returns_self(self):
        """bind_tools() returns the FallbackLLM instance."""
        t1 = MagicMock()
        anth = MagicMock()
        t1.bind_tools.return_value = t1
        anth.bind_tools.return_value = anth

        llm = _make_fallback([t1], anth)
        result = llm.bind_tools([MagicMock()])

        assert result is llm

    def test_stores_bound_llms(self):
        """bind_tools() binds all inner LLMs."""
        t1_bound = MagicMock()
        t1 = MagicMock()
        anth_bound = MagicMock()
        anth = MagicMock()
        t1.bind_tools.return_value = t1_bound
        anth.bind_tools.return_value = anth_bound

        llm = _make_fallback([t1], anth)
        tools = [MagicMock()]
        llm.bind_tools(tools)

        t1.bind_tools.assert_called_once_with(tools)
        anth.bind_tools.assert_called_once_with(tools)
        assert llm._groq_tiers[0][2] is t1_bound
        assert llm._anthropic_bound is anth_bound


# ------------------------------------------------------------------
# Tests — Budget-aware routing
# ------------------------------------------------------------------


class TestBudgetSkip:
    """Budget exhaustion skips tiers."""

    def test_skips_unaffordable_tier(self):
        """Tier 1 unaffordable → tier 2 used."""
        t1 = MagicMock()
        t2 = MagicMock()
        anth = MagicMock()
        t1.invoke.return_value = "tier1"
        t2.invoke.return_value = "tier2"

        def _afford(model, est):
            return model != "model-tier-0"

        llm = _make_fallback([t1, t2], anth, budget_can_afford=_afford)
        result = llm.invoke("hello")

        assert result == "tier2"
        t1.invoke.assert_not_called()
        t2.invoke.assert_called_once()


class TestProgressiveCompression:
    """Progressive compression shrinks messages to fit."""

    def test_compression_enables_tier(self):
        """After progressive compress, tier becomes affordable."""
        t1 = MagicMock()
        anth = MagicMock()
        t1.invoke.return_value = "compressed_ok"

        afford_calls = {"n": 0}

        def _afford(model, est):
            if model == "model-tier-0":
                afford_calls["n"] += 1
                return afford_calls["n"] > 1
            return True

        llm = _make_fallback([t1], anth, budget_can_afford=_afford)
        llm._budget.get_tpm.return_value = 8000

        result = llm.invoke("hello")

        assert result == "compressed_ok"
        t1.invoke.assert_called_once()
        anth.invoke.assert_not_called()
        # Compressor called twice: default + progressive.
        assert llm._compressor.compress.call_count == 2
        second = llm._compressor.compress.call_args_list[1]
        assert second.kwargs.get("target_tokens") == 5600

    def test_compression_still_too_big_cascades(self):
        """If compression isn't enough, cascade continues."""
        t1 = MagicMock()
        anth = MagicMock()
        anth.invoke.return_value = "anthropic_ok"

        llm = _make_fallback([t1], anth, budget_can_afford=False)
        llm._budget.get_tpm.return_value = 8000

        result = llm.invoke("hello")

        assert result == "anthropic_ok"
        t1.invoke.assert_not_called()


# ------------------------------------------------------------------
# Tests — No Groq key
# ------------------------------------------------------------------


class TestNoGroqKey:
    """When GROQ_API_KEY is absent, Anthropic is used."""

    def test_straight_to_anthropic(self):
        """No Groq key → Anthropic directly."""
        import llm_fallback

        budget = MagicMock()
        budget.estimate_tokens.return_value = 100
        budget.can_afford.return_value = True
        budget.reserve.return_value = True
        comp = MagicMock()
        comp.compress.side_effect = lambda msgs, *a, **kw: msgs
        anth = MagicMock()
        anth.invoke.return_value = "anthropic_direct"

        with (
            patch.object(
                llm_fallback,
                "ChatAnthropic",
                return_value=anth,
            ),
            patch.dict("os.environ", {}, clear=True),
        ):
            llm = llm_fallback.FallbackLLM(
                groq_models=["some-model"],
                anthropic_model="claude-sonnet-4-6",
                temperature=0.0,
                agent_id="test",
                token_budget=budget,
                compressor=comp,
            )

        result = llm.invoke("hello")

        assert result == "anthropic_direct"
        assert len(llm._groq_tiers) == 0
