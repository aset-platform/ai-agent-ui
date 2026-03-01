"""Layout package for the AI Stock Analysis Dashboard.

Re-exports all public layout constants and factory functions so that
``from dashboard.layouts import X`` continues to work unchanged.
"""

import logging

from dashboard.layouts.helpers import _get_available_tickers, _load_registry
from dashboard.layouts.navbar import NAVBAR
from dashboard.layouts.home import home_layout
from dashboard.layouts.analysis import analysis_layout, analysis_tabs_layout
from dashboard.layouts.forecast import forecast_layout
from dashboard.layouts.compare import compare_layout
from dashboard.layouts.admin import admin_users_layout
from dashboard.layouts.insights import insights_layout

logger = logging.getLogger(__name__)

# Module-level export list; kept at module scope as required by Python's
# import machinery but prefixed conceptually as a package-level constant.
_all_exports = [
    "NAVBAR",
    "home_layout",
    "analysis_layout",
    "analysis_tabs_layout",
    "forecast_layout",
    "compare_layout",
    "admin_users_layout",
    "insights_layout",
    "_load_registry",
    "_get_available_tickers",
]

__all__ = _all_exports