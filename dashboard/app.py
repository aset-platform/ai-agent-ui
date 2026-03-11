"""Plotly Dash entry point for the AI Stock Analysis Dashboard.

Bootstraps the application, wires callbacks, and launches the server on
port 8050.  Heavy lifting is delegated to sub-modules:

- :mod:`dashboard.app_env` — ``sys.path`` setup and ``.env`` loading.
- :mod:`dashboard.app_init` — :func:`~dashboard.app_init.create_app` factory.
- :mod:`dashboard.app_layout` — root layout and page-routing callback.
- :mod:`dashboard.callbacks` — interactive Dash callbacks.

Usage::

    python dashboard/app.py

    # or with gunicorn:
    gunicorn "dashboard.app:server" --bind 0.0.0.0:8050
"""

import logging
import sys
from pathlib import Path

# Bootstrap sys.path FIRST so that 'dashboard' is importable as a package.
# When launched as `python dashboard/app.py`, Python sets sys.path[0] to the
# dashboard/ directory, not the project root.  We add the project root here
# before any 'from dashboard.*' imports.
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.app_env import (  # noqa: E402
    _PROJECT_ROOT,
    _load_dotenv,
    setup_sys_path,
)

# Ensure project root is registered via the canonical helper too
setup_sys_path()
_load_dotenv(_PROJECT_ROOT / ".env")
_load_dotenv(_PROJECT_ROOT / "backend" / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
# Module-level logger — must remain module-level for pre-class import logging
_logger = logging.getLogger(__name__)

from dashboard.app_init import create_app  # noqa: E402 — must be after dotenv
from dashboard.app_layout import build_layout  # noqa: E402
from dashboard.callbacks import register_callbacks  # noqa: E402

# Module-level Dash application instance — must remain module-level to expose
# 'server' for gunicorn and to allow Dash callback registration at import time.
_app = create_app()
server = _app.server  # expose for gunicorn
build_layout(_app)
register_callbacks(_app)

if __name__ == "__main__":
    _logger.info(
        "Starting AI Stock Analysis Dashboard on http://127.0.0.1:8050"
    )
    _app.run(debug=True, port=8050)
