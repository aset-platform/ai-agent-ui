"""Iceberg repository singleton for the AI Stock Analysis Dashboard.

Provides a lazy-initialised singleton accessor for the
:class:`~stocks.repository.StockRepository` used by Insights-page callbacks.
The singleton is initialised on first call and cached for the lifetime of the
dashboard process.

Example::

    from dashboard.callbacks.iceberg import _get_iceberg_repo
    repo = _get_iceberg_repo()
"""

import logging
import sys
from pathlib import Path
from typing import Optional

# Module-level logger — must remain module-level for use outside any class scope
_logger = logging.getLogger(__name__)

# Ensure project root on sys.path before stocks import
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_DASH_REPO = None
_DASH_REPO_INIT_ATTEMPTED = False


def _get_iceberg_repo() -> Optional[object]:
    """Return the module-level :class:`~stocks.repository.StockRepository` singleton.

    Initialised on first call; returns ``None`` when PyIceberg is unavailable.

    Returns:
        :class:`~stocks.repository.StockRepository` instance or ``None``.
    """
    global _DASH_REPO, _DASH_REPO_INIT_ATTEMPTED
    if _DASH_REPO_INIT_ATTEMPTED:
        return _DASH_REPO
    _DASH_REPO_INIT_ATTEMPTED = True
    try:
        from stocks.repository import StockRepository  # noqa: PLC0415
        _DASH_REPO = StockRepository()
        _logger.debug("StockRepository initialised for dashboard")
    except Exception as _e:
        _logger.warning("StockRepository unavailable in dashboard: %s", _e)
    return _DASH_REPO