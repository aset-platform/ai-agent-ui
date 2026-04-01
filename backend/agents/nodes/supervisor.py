"""Supervisor node — intent-to-agent mapping.

Simple lookup that maps the classified intent to a
sub-agent node name.  No LLM call — pure dict mapping.
"""

from __future__ import annotations

_AGENT_MAP: dict[str, str] = {
    "portfolio": "portfolio",
    "stock_analysis": "stock_analyst",
    "forecast": "forecaster",
    "research": "research",
}


def supervisor(state: dict) -> dict:
    """Route to the sub-agent matching intent.

    Falls back to ``stock_analyst`` for unknown intents.
    """
    intent = state.get("intent", "")
    next_agent = _AGENT_MAP.get(
        intent, "stock_analyst",
    )
    return {"next_agent": next_agent}
