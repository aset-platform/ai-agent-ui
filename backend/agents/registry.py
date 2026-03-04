"""In-process registry that maps agent IDs to :class:`~agents.base.BaseAgent` instances.

:class:`AgentRegistry` is the runtime counterpart to
:class:`~tools.registry.ToolRegistry`.  It holds every agent that the server
has instantiated and provides the lookup used by the HTTP layer to dispatch
chat requests.

Adding a new agent to the system requires only:

1. Creating a concrete :class:`~agents.base.BaseAgent` subclass.
2. Calling :meth:`AgentRegistry.register` with an instance of that subclass
   during server startup.

No modifications to routing or request-handling code are necessary.

Typical usage::

    from agents.registry import AgentRegistry
    from agents.general_agent import create_general_agent

    registry = AgentRegistry()
    registry.register(create_general_agent(tool_registry))

    agent = registry.get("general")   # returns the GeneralAgent instance
    registry.list_agents()            # [{"id": "general", "name": ..., ...}]
"""

import logging
from typing import Optional

from agents.base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Thread-unsafe in-process store for named :class:`~agents.base.BaseAgent` instances.

    Agents are keyed by :attr:`~agents.base.AgentConfig.agent_id`.
    Registering an agent with a duplicate ID silently overwrites the
    previous entry.

    Attributes:
        _agents: Internal mapping from agent ID to agent instance.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Add an agent to the registry.

        Args:
            agent: A fully initialised :class:`~agents.base.BaseAgent`
                subclass instance.  The agent's
                :attr:`~agents.base.AgentConfig.agent_id` is used as the
                registry key.

        Example:
            >>> registry = AgentRegistry()
            >>> registry.register(create_general_agent(tool_registry))
            >>> "general" in [a["id"] for a in registry.list_agents()]
            True
        """
        self._agents[agent.config.agent_id] = agent
        logger.debug(
            "Registered agent: %s (%s)",
            agent.config.agent_id,
            agent.config.name,
        )

    def get(self, agent_id: str) -> Optional[BaseAgent]:
        """Look up a single agent by its ID.

        Logs a ``WARNING`` when the requested ID is not found so operators
        can detect misconfigured clients without raising an exception at
        the registry level.

        Args:
            agent_id: The exact agent ID string as registered, e.g.
                ``"general"``.

        Returns:
            The matching :class:`~agents.base.BaseAgent` instance, or
            ``None`` if no agent with that ID is registered.

        Example:
            >>> registry = AgentRegistry()
            >>> registry.get("nonexistent") is None
            True
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            logger.warning("Agent not found: %s", agent_id)
        return agent

    def list_agents(self) -> list[dict]:
        """Return a serialisable summary of every registered agent.

        The returned dicts are safe to include directly in a JSON HTTP
        response (used by the ``GET /agents`` endpoint).

        Returns:
            A list of dicts, each containing:

            - ``"id"`` (:class:`str`): The agent's unique identifier.
            - ``"name"`` (:class:`str`): Human-readable display name.
            - ``"description"`` (:class:`str`): One-sentence description.

        Example:
            >>> registry = AgentRegistry()
            >>> registry.register(create_general_agent(tool_registry))
            >>> registry.list_agents()
            [{'id': 'general', 'name': 'General Agent', 'description': '...'}]
        """
        return [
            {
                "id": a.config.agent_id,
                "name": a.config.name,
                "description": a.config.description,
            }
            for a in self._agents.values()
        ]
