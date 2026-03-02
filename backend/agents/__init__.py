"""agents — multi-agent framework sub-package.

Re-exports the public API for convenience::

    from agents import AgentConfig, BaseAgent
"""

import logging

from agents.base import BaseAgent
from agents.config import MAX_ITERATIONS, AgentConfig

logger = logging.getLogger(__name__)

# Module-level export list; kept here as required by Python's import machinery.
_all_exports = ["AgentConfig", "MAX_ITERATIONS", "BaseAgent"]

__all__ = _all_exports
