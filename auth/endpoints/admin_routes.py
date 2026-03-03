"""Admin endpoint registrations (superuser only).

Functions
---------
- :func:`register` — attach admin routes to the router
"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

import auth.endpoints.helpers as _helpers
from auth.dependencies import superuser_only
from auth.models import UserContext


def register(router: APIRouter) -> None:
    """Register admin-only routes.

    Args:
        router: The :class:`~fastapi.APIRouter` to attach routes to.
    """

    @router.get("/admin/audit-log", tags=["admin"])
    def get_audit_log(
        _: UserContext = Depends(superuser_only),
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return all audit log events, sorted newest-first.

        Args:
            _: Superuser guard.

        Returns:
            A dict ``{"events": [...]}`` where each element is an audit event
            with ISO-8601 ``event_timestamp``.
        """
        repo = _helpers._get_repo()
        raw_events = repo.list_audit_events()
        events = []
        for ev in raw_events:
            d = dict(ev)
            ts = d.get("event_timestamp")
            if ts is not None and hasattr(ts, "isoformat"):
                d["event_timestamp"] = ts.isoformat()
            events.append(d)
        return {"events": events}
