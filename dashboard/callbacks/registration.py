dashboard/callbacks/registration.py
"""Callback registration entry point for the AI Stock Analysis Dashboard.

Defines :func:`register_callbacks` which imports and calls each feature-area
``register(app)`` function in the correct order so all Dash callbacks are
wired to the application instance.

Example::

    from dashboard.callbacks.registration import register_callbacks
    register_callbacks(app)
"""

import logging

from dashboard.callbacks.routing_cbs import register as _reg_routing
from dashboard.callbacks.profile_cbs import register as _reg_profile
from dashboard.callbacks.home_cbs import register as _reg_home
from dashboard.callbacks.analysis_cbs import register as _reg_analysis
from dashboard.callbacks.forecast_cbs import register as _reg_forecast
from dashboard.callbacks.admin_cbs import register as _reg_admin
from dashboard.callbacks.admin_cbs2 import register as _reg_admin2
from dashboard.callbacks.insights_cbs import register as _reg_insights

# Module-level logger; kept here as a module-level constant (not mutable state).
_logger = logging.getLogger(__name__)


def register_callbacks(app) -> None:
    """Register all Dash callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance created in
            ``dashboard/app.py``.

    Returns:
        None

    Raises:
        Exception: Propagates any exception raised by individual
            feature-area ``register(app)`` functions.
    """
    _logger.debug("Registering routing callbacks.")
    _reg_routing(app)

    _logger.debug("Registering profile callbacks.")
    _reg_profile(app)

    _logger.debug("Registering home callbacks.")
    _reg_home(app)

    _logger.debug("Registering analysis callbacks.")
    _reg_analysis(app)

    _logger.debug("Registering forecast callbacks.")
    _reg_forecast(app)

    _logger.debug("Registering admin callbacks.")
    _reg_admin(app)

    _logger.debug("Registering admin2 callbacks.")
    _reg_admin2(app)

    _logger.debug("Registering insights callbacks.")
    _reg_insights(app)

    _logger.info("All callbacks registered successfully.")