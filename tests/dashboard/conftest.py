"""Pytest configuration for dashboard unit tests.

Adds the project root to :data:`sys.path` so the ``dashboard`` package
is importable without installing anything.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
