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
import time as _time
from pathlib import Path
from typing import Optional

# Module-level logger — must remain module-level for use outside any class scope
_logger = logging.getLogger(__name__)

# Ensure project root on sys.path before stocks import
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Fix #10: TTL-based singleton — re-initialises after 1 h to survive Iceberg restarts
_DASH_REPO = None
_DASH_REPO_EXPIRY: float = 0.0
_DASH_REPO_TTL = 3600   # 1 hour

# Fix #6: TTL caches for expensive shared Iceberg reads (5-min TTL)
_SHARED_TTL = 300
_SUMMARY_CACHE: dict = {"data": None, "expiry": 0.0}
_COMPANY_CACHE: dict = {"data": None, "expiry": 0.0}


def _get_iceberg_repo() -> Optional[object]:
    """Return the module-level :class:`~stocks.repository.StockRepository` singleton.

    Re-initialised after ``_DASH_REPO_TTL`` seconds so the dashboard can
    recover automatically after an Iceberg catalog restart without requiring
    a full process restart.

    Returns:
        :class:`~stocks.repository.StockRepository` instance or ``None``.
    """
    global _DASH_REPO, _DASH_REPO_EXPIRY
    now = _time.monotonic()
    if _DASH_REPO is not None and now < _DASH_REPO_EXPIRY:
        return _DASH_REPO
    try:
        from stocks.repository import StockRepository  # noqa: PLC0415
        _DASH_REPO = StockRepository()
        _DASH_REPO_EXPIRY = now + _DASH_REPO_TTL
        _logger.debug("StockRepository initialised for dashboard")
    except Exception as _e:
        _logger.warning("StockRepository unavailable in dashboard: %s", _e)
        _DASH_REPO = None
    return _DASH_REPO


def _get_analysis_summary_cached(repo: object):
    """Return all latest analysis summaries, cached for ``_SHARED_TTL`` seconds.

    Avoids repeated Iceberg scans when multiple callbacks (screener, risk,
    sectors) all need the same table within the same refresh cycle.

    Args:
        repo: Active :class:`~stocks.repository.StockRepository` instance.

    Returns:
        :class:`~pandas.DataFrame` of analysis summary rows.
    """
    now = _time.monotonic()
    if _SUMMARY_CACHE["data"] is not None and now < _SUMMARY_CACHE["expiry"]:
        return _SUMMARY_CACHE["data"]
    data = repo.get_all_latest_analysis_summary()
    _SUMMARY_CACHE.update({"data": data, "expiry": now + _SHARED_TTL})
    return data


def _get_company_info_cached(repo: object):
    """Return all latest company info rows, cached for ``_SHARED_TTL`` seconds.

    Args:
        repo: Active :class:`~stocks.repository.StockRepository` instance.

    Returns:
        :class:`~pandas.DataFrame` of company info rows.
    """
    now = _time.monotonic()
    if _COMPANY_CACHE["data"] is not None and now < _COMPANY_CACHE["expiry"]:
        return _COMPANY_CACHE["data"]
    data = repo.get_all_latest_company_info()
    _COMPANY_CACHE.update({"data": data, "expiry": now + _SHARED_TTL})
    return data