"""LangGraph state schema for the supervisor graph.

Defines the ``AgentState`` TypedDict that flows through every
node in the supervisor graph.  Fields use reducer annotations
so parallel branches merge cleanly.
"""

from __future__ import annotations

import operator
from typing import Annotated, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def _dedup_merge(
    a: list[str], b: list[str],
) -> list[str]:
    """Merge two lists, deduplicating entries."""
    return list(dict.fromkeys(a + b))


class AgentState(dict):
    """Shared state flowing through the supervisor graph.

    This is a TypedDict-style class that LangGraph uses to
    track conversation messages, routing decisions, tool
    events, and the final response.

    Reducer annotations control how fields merge when
    parallel branches converge.
    """

    __annotations__ = {
        # ── Core conversation ──────────────────────
        "messages": Annotated[
            Sequence[BaseMessage], add_messages
        ],
        "user_input": str,
        "user_id": str,
        "session_id": str,
        "history": list[dict],
        # ── Routing ────────────────────────────────
        "intent": str,
        "next_agent": str,
        "current_agent": str,
        # ── User context ──────────────────────────
        "user_context": dict,
        # ── Data context ───────────────────────────
        "tickers": list[str],
        "data_sources_used": Annotated[
            list[str], _dedup_merge
        ],
        "was_local_sufficient": bool,
        # ── Sub-agent results ──────────────────────
        "tool_events": Annotated[
            list[dict], operator.add
        ],
        "final_response": str,
        "response_actions": list[dict],
        "error": str | None,
        # ── Timing ─────────────────────────────────
        "start_time_ns": int,
    }
