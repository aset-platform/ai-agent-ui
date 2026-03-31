"""Message list compression for Groq token budget management.

Reduces the token footprint of LangChain message lists through
three stages applied in order:

1. **System prompt tiering** — full prompt on iteration 1,
   condensed (~40 %) on iteration 2+.
2. **History truncation** — keep only the last *N* user/assistant
   turns from the conversation history portion.
3. **Tool result truncation** — cap ``ToolMessage.content`` at a
   configurable character limit.

Typical usage::

    from message_compressor import MessageCompressor

    compressor = MessageCompressor()
    compressed = compressor.compress(messages, iteration=2)
"""

import logging
import re

from langsmith import traceable

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

_logger = logging.getLogger(__name__)

# Regex patterns for lines to KEEP in condensed system prompts.
_KEEP_PATTERNS = re.compile(
    r"^\s*("
    r"\d+\."  # numbered steps
    r"|[-•]"  # bullet points
    r"|[A-Z]{2,}"  # ALL-CAPS keywords (RULES, PIPELINE, etc.)
    r"|You are"  # identity line
    r"|STANDARD"  # pipeline headers
    r"|COMPARISON"
    r"|RULES"
    r")",
)


class MessageCompressor:
    """Compress LangChain message lists to fit token budgets.

    All methods return **new** lists; the input is never mutated.

    Args:
        max_history_turns: Maximum user/assistant turn pairs to
            keep from the pre-loop conversation history.
        max_tool_result_chars: Maximum characters per
            ``ToolMessage.content``.
        condensed_prompt_ratio: Target ratio for condensed
            system prompt (0.0–1.0).  Used as a guideline, not
            a hard cap.
    """

    def __init__(
        self,
        max_history_turns: int = 3,
        max_tool_result_chars: int = 800,
        condensed_prompt_ratio: float = 0.4,
    ) -> None:
        self._max_history = max_history_turns
        self._max_tool_chars = max_tool_result_chars
        self._condensed_ratio = condensed_prompt_ratio

    @traceable(name="MessageCompressor.compress")
    def compress(
        self,
        messages: list[BaseMessage],
        iteration: int,
        target_tokens: int | None = None,
    ) -> list[BaseMessage]:
        """Return a compressed copy of *messages*.

        Args:
            messages: Full message list from the agentic loop.
            iteration: Current loop iteration (1-based).
            target_tokens: Optional budget target.  When set,
                progressively aggressive compression is applied
                until the estimate is under target.

        Returns:
            A new list of BaseMessage objects.
        """
        # Early exit: skip compression if well under budget.
        if target_tokens is not None:
            from token_budget import TokenBudget

            est = TokenBudget.estimate_tokens(messages)
            if est < target_tokens * 0.5:
                _logger.debug(
                    "Compression skipped: %d tokens " "< 50%% of %d budget",
                    est,
                    target_tokens,
                )
                return list(messages)

        result = list(messages)

        # Stage 1: condense system prompt on iteration 2+
        if iteration > 1:
            result = self._condense_system_prompt(result)

        # Stage 2: truncate history
        result = self._truncate_history(
            result,
            self._max_history,
        )

        # Stage 3: truncate tool results
        result = self._truncate_tool_results(
            result,
            self._max_tool_chars,
        )

        # Progressive compression if target_tokens is set
        if target_tokens is not None:
            result = self._progressive_compress(
                result,
                target_tokens,
            )

        return result

    # ------------------------------------------------------------------
    # Stage 1 — system prompt tiering
    # ------------------------------------------------------------------

    def _condense_system_prompt(
        self, messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        """Replace the SystemMessage with a condensed version.

        Keeps lines matching key structural patterns (numbered
        steps, bullets, ALL-CAPS headings) and drops verbose
        explanatory paragraphs.

        Args:
            messages: Message list (may or may not start with
                a SystemMessage).

        Returns:
            New list with condensed SystemMessage.
        """
        if not messages or not isinstance(messages[0], SystemMessage):
            return list(messages)

        original = messages[0].content or ""
        lines = original.split("\n")
        kept: list[str] = []
        for line in lines:
            if _KEEP_PATTERNS.match(line):
                kept.append(line)
            elif not line.strip():
                # Preserve blank lines for readability.
                if kept and kept[-1].strip():
                    kept.append("")

        condensed = "\n".join(kept).strip()
        if not condensed:
            # Fallback: keep the first 40 % of original.
            cut = int(len(original) * self._condensed_ratio)
            condensed = original[:cut].rsplit("\n", 1)[0]

        _logger.debug(
            "System prompt condensed: %d → %d chars",
            len(original),
            len(condensed),
        )
        return [SystemMessage(content=condensed)] + list(messages[1:])

    # ------------------------------------------------------------------
    # Stage 2 — history truncation
    # ------------------------------------------------------------------

    def _truncate_history(
        self,
        messages: list[BaseMessage],
        max_turns: int,
    ) -> list[BaseMessage]:
        """Keep system + last N history turns + loop messages.

        A "turn" is a ``(HumanMessage, AIMessage)`` pair from the
        pre-loop conversation history.  Loop-generated messages
        (from the current agentic request) are always preserved.

        The boundary between "history" and "loop" is the last
        ``HumanMessage`` that is NOT followed by a ``ToolMessage``
        within 2 positions — that marks the user's current input.

        Args:
            messages: Full message list.
            max_turns: Max history turn pairs to retain.

        Returns:
            New list with truncated history.
        """
        # Find the boundary: the last HumanMessage that starts
        # the current agentic request.  It's the last Human
        # message before any AIMessage-with-tool_calls or
        # ToolMessage sequence.
        boundary = self._find_loop_boundary(messages)
        if boundary <= 0:
            return list(messages)

        # Split into parts.
        prefix: list[BaseMessage] = []  # SystemMessage(s)
        history: list[BaseMessage] = []
        idx = 0

        # Collect prefix (SystemMessage).
        while idx < boundary and isinstance(messages[idx], SystemMessage):
            prefix.append(messages[idx])
            idx += 1

        # Collect history turns.
        history = list(messages[idx:boundary])

        # Loop messages (current request).
        loop_msgs = list(messages[boundary:])

        # Truncate history to last max_turns pairs.
        turns: list[list[BaseMessage]] = []
        current_turn: list[BaseMessage] = []
        for msg in history:
            current_turn.append(msg)
            if isinstance(msg, AIMessage):
                turns.append(current_turn)
                current_turn = []
        if current_turn:
            turns.append(current_turn)

        kept_turns = turns[-max_turns:] if max_turns > 0 else []
        kept_history: list[BaseMessage] = []
        for turn in kept_turns:
            kept_history.extend(turn)

        if len(history) != len(kept_history):
            _logger.debug(
                "History truncated: %d → %d messages " "(%d turns kept)",
                len(history),
                len(kept_history),
                len(kept_turns),
            )

        return prefix + kept_history + loop_msgs

    @staticmethod
    def _find_loop_boundary(
        messages: list[BaseMessage],
    ) -> int:
        """Find index of the HumanMessage starting the current request.

        Builds a single-pass index of message types, then uses
        the index to locate the boundary without multiple scans.

        Returns:
            Index of the boundary HumanMessage, or 0.
        """
        # Single-pass: collect indices by type.
        human_idxs: list[int] = []
        tool_idxs: list[int] = []
        for i, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                human_idxs.append(i)
            elif isinstance(msg, ToolMessage):
                tool_idxs.append(i)

        if not human_idxs or human_idxs[-1] <= 0:
            return 0

        last_human = human_idxs[-1]

        # Check if any ToolMessage exists after last human.
        has_tool_after = any(t > last_human for t in tool_idxs)
        if has_tool_after:
            return last_human

        # Check second-to-last human message.
        if len(human_idxs) >= 2:
            prev_human = human_idxs[-2]
            has_tool = any(t > prev_human for t in tool_idxs)
            if has_tool:
                return prev_human

        return last_human

    # ------------------------------------------------------------------
    # Stage 3 — tool result truncation
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_tool_results(
        messages: list[BaseMessage],
        max_chars: int,
    ) -> list[BaseMessage]:
        """Truncate ToolMessage content exceeding *max_chars*.

        Args:
            messages: Message list.
            max_chars: Max characters per ToolMessage.

        Returns:
            New list with truncated ToolMessages.
        """
        result: list[BaseMessage] = []
        for msg in messages:
            if (
                isinstance(msg, ToolMessage)
                and isinstance(msg.content, str)
                and len(msg.content) > max_chars
            ):
                original_len = len(msg.content)
                truncated = (
                    msg.content[:max_chars]
                    + f"\n... [truncated {original_len - max_chars}"
                    f" chars]"
                )
                result.append(
                    ToolMessage(
                        content=truncated,
                        tool_call_id=msg.tool_call_id,
                    )
                )
            else:
                result.append(msg)
        return result

    # ------------------------------------------------------------------
    # Progressive compression
    # ------------------------------------------------------------------

    def _progressive_compress(
        self,
        messages: list[BaseMessage],
        target_tokens: int,
    ) -> list[BaseMessage]:
        """Apply increasingly aggressive compression.

        Pass 1: already done (default stages).
        Pass 2: reduce history to 1 turn, tool results to 1000.
        Pass 3: 0 history turns, tool results to 500.

        Args:
            messages: Already-compressed message list.
            target_tokens: Token budget target.

        Returns:
            Further-compressed message list.
        """
        from token_budget import TokenBudget

        est = TokenBudget.estimate_tokens(messages)
        if est <= target_tokens:
            return messages

        # Pass 2
        result = self._truncate_history(messages, 1)
        result = self._truncate_tool_results(result, 500)
        est = TokenBudget.estimate_tokens(result)
        if est <= target_tokens:
            _logger.debug(
                "Progressive pass 2: %d tokens (target %d)",
                est,
                target_tokens,
            )
            return result

        # Pass 3
        result = self._truncate_history(result, 0)
        result = self._truncate_tool_results(result, 300)
        _logger.debug(
            "Progressive pass 3: %d tokens (target %d)",
            TokenBudget.estimate_tokens(result),
            target_tokens,
        )
        return result
