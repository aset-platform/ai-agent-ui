"""Monthly retention truncation for ``stocks.intraday_bars``
(ASETPLTFRM-400 slice 1g).

Maintains a rolling **4-year window** by deleting any row whose
``bar_date`` is older than ``first_of_month(today_IST - 4 years)``.

Cadence
-------
Wired into the daily ``Intraday Bars Daily Pipeline`` for
operational simplicity, but **no-ops on every run except the
first successful run of each IST calendar month**. The first-run
detection uses ``scheduler_runs`` (looks at the previous
successful retention's started_at), so:

- 1st of month is a weekday → runs that day
- 1st of month is Sat / Sun  → no daily-pipeline run those days;
  next Monday becomes the first-run-of-the-month, retention fires
- Re-runs within the same month (e.g. operator triggers manually)
  → no-op

Why monthly rather than daily?
``stocks.intraday_bars`` partitions on ``(ticker, year_month)``.
A daily-cadence cutoff (e.g. 2022-05-14) lands mid-partition and
forces a row-level rewrite of every May 2022 file. A monthly
cutoff (= ``first_of_month``) is partition-aligned: the whole
``year_month=2022-05`` slice's parquet files are dropped from
the manifest as a metadata-only delete. Plus the pre-delete
``backup_table`` step (~16 min for a 500 MB tree) fires once a
month instead of every weekday.

The delete predicate uses ``LessThan("bar_date", cutoff_iso)``.
``bar_date`` is stored as a ``YYYY-MM-DD`` string so
lexicographic ordering matches chronological — Iceberg's
predicate push-down handles this without a custom expression.

Idempotent within a month (the gate short-circuits) AND across
months (re-running after the cutoff has already advanced deletes
no rows).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pyiceberg.expressions import LessThan

from sqlalchemy import text

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.db.duckdb_engine import invalidate_metadata
from backend.db.engine import disposable_pg_session
from backend.maintenance.backup import backup_table

_logger = logging.getLogger(__name__)

INTRADAY_BARS_TABLE = "stocks.intraday_bars"
DEFAULT_RETENTION_YEARS = 4


def _ist_today() -> date:
    """IST-local date (UTC+5:30)."""
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _retention_cutoff(
    today: date,
    *,
    years: int = DEFAULT_RETENTION_YEARS,
) -> date:
    """First day of the month ``years`` years before ``today``.

    Partition-aligned with the ``(ticker, year_month)`` layout so
    the eventual ``LessThan("bar_date", cutoff)`` delete drops
    whole-month partitions as a metadata-only operation, not a
    mid-partition rewrite.

    Examples
    --------
    >>> _retention_cutoff(date(2026, 5, 13))
    datetime.date(2022, 5, 1)
    >>> _retention_cutoff(date(2026, 6, 3))  # ran late after weekend
    datetime.date(2022, 6, 1)
    >>> _retention_cutoff(date(2024, 2, 29), years=4)
    datetime.date(2020, 2, 1)
    """
    return date(today.year - years, today.month, 1)


async def _already_ran_this_month(
    *, today: date,
) -> bool:
    """``True`` iff the latest successful intraday-bars-retention
    run in ``scheduler_runs`` started this IST calendar month.

    Detection by query rather than by ``today.day == 1`` so the
    job fires on the first run of the month even when the 1st is
    Sat / Sun (and the daily pipeline skips the weekend).
    """
    async with disposable_pg_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT started_at FROM scheduler_runs "
                    "WHERE job_type = 'intraday_bars_retention' "
                    "  AND status = 'success' "
                    "ORDER BY started_at DESC LIMIT 1"
                ),
            )
        ).first()
    if row is None or row[0] is None:
        return False
    # ``started_at`` is tz-aware UTC; convert to IST and check
    # against ``today``'s (year, month).
    started_ist = (
        row[0].astimezone(timezone.utc)
        + timedelta(hours=5, minutes=30)
    ).date()
    return (
        started_ist.year == today.year
        and started_ist.month == today.month
    )


async def run_intraday_bars_retention_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delete ``stocks.intraday_bars`` rows older than the
    rolling retention cutoff.

    Payload keys (all optional):
      - ``years``: retention window length. Default
        ``DEFAULT_RETENTION_YEARS`` (= 4).
      - ``today``: ISO date override (testing). Default
        IST-today.

    Returns a structured summary suitable for ``scheduler_runs``.
    The Iceberg delete API does not surface a row count, so the
    payload reports the cutoff date and a status flag rather
    than the actual rows removed. Operators can verify via a
    follow-up ``SELECT COUNT(*) WHERE bar_date < cutoff`` (which
    should return 0 after a successful run).
    """
    payload = payload or {}
    years = int(payload.get("years") or DEFAULT_RETENTION_YEARS)
    force = bool(payload.get("force") or False)
    today = (
        date.fromisoformat(payload["today"])
        if payload.get("today")
        else _ist_today()
    )

    # Monthly-cadence gate: short-circuit when retention already
    # ran successfully this IST calendar month. The expensive
    # backup-before-delete only fires once a month this way,
    # saving ~70 hr/yr of wall clock + I/O. ``payload.force``
    # bypasses the gate for ad-hoc CLI / one-shot operator runs.
    if not force and await _already_ran_this_month(today=today):
        _logger.info(
            "intraday-retention: skipping — already ran this "
            "IST month (today=%s); pass payload.force=true to "
            "override.",
            today.isoformat(),
        )
        return {
            "status": "skipped_already_ran_this_month",
            "today": today.isoformat(),
            "years": years,
        }

    cutoff = _retention_cutoff(today, years=years)
    cutoff_iso = cutoff.isoformat()

    _logger.info(
        "intraday-retention: trimming bars older than %s "
        "(today=%s, years=%d, partition-aligned monthly cutoff)",
        cutoff_iso,
        today.isoformat(),
        years,
    )

    # Fail-closed table-level backup BEFORE the delete commits
    # (ASETPLTFRM-400 slice 1h). The retention delete cannot be
    # rolled back in-place once the maintenance step expires
    # the pre-delete snapshot, so a filesystem-level safety
    # copy is the only durable rollback path. Targeted at this
    # one table so the rsync stays under the 30-min cap.
    skip_backup = bool(payload.get("skip_backup"))
    backup_path: str | None = None
    if not skip_backup:
        try:
            backup_path = backup_table(INTRADAY_BARS_TABLE)
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "intraday-retention: pre-delete backup failed "
                "for %s — aborting delete: %s",
                INTRADAY_BARS_TABLE,
                exc,
                exc_info=True,
            )
            return {
                "status": "error",
                "cutoff": cutoff_iso,
                "today": today.isoformat(),
                "years": years,
                "error": f"backup_failed: {exc!s}"[:200],
            }

    def _do_delete() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(INTRADAY_BARS_TABLE)
        # ``LessThan`` matches chronologically because the
        # bar_date column is a fixed-width YYYY-MM-DD string.
        tbl.delete(LessThan("bar_date", cutoff_iso))

    try:
        retry_iceberg_op(INTRADAY_BARS_TABLE, _do_delete)
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "intraday-retention: delete failed for cutoff=%s: %s",
            cutoff_iso,
            exc,
            exc_info=True,
        )
        return {
            "status": "error",
            "cutoff": cutoff_iso,
            "today": today.isoformat(),
            "years": years,
            "error": str(exc)[:200],
        }
    invalidate_metadata(INTRADAY_BARS_TABLE)
    _logger.info(
        "intraday-retention: trim complete for cutoff=%s",
        cutoff_iso,
    )
    return {
        "status": "ok",
        "cutoff": cutoff_iso,
        "today": today.isoformat(),
        "years": years,
        "backup_path": backup_path,
    }
