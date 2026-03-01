"""Environment setup helpers for the Dash dashboard.

Functions
---------
- :func:`_load_dotenv` — parse key=value pairs into ``os.environ``.
- :func:`setup_sys_path` — add project root to ``sys.path``.
"""

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent


def _load_dotenv(path: Path) -> None:
    """Parse key=value pairs from *path* into ``os.environ`` (no-op if absent).

    Only sets keys that are not already present in the environment, so
    shell-exported values always take precedence.

    Args:
        path: Absolute path to the ``.env`` file to read.
    """
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            _v = _v.strip().strip("'\"")
            if _k and _k not in os.environ:
                os.environ[_k] = _v


def setup_sys_path() -> None:
    """Add the project root directory to ``sys.path``.

    Required so that backend packages (``tools.*``, ``stocks.*``, etc.) can
    be imported by dashboard callbacks without needing an installed package.
    """
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
