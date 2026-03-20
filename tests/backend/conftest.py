"""Pytest configuration for backend unit tests.

Adds ``backend/`` and the project root to :data:`sys.path` so that
``backend`` packages (``tools``, ``agents``, ``config``, …) and
project-root packages (``auth``, ``stocks``) are importable without
installing anything.
"""

import os
import sys
from pathlib import Path

# Force test cascade profile (free-tier only, no Anthropic).
os.environ.setdefault("AI_AGENT_UI_ENV", "test")

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"

for _p in (str(_BACKEND_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_limiter():
    """Clear slowapi limiter state between test modules."""
    try:
        from auth.rate_limit import limiter

        limiter.reset()
    except Exception:
        pass


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """Reset rate limiter state before each test."""
    _reset_limiter()
    yield
    _reset_limiter()
