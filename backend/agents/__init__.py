"""agents — multi-agent framework sub-package.

Re-exports the public API for convenience::

    from agents import AgentConfig, BaseAgent
"""

import logging

from agents.config import AgentConfig, MAX_ITERATIONS
from agents.base import BaseAgent

logger = logging.getLogger(__name__)

# Module-level export list; kept here as required by Python's import machinery.
_all_exports = ["AgentConfig", "MAX_ITERATIONS", "BaseAgent"]

__all__ = _all_exports