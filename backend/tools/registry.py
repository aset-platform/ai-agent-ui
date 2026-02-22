"""In-process registry that maps tool names to LangChain :class:`~langchain_core.tools.BaseTool` instances.

:class:`ToolRegistry` acts as a service locator for all callable tools in the
system.  Agents do not import tool modules directly; instead they request a
list of :class:`~langchain_core.tools.BaseTool` objects by name, which are
bound to the LLM at agent setup time.

Separating tool registration from agent construction means new tools can be
added at runtime without modifying agent code, and the same tool instance can
be shared across multiple agents.

Typical usage::

    from tools.registry import ToolRegistry
    from tools.time_tool import get_current_time
    from tools.search_tool import search_web

    registry = ToolRegistry()
    registry.register(get_current_time)
    registry.register(search_web)

    # Retrieve a subset for a specific agent:
    tools = registry.get_tools(["get_current_time"])
"""

import logging
from typing import Optional

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Thread-unsafe in-process store for named LangChain tools.

    Tools are keyed by :attr:`~langchain_core.tools.BaseTool.name`.
    Registering a tool with a duplicate name silently overwrites the
    previous entry.

    Attributes:
        _tools: Internal mapping from tool name to tool instance.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Add a tool to the registry, indexed by its name.

        Args:
            tool: A LangChain :class:`~langchain_core.tools.BaseTool`
                instance.  The tool's ``name`` attribute is used as the
                registry key.

        Example:
            >>> from tools.time_tool import get_current_time
            >>> registry = ToolRegistry()
            >>> registry.register(get_current_time)
            >>> "get_current_time" in registry.list_names()
            True
        """
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def get(self, name: str) -> Optional[BaseTool]:
        """Look up a single tool by name.

        Args:
            name: The exact tool name as registered.

        Returns:
            The matching :class:`~langchain_core.tools.BaseTool`, or
            ``None`` if no tool with that name has been registered.

        Example:
            >>> registry = ToolRegistry()
            >>> registry.get("nonexistent") is None
            True
        """
        return self._tools.get(name)

    def get_tools(self, names: list[str]) -> list[BaseTool]:
        """Return a list of tools corresponding to the requested names.

        Names that are not found in the registry are silently skipped,
        so callers should ensure all expected tools are registered before
        invoking this method.

        Args:
            names: Ordered list of tool names to retrieve.

        Returns:
            A list of :class:`~langchain_core.tools.BaseTool` instances
            in the same order as *names*, with missing entries omitted.

        Example:
            >>> registry = ToolRegistry()
            >>> registry.register(get_current_time)
            >>> tools = registry.get_tools(["get_current_time", "missing"])
            >>> len(tools)
            1
        """
        return [self._tools[n] for n in names if n in self._tools]

    def invoke(self, name: str, args: dict) -> str:
        """Execute a registered tool by name and return its output as a string.

        This is the primary execution path used by :class:`~agents.base.BaseAgent`
        during the agentic loop.  If the requested tool is not found, an
        error string is returned rather than raising an exception, so the
        LLM can receive a meaningful ``ToolMessage`` and recover gracefully.

        Args:
            name: The tool name to invoke, as provided in the LLM's
                tool-call response.
            args: Keyword arguments to pass to the tool, as decoded from
                the LLM's tool-call ``args`` field.

        Returns:
            The tool's string output, or an ``"Unknown tool: <name>"``
            error string if the tool is not registered.

        Example:
            >>> registry = ToolRegistry()
            >>> registry.register(get_current_time)
            >>> result = registry.invoke("get_current_time", {})
            >>> isinstance(result, str)
            True
        """
        tool = self._tools.get(name)
        if tool is None:
            logger.debug("Invoked unknown tool: %s", name)
            return f"Unknown tool: {name}"

        logger.debug("Invoking tool %s with args: %s", name, args)
        result = str(tool.invoke(args))
        # Truncate in the log only — the full result is forwarded to the LLM.
        logger.debug("Tool %s result (truncated): %s", name, result[:300])
        return result

    def list_names(self) -> list[str]:
        """Return the names of all registered tools in insertion order.

        Returns:
            A list of tool name strings.

        Example:
            >>> registry = ToolRegistry()
            >>> registry.register(get_current_time)
            >>> registry.list_names()
            ['get_current_time']
        """
        return list(self._tools.keys())
