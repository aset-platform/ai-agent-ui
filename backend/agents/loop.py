"""Synchronous agentic loop execution.

:func:`run` implements the tool-calling loop shared by all
:class:`~agents.base.BaseAgent` subclasses.

Functions
---------
- :func:`run`
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List

from agents.config import MAX_ITERATIONS
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from agents.base import BaseAgent

# Module-level logger for this module; not a mutable data global.
_logger = logging.getLogger(__name__)


def run(
    agent: "BaseAgent",
    user_input: str,
    history: List[Dict],
    max_iterations: int | None = None,
) -> str:
    """Execute the agentic loop and return the LLM's final text response.

    The loop:
    1. Build the initial message list from ``history`` + ``user_input``.
    2. Invoke ``llm_with_tools``.
    3. If the response has tool calls, execute each via the registry,
       append a :class:`~langchain_core.messages.ToolMessage`, and repeat.
    4. When the response has no tool calls, return its ``content``.

    Args:
        agent: A :class:`~agents.base.BaseAgent` instance with ``config``,
            ``tool_registry``, ``llm_with_tools``, and ``logger`` attributes.
        user_input: The user's latest message.
        history: Prior conversation turns as raw dicts.

    Returns:
        The final natural-language response, or ``"No response"`` if the
        model returned empty content.

    Raises:
        Exception: Any exception raised by the LLM or tool execution is
            logged and re-raised.
    """
    _cap = max_iterations or MAX_ITERATIONS
    agent.logger.info(
        "Request start | agent=%s | input_len=%d",
        agent.config.agent_id,
        len(user_input),
    )
    messages = agent._build_messages(user_input, history)
    iteration = 0
    response = None
    _had_tool_calls = False
    _use_synthesis = (
        agent.llm_synthesis is not agent.llm_with_tools
    )

    try:
        while True:
            iteration += 1
            if iteration > _cap:
                agent.logger.warning(
                    "Agent '%s' hit max iterations (%d).",
                    agent.config.agent_id,
                    _cap,
                )
                break
            agent.logger.debug(
                "Iteration %d | message_count=%d",
                iteration,
                len(messages),
            )

            # If previous iteration had tool calls and we
            # have a separate synthesis cascade, use it for
            # this (likely final) iteration to avoid a
            # wasted tool-cascade call.
            llm = agent.llm_with_tools
            if _had_tool_calls and _use_synthesis:
                llm = agent.llm_synthesis
                agent.logger.debug(
                    "Using synthesis cascade"
                )

            response = llm.invoke(
                messages,
                iteration=iteration,
            )
            messages.append(response)

            if not response.tool_calls:
                break

            # Response has tool calls — stay on tool cascade
            # next iteration (reset synthesis choice).
            _had_tool_calls = True
            tool_names_called = [
                tc["name"] for tc in response.tool_calls
            ]
            agent.logger.info(
                "Tools called: %s", tool_names_called
            )

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("args", {})
                agent.logger.debug(
                    "Tool args | %s: %s",
                    tool_name,
                    tool_args,
                )
                result = agent.tool_registry.invoke(
                    tool_name, tool_args
                )
                agent.logger.debug(
                    "Tool result | %s: %s",
                    tool_name,
                    result[:300],
                )
                messages.append(
                    ToolMessage(
                        content=result,
                        tool_call_id=tc["id"],
                    )
                )

    except Exception:
        agent.logger.error(
            "Agent run failed", exc_info=True
        )
        raise

    agent.logger.info(
        "Request end | agent=%s | iterations=%d",
        agent.config.agent_id,
        iteration,
    )
    final_text = (
        (response.content or "No response")
        if response is not None
        else "No response"
    )

    # Post-process with report template if available.
    if hasattr(agent, "format_response"):
        final_text = agent.format_response(
            final_text, messages
        )

    return final_text
