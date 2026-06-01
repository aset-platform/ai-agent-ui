"""Shared market-hours utilities.

Single source of truth for the NSE session window so multiple
routes (``market_routes``, ``dashboard_routes``, future overlay
modules) all agree on what "live" means.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def is_market_open() -> bool:
    """True if IST is Mon-Fri 09:00-15:30.

    NSE cash session is 09:15-15:30, but we widen the lower
    bound to 09:00 to surface pre-open auction data (which
    Kite ``quote()`` reflects).
    """
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(
        hour=9, minute=0, second=0, microsecond=0,
    )
    close_t = now.replace(
        hour=15, minute=30, second=0, microsecond=0,
    )
    return open_t <= now <= close_t
