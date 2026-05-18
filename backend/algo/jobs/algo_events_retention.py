"""Weekly retention pass for ``algo.events``.

Reduces the blast radius of the global event log by purging
short-retention events (backtest / paper / dryrun / observability)
on a 7-day window, while keeping the long retention (365 days)
for live-mode events that actually represent a successful order
placement on Zerodha — see ``LIVE_PLACED_ZERODHA_TYPES`` in
``backend/algo/iceberg_init.py``.

Retention matrix
----------------
+-----------------------------+-----------------+
| mode                        | retention       |
+=============================+=================+
| backtest                    | 7 d             |
| paper                       | 7 d             |
| dryrun                      | 7 d             |
| live-ws                     | 7 d             |
| walkforward                 | 7 d             |
| pipeline                    | 7 d             |
| live (non-placed-on-Zerodha)| 7 d             |
| live (placed-on-Zerodha)    | 365 d           |
+-----------------------------+-----------------+

A row is "placed on Zerodha" if its ``type`` is in
``LIVE_PLACED_ZERODHA_TYPES``:

* ``order_submitted_live``      — order sent to Kite
* ``order_filled_live``         — Kite confirmed a fill
* ``kite_postback_received``    — Kite confirmation postback
* ``freeze_qty_fallback_applied``— Kite-side mid-order adjustment

Per CLAUDE.md §5.1: scheduler jobs MUST use
``disposable_pg_session`` (NullPool) and the Iceberg writes go
through ``retry_iceberg_op``.  The pre-delete ``backup_table``
follows the same fail-closed contract as
``intraday_bars_retention`` — abort the delete if the backup
fails, since Iceberg deletes can't be rolled back once snapshot
expiry runs.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pyiceberg.expressions import (
    And,
    EqualTo,
    LessThan,
    NotIn,
    Or,
)

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.algo.iceberg_init import (
    LIVE_PLACED_ZERODHA_TYPES,
    LONG_RETENTION_DAYS,
    SHORT_RETENTION_DAYS,
    SHORT_RETENTION_MODES,
)
from backend.db.duckdb_engine import invalidate_metadata
from backend.maintenance.backup import backup_table

_logger = logging.getLogger(__name__)

ALGO_EVENTS_TABLE = "algo.events"


def _ist_today() -> date:
    """IST-local date (UTC+5:30)."""
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def _delete_predicate(today: date):
    """Build a PyIceberg expression that matches every row that
    has aged beyond its retention.

    The expression is the OR of three disjoint clauses:

    1. Any non-live mode older than ``SHORT_RETENTION_DAYS``.
    2. Live mode + type NOT in placed-on-Zerodha allowlist + older
       than ``SHORT_RETENTION_DAYS``.
    3. Live mode older than ``LONG_RETENTION_DAYS`` (hard cap,
       regardless of type).
    """
    short_cut = today - timedelta(days=SHORT_RETENTION_DAYS)
    long_cut = today - timedelta(days=LONG_RETENTION_DAYS)

    # PyIceberg ``Or`` is binary, so chain the mode-in-set predicate
    # as a disjunction.  These modes are 6 known string literals so
    # chaining is fine; no need for an IN-list expression.
    short_mode_terms = [EqualTo("mode", m) for m in SHORT_RETENTION_MODES]
    short_mode_pred = short_mode_terms[0]
    for t in short_mode_terms[1:]:
        short_mode_pred = Or(short_mode_pred, t)

    # NotIn for the placed-on-Zerodha allowlist.  Passing a list of
    # types lets Iceberg push the filter down into the manifest
    # planning phase.
    placed_types = tuple(LIVE_PLACED_ZERODHA_TYPES)

    clause_short_nonlive = And(
        short_mode_pred,
        LessThan("ts_date", short_cut),
    )
    clause_live_non_placed = And(
        And(
            EqualTo("mode", "live"),
            NotIn("type", placed_types),
        ),
        LessThan("ts_date", short_cut),
    )
    clause_live_long_cap = And(
        EqualTo("mode", "live"),
        LessThan("ts_date", long_cut),
    )

    return Or(
        Or(clause_short_nonlive, clause_live_non_placed),
        clause_live_long_cap,
    )


def run_algo_events_retention_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the retention matrix above to ``algo.events``.

    Payload keys (all optional):
      - ``today``: ISO date override (testing). Default IST-today.
      - ``skip_backup``: bypass the fail-closed pre-delete backup
        step.  Off by default.
      - ``dry_run``: log the predicate + return early without
        deleting.  Off by default.

    Returns a structured summary suitable for ``scheduler_runs``.
    The Iceberg delete API does not surface a row count; operators
    can verify via a follow-up
    ``SELECT COUNT(*) FROM events WHERE <predicate>`` which
    should return 0 after a successful run.
    """
    payload = payload or {}
    today = (
        date.fromisoformat(payload["today"])
        if payload.get("today")
        else _ist_today()
    )
    short_cut = today - timedelta(days=SHORT_RETENTION_DAYS)
    long_cut = today - timedelta(days=LONG_RETENTION_DAYS)
    dry_run = bool(payload.get("dry_run") or False)
    skip_backup = bool(payload.get("skip_backup") or False)

    _logger.info(
        "algo-events-retention: short_cut=%s long_cut=%s "
        "(short=%dd, long=%dd, today=%s, dry_run=%s)",
        short_cut.isoformat(),
        long_cut.isoformat(),
        SHORT_RETENTION_DAYS,
        LONG_RETENTION_DAYS,
        today.isoformat(),
        dry_run,
    )

    predicate = _delete_predicate(today)
    if dry_run:
        return {
            "status": "dry_run",
            "short_cut": short_cut.isoformat(),
            "long_cut": long_cut.isoformat(),
            "predicate_repr": repr(predicate)[:500],
            "today": today.isoformat(),
        }

    backup_path: str | None = None
    if not skip_backup:
        try:
            backup_path = backup_table(ALGO_EVENTS_TABLE)
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "algo-events-retention: pre-delete backup "
                "failed for %s — aborting: %s",
                ALGO_EVENTS_TABLE,
                exc,
                exc_info=True,
            )
            return {
                "status": "error",
                "today": today.isoformat(),
                "error": f"backup_failed: {exc!s}"[:200],
            }

    def _do_delete() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(ALGO_EVENTS_TABLE)
        tbl.delete(predicate)

    try:
        retry_iceberg_op(ALGO_EVENTS_TABLE, _do_delete)
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "algo-events-retention: delete failed: %s",
            exc, exc_info=True,
        )
        return {
            "status": "error",
            "today": today.isoformat(),
            "error": str(exc)[:200],
            "backup_path": backup_path,
        }

    try:
        invalidate_metadata(ALGO_EVENTS_TABLE)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "algo-events-retention: invalidate_metadata "
            "failed (non-fatal): %s", exc,
        )

    _logger.info(
        "algo-events-retention: complete (today=%s, "
        "backup=%s)",
        today.isoformat(),
        backup_path,
    )
    return {
        "status": "ok",
        "today": today.isoformat(),
        "short_cut": short_cut.isoformat(),
        "long_cut": long_cut.isoformat(),
        "backup_path": backup_path,
    }
