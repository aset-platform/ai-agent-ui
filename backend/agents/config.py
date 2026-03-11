"""AgentConfig dataclass and iteration limit constant.

Constants
---------
- :data:`MAX_ITERATIONS` — maximum agentic loop iterations before warning

Classes
-------
- :class:`AgentConfig` — immutable configuration bundle for a single agent
"""

from dataclasses import dataclass, field

MAX_ITERATIONS: int = 15


@dataclass
class AgentConfig:
    """Immutable configuration bundle for a single agent instance.

    Attributes:
        agent_id: Unique string identifier for routing and logging.
        name: Human-readable display name.
        description: One-sentence description exposed via ``GET /agents``.
        groq_model_tiers: Ordered list of Groq model names,
            tried first-to-last before the Anthropic fallback.
        temperature: Sampling temperature.  ``0.0`` produces deterministic
            outputs; higher values increase creativity.
        system_prompt: Optional system message prepended to every conversation.
        tool_names: Names of tools this agent is permitted to call.
    """

    agent_id: str
    name: str
    description: str
    groq_model_tiers: list[str] = field(
        default_factory=list,
    )
    temperature: float = 0.0
    system_prompt: str = ""
    tool_names: list[str] = field(default_factory=list)
