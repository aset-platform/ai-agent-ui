"""Synchronous agentic loop execution.

:func:`run` implements the tool-calling loop shared by all
:class:`~agents.base.BaseAgent` subclasses.

Functions
---------
- :func:`run`
"""

import logging
from typing import TYPE_CHECKING, Dict, List

from agents.config import MAX_ITERATIONS
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from agents.base import BaseAgent

# Module-level logger for this module; not a mutable data global.
_logger = logging.getLogger(__name__)


def run(agent: "BaseAgent", user_input: str, history: List[Dict]) -> str:
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
    agent.logger.info(
        "Request start | agent=%s | input_len=%d",
        agent.config.agent_id,
        len(user_input),
    )
    messages = agent._build_messages(user_input, history)
    iteration = 0
    response = None

    try:
        while True:
            iteration += 1
            if iteration > MAX_ITERATIONS:
                agent.logger.warning(
                    "Agent '%s' hit MAX_ITERATIONS (%d). Returning last response.",
                    agent.config.agent_id,
                    MAX_ITERATIONS,
                )
                break
            agent.logger.debug(
                "Iteration %d | message_count=%d", iteration, len(messages)
            )
            response = agent.llm_with_tools.invoke(messages)
            messages.append(response)

            if not response.tool_calls:
                break

            tool_names_called = [tc["name"] for tc in response.tool_calls]
            agent.logger.info("Tools called: %s", tool_names_called)

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("args", {})
                agent.logger.debug("Tool args | %s: %s", tool_name, tool_args)
                result = agent.tool_registry.invoke(tool_name, tool_args)
                agent.logger.debug(
                    "Tool result | %s: %s", tool_name, result[:300]
                )
                messages.append(
                    ToolMessage(content=result, tool_call_id=tc["id"])
                )

    except Exception:
        agent.logger.error("Agent run failed", exc_info=True)
        raise

    agent.logger.info(
        "Request end | agent=%s | iterations=%d",
        agent.config.agent_id,
        iteration,
    )
    return (
        (response.content or "No response")
        if response is not None
        else "No response"
    )
