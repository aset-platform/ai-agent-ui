"""Usage tracking with lazy monthly auto-reset + history.

On each :func:`increment_usage` call, the current month is
compared to the stored ``usage_month``.  If different, the
previous month's count is archived to ``auth.usage_history``
and the counter is zeroed before incrementing.

Functions
---------
- :func:`increment_usage` — increment + lazy reset
- :func:`get_usage_stats` — all users with counts
- :func:`reset_user_usage` — reset specific users
- :func:`reset_monthly_usage` — reset all users
- :func:`get_usage_history` — month-on-month history
- :func:`is_quota_exceeded` — check if user hit limit
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)


def _current_month() -> str:
    """Return current month as ``YYYY-MM``."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _now_naive() -> datetime:
    """Return naive UTC datetime for Iceberg storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_quota_exceeded(user_id: str) -> bool:
    """Check if the user's monthly quota is exhausted.

    Performs a lazy reset if the month has changed.
    Returns True if the user should be blocked.
    Premium users (quota=0) always return False.

    Args:
        user_id: UUID string.

    Returns:
        True if quota exceeded, False if allowed.
    """
    if not user_id:
        return False
    try:
        from auth.endpoints.helpers import _get_repo
        from subscription_config import USAGE_QUOTAS

        repo = _get_repo()
        user = repo.get_by_id(user_id)
        if user is None:
            return False

        user = _maybe_reset(
            repo, user, _current_month(),
        )
        tier = user.get("subscription_tier") or "free"
        quota = USAGE_QUOTAS.get(tier, 3)
        if quota == 0:
            return False  # unlimited
        count = user.get("monthly_usage_count") or 0
        return count >= quota
    except Exception:
        _logger.exception(
            "Quota check failed for user=%s",
            user_id,
        )
        return False  # fail open


def _archive_usage(
    user_id: str,
    month: str,
    count: int,
    tier: str,
) -> None:
    """Write a usage snapshot to ``auth.usage_history``.

    Args:
        user_id: UUID string.
        month: Month string (``YYYY-MM``).
        count: Usage count for that month.
        tier: Subscription tier during that month.
    """
    if count <= 0:
        return
    try:
        import pyarrow as pa

        from auth.endpoints.helpers import _get_repo
        from auth.repo.schemas import (
            _USAGE_HISTORY_PA_SCHEMA,
            _USAGE_HISTORY_TABLE,
        )

        repo = _get_repo()
        cat = repo._get_catalog()
        tbl = cat.load_table(_USAGE_HISTORY_TABLE)
        row = pa.table(
            {
                "user_id": [user_id],
                "month": [month],
                "usage_count": [count],
                "tier": [tier],
                "archived_at": [_now_naive()],
            },
            schema=_USAGE_HISTORY_PA_SCHEMA,
        )
        tbl.append(row)
        _logger.info(
            "Archived usage: user=%s month=%s"
            " count=%d tier=%s",
            user_id,
            month,
            count,
            tier,
        )
    except Exception:
        _logger.exception(
            "Failed to archive usage for user=%s"
            " month=%s",
            user_id,
            month,
        )


def _maybe_reset(
    repo: object,
    user: dict,
    current_month: str,
) -> dict:
    """Lazy auto-reset if month has changed.

    Archives previous month, resets counter to 0,
    updates usage_month to current.

    Args:
        repo: IcebergUserRepository instance.
        user: User dict from Iceberg.
        current_month: Current ``YYYY-MM``.

    Returns:
        Updated user dict (count may be zeroed).
    """
    stored_month = user.get("usage_month") or ""
    if stored_month == current_month:
        return user

    old_count = user.get("monthly_usage_count") or 0
    old_tier = user.get("subscription_tier") or "free"
    uid = user["user_id"]

    # Archive the old month's usage
    if stored_month and old_count > 0:
        _archive_usage(uid, stored_month, old_count, old_tier)

    # Reset counter + set new month
    repo.update(
        uid,
        {
            "monthly_usage_count": 0,
            "usage_month": current_month,
        },
    )
    user = dict(user)
    user["monthly_usage_count"] = 0
    user["usage_month"] = current_month
    _logger.info(
        "Auto-reset: user=%s old_month=%s"
        " new_month=%s archived=%d",
        uid,
        stored_month,
        current_month,
        old_count,
    )
    return user


def increment_usage(user_id: str) -> None:
    """Increment ``monthly_usage_count`` with lazy reset.

    If the stored ``usage_month`` differs from the current
    month, archives the old count and resets before
    incrementing.

    Args:
        user_id: UUID string of the user.
    """
    try:
        from auth.endpoints.helpers import _get_repo

        repo = _get_repo()
        user = repo.get_by_id(user_id)
        if user is None:
            _logger.warning(
                "increment_usage: user_id=%s not found",
                user_id,
            )
            return

        user = _maybe_reset(repo, user, _current_month())
        current = user.get("monthly_usage_count") or 0
        repo.update(
            user_id,
            {
                "monthly_usage_count": current + 1,
                "usage_month": _current_month(),
            },
        )
        _logger.info(
            "Usage incremented: user=%s count=%d",
            user_id,
            current + 1,
        )
    except Exception:
        _logger.exception(
            "Failed to increment usage for user=%s",
            user_id,
        )


def get_usage_stats() -> list[dict]:
    """Return usage stats for all users.

    Returns:
        List of dicts sorted by usage descending.
    """
    from auth.endpoints.helpers import _get_repo

    repo = _get_repo()
    users = repo.list_all()
    result = []
    for u in users:
        result.append({
            "user_id": u.get("user_id", ""),
            "email": u.get("email", ""),
            "full_name": u.get("full_name", ""),
            "subscription_tier": (
                u.get("subscription_tier") or "free"
            ),
            "monthly_usage_count": (
                u.get("monthly_usage_count") or 0
            ),
            "usage_month": u.get("usage_month") or "",
        })
    result.sort(
        key=lambda x: x["monthly_usage_count"],
        reverse=True,
    )
    return result


def reset_user_usage(user_ids: list[str]) -> int:
    """Reset usage for specific users.

    Archives current month before resetting.

    Args:
        user_ids: List of user UUID strings.

    Returns:
        Number of users actually reset.
    """
    from auth.endpoints.helpers import _get_repo

    repo = _get_repo()
    month = _current_month()
    count = 0
    for uid in user_ids:
        user = repo.get_by_id(uid)
        if not user:
            continue
        old = user.get("monthly_usage_count") or 0
        if old > 0:
            _archive_usage(
                uid,
                user.get("usage_month") or month,
                old,
                user.get("subscription_tier") or "free",
            )
            repo.update(
                uid,
                {
                    "monthly_usage_count": 0,
                    "usage_month": month,
                },
            )
            count += 1
    _logger.info(
        "Selective reset: %d/%d users",
        count,
        len(user_ids),
    )
    return count


def reset_monthly_usage() -> int:
    """Reset all users — archives first.

    Returns:
        Number of users whose count was reset.
    """
    from auth.endpoints.helpers import _get_repo

    repo = _get_repo()
    users = repo.list_all()
    month = _current_month()
    count = 0
    for user in users:
        uid = user.get("user_id", "")
        current = user.get("monthly_usage_count") or 0
        if current > 0:
            _archive_usage(
                uid,
                user.get("usage_month") or month,
                current,
                user.get("subscription_tier") or "free",
            )
            repo.update(
                uid,
                {
                    "monthly_usage_count": 0,
                    "usage_month": month,
                },
            )
            count += 1
    _logger.info(
        "Monthly usage reset: %d users zeroed", count,
    )
    return count


def get_usage_history(
    user_id: str | None = None,
    limit: int = 12,
) -> list[dict]:
    """Fetch month-on-month usage history.

    Args:
        user_id: Filter to specific user, or all if None.
        limit: Max months to return (newest first).

    Returns:
        List of dicts with user_id, month, usage_count,
        tier, archived_at.
    """
    try:
        from auth.endpoints.helpers import _get_repo
        from auth.repo.schemas import (
            _USAGE_HISTORY_TABLE,
        )

        repo = _get_repo()
        cat = repo._get_catalog()
        tbl = cat.load_table(_USAGE_HISTORY_TABLE)
        scan = tbl.scan()
        df = scan.to_pandas()

        if df.empty:
            return []

        if user_id:
            df = df[df["user_id"] == user_id]

        df = df.sort_values(
            "archived_at", ascending=False,
        )
        rows = df.head(limit).to_dict("records")
        for r in rows:
            if hasattr(r.get("archived_at"), "isoformat"):
                r["archived_at"] = (
                    r["archived_at"].isoformat()
                )
        return rows
    except Exception:
        _logger.exception("Failed to read usage history")
        return []
