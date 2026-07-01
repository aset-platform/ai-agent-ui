"""Weekly retention pass for ``algo.budget_reservations``.

Reduces unbounded row growth in the reservation event log by
purging rows that no longer affect any financial calculation:

Retention matrix
----------------
+-----------------------------+-------+-----------------------------+
| Row type                    | Grace | Rationale                   |
+=============================+=======+=============================+
| paper / dryrun (any state)  | 1 d   | Excluded from all budget    |
|                             |       | math; only shown in         |
|                             |       | BudgetPanel UI badges.      |
+-----------------------------+-------+-----------------------------+
| live TIMEOUT / CANCELLED /  | 7 d   | Terminal, no financial      |
| REJECTED / PARTIAL_CANCELLED|       | effect; useful for 1-week   |
|                             |       | debugging window only.      |
+-----------------------------+-------+-----------------------------+
| live FILLED (BUY + SELL)    | kept  | sum_open_position_cost      |
|                             | indef | needs both legs while a     |
|                             |       | position is open.  Long-run |
|                             |       | cleanup tracked in          |
|                             |       | ASETPLTFRM-462 (Option 3).  |
+-----------------------------+-------+-----------------------------+
| live PENDING / SUBMITTED /  | never | Active reservation — deleting|
| PARTIAL                     |       | breaks the budget gate.     |
+-----------------------------+-------+-----------------------------+

Runs weekly (Sunday 02:00 IST) via the ``scheduled_jobs`` row
seeded by ``scripts/seed_budget_reservations_retention.py``.

Per CLAUDE.md §5.1: uses ``disposable_pg_session`` (NullPool,
per-call) — scheduler jobs must not reuse the uvicorn session.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from backend.db.engine import disposable_pg_session

_logger = logging.getLogger(__name__)

# Non-live modes whose rows are safe to purge after a short grace.
_NON_LIVE_MODES = ("paper", "dryrun")

# Live terminal states where the reservation had no financial
# effect (no fill recorded).  FILLED rows are intentionally
# excluded — see retention matrix above.
_PURGEABLE_LIVE_STATES = (
    "TIMEOUT",
    "CANCELLED",
    "REJECTED",
    "PARTIAL_CANCELLED",
)

_NON_LIVE_GRACE_DAYS: int = 1
_LIVE_TERMINAL_GRACE_DAYS: int = 7


def run_budget_reservations_retention_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delete expired reservation rows.

    Payload keys (all optional):
      - ``non_live_grace_days``: override for paper/dryrun grace
        (default 1).
      - ``live_terminal_grace_days``: override for live terminal
        grace (default 7).
      - ``dry_run``: log counts and return early without deleting.

    Returns a summary dict suitable for ``scheduler_runs``.
    """
    import asyncio

    return asyncio.run(_run(payload or {}))


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.db.engine import disposable_pg_session

    non_live_grace = int(
        payload.get("non_live_grace_days", _NON_LIVE_GRACE_DAYS)
    )
    live_terminal_grace = int(
        payload.get(
            "live_terminal_grace_days", _LIVE_TERMINAL_GRACE_DAYS
        )
    )
    dry_run = bool(payload.get("dry_run", False))

    now = datetime.now(timezone.utc)
    non_live_cut = now - timedelta(days=non_live_grace)
    live_terminal_cut = now - timedelta(days=live_terminal_grace)

    _logger.info(
        "budget-reservations-retention: non_live_cut=%s "
        "live_terminal_cut=%s dry_run=%s",
        non_live_cut.isoformat(),
        live_terminal_cut.isoformat(),
        dry_run,
    )

    if dry_run:
        counts = await _count_rows(non_live_cut, live_terminal_cut)
        return {
            "status": "dry_run",
            "non_live_cut": non_live_cut.isoformat(),
            "live_terminal_cut": live_terminal_cut.isoformat(),
            "would_delete_non_live": counts["non_live"],
            "would_delete_live_terminal": counts["live_terminal"],
        }

    async with disposable_pg_session() as session:
        modes_in = ",".join(f"'{m}'" for m in _NON_LIVE_MODES)
        states_in = ",".join(
            f"'{s}'" for s in _PURGEABLE_LIVE_STATES
        )

        # Pass 1 — paper / dryrun (any state, short grace).
        r1 = await session.execute(
            text(
                f"DELETE FROM algo.budget_reservations "
                f"WHERE transitioned_at < :cut "
                f"  AND metadata->>'mode' IN ({modes_in})"
            ),
            {"cut": non_live_cut},
        )
        deleted_non_live: int = r1.rowcount

        # Pass 2 — live terminal non-filled (longer grace).
        r2 = await session.execute(
            text(
                f"DELETE FROM algo.budget_reservations "
                f"WHERE transitioned_at < :cut "
                f"  AND COALESCE(metadata->>'mode', 'live') "
                f"      = 'live' "
                f"  AND state IN ({states_in})"
            ),
            {"cut": live_terminal_cut},
        )
        deleted_live_terminal: int = r2.rowcount

        await session.commit()

    _logger.info(
        "budget-reservations-retention: deleted "
        "non_live=%d live_terminal=%d",
        deleted_non_live,
        deleted_live_terminal,
    )
    return {
        "status": "ok",
        "non_live_cut": non_live_cut.isoformat(),
        "live_terminal_cut": live_terminal_cut.isoformat(),
        "deleted_non_live": deleted_non_live,
        "deleted_live_terminal": deleted_live_terminal,
    }


async def _count_rows(
    non_live_cut: datetime,
    live_terminal_cut: datetime,
) -> dict[str, int]:
    """Return counts of rows that *would* be deleted (dry-run)."""
    modes_in = ",".join(f"'{m}'" for m in _NON_LIVE_MODES)
    states_in = ",".join(f"'{s}'" for s in _PURGEABLE_LIVE_STATES)

    async with disposable_pg_session() as session:
        r1 = await session.execute(
            text(
                f"SELECT COUNT(*) FROM algo.budget_reservations "
                f"WHERE transitioned_at < :cut "
                f"  AND metadata->>'mode' IN ({modes_in})"
            ),
            {"cut": non_live_cut},
        )
        r2 = await session.execute(
            text(
                f"SELECT COUNT(*) FROM algo.budget_reservations "
                f"WHERE transitioned_at < :cut "
                f"  AND COALESCE(metadata->>'mode', 'live') "
                f"      = 'live' "
                f"  AND state IN ({states_in})"
            ),
            {"cut": live_terminal_cut},
        )
        return {
            "non_live": r1.scalar() or 0,
            "live_terminal": r2.scalar() or 0,
        }
