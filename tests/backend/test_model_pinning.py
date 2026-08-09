"""Tests for per-request model pinning in FallbackLLM."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from llm_fallback import FallbackLLM


def _make_fallback(
    groq_models: list[str] | None = None,
) -> "FallbackLLM":
    """Build a minimal FallbackLLM with mocked deps."""
    from llm_fallback import FallbackLLM

    budget = MagicMock()
    budget.register_pool = MagicMock(
        return_value=MagicMock(
            name="pool-0",
            models=groq_models or [],
        ),
    )
    compressor = MagicMock()

    with patch(
        "llm_fallback.ChatAnthropic",
    ):
        llm = FallbackLLM(
            groq_models=groq_models or [],
            anthropic_model=None,
            temperature=0.0,
            agent_id="test",
            token_budget=budget,
            compressor=compressor,
        )
    return llm


class TestPinnedModelStartsNone:
    """_pinned_model should be None on construction."""

    def test_pinned_model_starts_none(self) -> None:
        llm = _make_fallback()
        assert llm._pinned_model is None


class TestPinReset:
    """pin_reset() should clear _pinned_model."""

    def test_pin_reset_clears_pinned_model(self) -> None:
        llm = _make_fallback()
        llm._pinned_model = "some-model"
        llm.pin_reset()
        assert llm._pinned_model is None


class TestPinSetAfterInvoke:
    """After a successful invoke, _pinned_model is set."""

    @patch("llm_fallback.FallbackLLM._try_model")
    def test_pin_set_after_first_invoke(
        self,
        mock_try: MagicMock,
    ) -> None:
        """Compression/token-estimation are injected dependencies
        (self._compressor.compress / self._budget.estimate_tokens),
        not FallbackLLM methods — configure the mocks _make_fallback
        already wires up rather than patching removed methods."""
        llm = _make_fallback(["model-a", "model-b"])
        # Fake the lookup and tiers so invoke can find
        # a model to try.
        llm._model_lookup = {
            "model-a": ("model-a", MagicMock(), MagicMock()),
            "model-b": ("model-b", MagicMock(), MagicMock()),
        }
        llm._pool_groups = []
        llm._compressor.compress.return_value = [
            {"role": "user", "content": "hi"},
        ]
        llm._budget.estimate_tokens.return_value = 100
        mock_try.return_value = MagicMock(content="ok")

        llm.invoke([{"role": "user", "content": "hi"}])
        assert llm._pinned_model is not None


class TestPinnedModelReused:
    """With _pinned_model set, invoke uses that model."""

    @patch("llm_fallback.FallbackLLM._try_model")
    def test_pinned_model_reused_on_second_invoke(
        self,
        mock_try: MagicMock,
    ) -> None:
        llm = _make_fallback(["model-a", "model-b"])
        llm._model_lookup = {
            "model-a": ("model-a", MagicMock(), MagicMock()),
            "model-b": ("model-b", MagicMock(), MagicMock()),
        }
        llm._pool_groups = []
        llm._pinned_model = "model-a"

        llm._compressor.compress.return_value = [
            {"role": "user", "content": "hi"},
        ]
        llm._budget.estimate_tokens.return_value = 100
        mock_try.return_value = MagicMock(content="ok")

        llm.invoke([{"role": "user", "content": "hi"}])
        # _try_model called with pinned model name.
        first_call = mock_try.call_args_list[0]
        assert first_call[0][0] == "model-a"
        # Pin should remain set.
        assert llm._pinned_model == "model-a"


class TestPinClearedOnBudgetExhaust:
    """If pinned model fails, pin is cleared."""

    @patch("llm_fallback.FallbackLLM._try_model")
    def test_pin_cleared_on_budget_exhaust(
        self,
        mock_try: MagicMock,
    ) -> None:
        llm = _make_fallback(["model-a", "model-b"])
        llm._model_lookup = {
            "model-a": ("model-a", MagicMock(), MagicMock()),
            "model-b": ("model-b", MagicMock(), MagicMock()),
        }
        llm._pool_groups = []
        llm._pinned_model = "model-a"

        llm._compressor.compress.return_value = [
            {"role": "user", "content": "hi"},
        ]
        llm._budget.estimate_tokens.return_value = 100
        # 1st call: pinned model-a (via the dedicated pin branch)
        # fails -> pin cleared. The cascade below does NOT skip
        # the just-unpinned model, so the 2nd call retries model-a
        # (also fails) before the 3rd call reaches model-b, which
        # succeeds and becomes the new pin.
        mock_try.side_effect = [
            None,
            None,
            MagicMock(content="ok"),
        ]

        llm.invoke([{"role": "user", "content": "hi"}])
        # Pin should have been cleared after the pinned
        # model returned None, then re-set to fallback.
        assert llm._pinned_model == "model-b"
