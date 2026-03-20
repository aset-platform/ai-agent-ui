"""Streaming agentic loop execution (NDJSON event emitter).

:func:`stream` implements the streaming tool-calling loop shared by all
:class:`~agents.base.BaseAgent` subclasses.

Functions
---------
- :func:`stream`
"""

import json
import logging
from typing import TYPE_CHECKING, Dict, Iterator, List

from agents.config import MAX_ITERATIONS
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from agents.base import BaseAgent

# Module-level logger; mutable but required for module-scope logging.
_logger = logging.getLogger(__name__)


def stream(
    agent: "BaseAgent", user_input: str, history: List[Dict]
) -> Iterator[str]:
    """Execute the agentic loop, yielding NDJSON status events.

    Each yielded value is a JSON-encoded object followed by ``\\n``.

    Event types:

    - ``thinking`` — LLM invocation starting; includes ``iteration``.
    - ``tool_start`` — tool about to execute; includes ``tool`` and ``args``.
    - ``tool_done`` — tool completed; includes ``tool`` and ``preview``.
    - ``warning`` — ``MAX_ITERATIONS`` reached.
    - ``final`` — loop complete; includes ``response`` and ``iterations``.
    - ``error`` — exception occurred; includes ``message``.

    Args:
        agent: A :class:`~agents.base.BaseAgent` instance.
        user_input: The user's latest message.
        history: Prior conversation turns as raw dicts.

    Yields:
        JSON-encoded event strings, each terminated with ``\\n``.

    Raises:
        Exception: Any exception is logged, yielded as an ``error`` event,
            and then re-raised.
    """
    agent.logger.info(
        "Stream start | agent=%s | input_len=%d",
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
            if iteration > MAX_ITERATIONS:
                warning_msg = (
                    f"Agent hit MAX_ITERATIONS "
                    f"({MAX_ITERATIONS}); returning "
                    f"last response."
                )
                agent.logger.warning(
                    "Agent '%s' hit MAX_ITERATIONS (%d).",
                    agent.config.agent_id,
                    MAX_ITERATIONS,
                )
                yield json.dumps(
                    {
                        "type": "warning",
                        "message": warning_msg,
                    }
                ) + "\n"
                break

            yield json.dumps(
                {
                    "type": "thinking",
                    "iteration": iteration,
                }
            ) + "\n"

            # Use synthesis cascade after first tool round.
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

            _had_tool_calls = True
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("args", {})
                yield (
                    json.dumps(
                        {
                            "type": "tool_start",
                            "tool": tool_name,
                            "args": tool_args,
                        }
                    )
                    + "\n"
                )
                result = agent.tool_registry.invoke(
                    tool_name, tool_args
                )
                yield (
                    json.dumps(
                        {
                            "type": "tool_done",
                            "tool": tool_name,
                            "preview": result[:300],
                        }
                    )
                    + "\n"
                )
                messages.append(
                    ToolMessage(
                        content=result,
                        tool_call_id=tc["id"],
                    )
                )

    except Exception as exc:
        agent.logger.error("Agent stream failed", exc_info=True)
        yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
        raise

    final_response = (
        (response.content or "No response")
        if response is not None
        else "No response"
    )

    # Post-process with report template if available.
    if hasattr(agent, "format_response"):
        final_response = agent.format_response(
            final_response, messages
        )

    agent.logger.info(
        "Stream end | agent=%s | iterations=%d",
        agent.config.agent_id,
        iteration,
    )
    yield (
        json.dumps(
            {
                "type": "final",
                "response": final_response,
                "iterations": iteration,
            }
        )
        + "\n"
    )
