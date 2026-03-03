"""Callback package for the AI Stock Analysis Dashboard.

Re-exports the public API so that ``from dashboard.callbacks import X``
continues to work unchanged after the package split.

The original ``dashboard/callbacks.py`` monolith has been refactored into
the following sub-modules:

- :mod:`~dashboard.callbacks.utils` — pure helpers (validation, market, currency)
- :mod:`~dashboard.callbacks.auth_utils` — JWT validation and API helpers
- :mod:`~dashboard.callbacks.data_loaders` — parquet / registry readers
- :mod:`~dashboard.callbacks.chart_builders` — analysis chart builders
- :mod:`~dashboard.callbacks.chart_builders2` — forecast chart builder
- :mod:`~dashboard.callbacks.card_builders` — stat / target / accuracy cards
- :mod:`~dashboard.callbacks.table_builders` — user / audit HTML tables
- :mod:`~dashboard.callbacks.iceberg` — Iceberg repo singleton
- :mod:`~dashboard.callbacks.registration` — :func:`register_callbacks`
- :mod:`~dashboard.callbacks.routing_cbs` — auth-token and routing callbacks
- :mod:`~dashboard.callbacks.home_cbs` — home-page callbacks
- :mod:`~dashboard.callbacks.analysis_cbs` — analysis and compare callbacks
- :mod:`~dashboard.callbacks.forecast_cbs` — forecast and run-analysis callbacks
- :mod:`~dashboard.callbacks.admin_cbs` — user management callbacks
- :mod:`~dashboard.callbacks.admin_cbs2` — modal and password-change callbacks
- :mod:`~dashboard.callbacks.insights_cbs` — Insights tab callbacks

Example::

    from dashboard.callbacks import register_callbacks
    register_callbacks(app)
"""

import logging

from dashboard.callbacks.auth_utils import (
    _admin_forbidden,
    _unauth_notice,
    _validate_token,
)
from dashboard.callbacks.registration import register_callbacks

logger = logging.getLogger(__name__)

# Module-level export list — must remain module-level for Python import machinery.
_all_public = [
    "register_callbacks",
    "_validate_token",
    "_unauth_notice",
    "_admin_forbidden",
]

__all__ = _all_public
