"""Pytest configuration for backend unit tests.

Adds ``backend/`` and the project root to :data:`sys.path` so that
``backend`` packages (``tools``, ``agents``, ``config``, …) and
project-root packages (``auth``, ``stocks``) are importable without
installing anything.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"

for _p in (str(_BACKEND_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
