"""Daily retention truncation for ``stocks.intraday_bars``
(ASETPLTFRM-400 slice 1g).

Maintains a rolling **4-year window** by deleting any row whose
``bar_date`` is older than ``today_IST - 4 years``. Runs as
step 2 of the ``Intraday Bars Daily Pipeline`` — between the
keeper write (step 1) and the Iceberg maintenance step
(step 3) so:

  - the keeper's fresh writes are already committed when we
    compute the cutoff and issue the scoped delete
  - the maintenance step's compaction afterward sees the
    smaller table and reclaims tombstoned files in the same
    run (free side-benefit, no extra cost)

The delete predicate uses ``LessThan("bar_date", cutoff_iso)``.
``bar_date`` is stored as a ``YYYY-MM-DD`` string so
lexicographic ordering matches chronological — Iceberg's
predicate push-down handles this without a custom expression.

Idempotent — re-running after the cutoff has already advanced
deletes nothing (the predicate matches no rows).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pyiceberg.expressions import LessThan

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.db.duckdb_engine import invalidate_metadata

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
    """Return ``today - years``, clamping Feb 29 → Feb 28 in the
    target year when the target year is non-leap.

    Examples
    --------
    >>> _retention_cutoff(date(2026, 5, 13))
    datetime.date(2022, 5, 13)
    >>> _retention_cutoff(date(2024, 2, 29))  # leap → 4 yrs back leap
    datetime.date(2020, 2, 29)
    >>> _retention_cutoff(date(2024, 2, 29), years=1)  # 2023 non-leap
    datetime.date(2023, 2, 28)
    """
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        # Feb 29 in a non-leap target year → fall back to Feb 28.
        return today.replace(
            year=today.year - years,
            day=28,
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
    today = (
        date.fromisoformat(payload["today"])
        if payload.get("today")
        else _ist_today()
    )
    cutoff = _retention_cutoff(today, years=years)
    cutoff_iso = cutoff.isoformat()

    _logger.info(
        "intraday-retention: trimming bars older than %s "
        "(today=%s, years=%d)",
        cutoff_iso,
        today.isoformat(),
        years,
    )

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
    }
