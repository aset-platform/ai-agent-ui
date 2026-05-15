"""Daily 15:45 IST incremental keeper for
``stocks.index_intraday_bars`` (ASETPLTFRM-402 / FE-6).

Runs Mon-Fri at 15:45 IST as step 2 of the Intraday Bars Daily
Pipeline — sits between the per-stock equity ingest (step 1) and
the feature compute (step 3) so FE-8's cross-sectional features
(forthcoming) see both the per-ticker bars and the NIFTY 50 /
sector-index bars from the same compute run.

Universe is the small list in
``backend.algo.jobs._index_universe.INDEX_UNIVERSE`` — broad-market
NIFTY 50 plus the sector indices the daily factor library already
keys off (banks, auto, FMCG, IT, financial services, pharma,
metal, energy, realty). The list is the **Kite tradingsymbol
verbatim** (e.g. ``"NIFTY 50"``) rather than the Yahoo ``^NSEI``
notation — see ``_index_universe.py`` docstring for the rename.

Wired via ``@register_job("index_intraday_bars_daily_ingest")`` in
``backend/jobs/executor.py``. The pipeline DAG that schedules it
is seeded by ``scripts/seed_intraday_keeper_pipeline.py``.

Best-effort: per-symbol failures (missing instrument_token, Kite
fetch error) are logged with ``exc_info=True`` and recorded in
the structured return payload; the run continues so one bad
index never strands the rest.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.algo.backtest.index_intraday_backfill import (
    backfill_index_window,
)
from backend.algo.broker.credentials_repo import BrokerCredentialsRepo
from backend.algo.broker.kite_client import KiteClient
from backend.algo.jobs._index_universe import INDEX_UNIVERSE
from backend.db.engine import disposable_pg_session

_logger = logging.getLogger(__name__)


def _ist_today() -> date:
    """IST-local date (UTC+5:30)."""
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def _default_window() -> tuple[date, date]:
    """``[yesterday, today]`` in IST. Two trading-day span absorbs
    Kite republishing yesterday's bars during today's session."""
    today = _ist_today()
    return today - timedelta(days=1), today


# 15m (900s), 5m (300s), 1m (60s) are all wired end-to-end via the
# backfill helper. Only 15m is enabled in the daily keeper today
# because the broad-market RS feature is daily-cadence — add
# ``300`` / ``60`` to ``_DEFAULT_INTERVALS`` when a finer-cadence
# index consumer ships.
_AVAILABLE_INTERVALS = (900, 300, 60)
_DEFAULT_INTERVALS = (900,)


async def _resolve_keeper_user(session) -> dict[str, Any] | None:
    """Pick the user whose Kite credentials the daily keeper will
    use.

    MVP: the first user in ``algo.broker_credentials`` with a
    non-expired access_token. Mirrors
    ``intraday_bars_daily_ingest._resolve_keeper_user`` exactly so
    both keepers share the same single-user posture.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT user_id FROM algo.broker_credentials "
                    "ORDER BY updated_at DESC NULLS LAST"
                ),
            )
        )
        .mappings()
        .all()
    )
    repo = BrokerCredentialsRepo()
    for r in rows:
        user_id = UUID(str(r["user_id"]))
        creds = await repo.load(session, user_id)
        if not creds:
            continue
        if creds.get("access_token_expired"):
            continue
        if not creds.get("access_token"):
            continue
        return {"user_id": user_id, "creds": creds}
    return None


async def run_index_intraday_bars_daily_ingest_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Daily incremental backfill orchestration for the NSE index
    universe.

    Payload keys (all optional):
      - ``user_id``: explicit Kite OAuth user. Default = first user
        with valid credentials.
      - ``index_symbols``: override ``INDEX_UNIVERSE`` (the list of
        Kite tradingsymbols to refresh).
      - ``intervals``: list of ``interval_sec`` values. Default
        ``[900]``.
      - ``interval_sec``: convenience single-value alias; appended
        to ``intervals`` when set.
      - ``period_start`` / ``period_end``: ISO date strings.
        Default ``[today-1, today]`` IST.
      - ``start`` / ``end``: aliases for the above.
      - ``force``: bypass any external-skip semantics (today a
        no-op marker; reserved for future market-holiday gates).
      - ``batch_size``: passed through to ``backfill_index_window``.

    Returns a structured summary suitable for ``scheduler_runs``.
    """
    payload = payload or {}
    intervals = list(payload.get("intervals") or _DEFAULT_INTERVALS)
    extra_iv = payload.get("interval_sec")
    if extra_iv and extra_iv not in intervals:
        intervals.append(int(extra_iv))
    start, end = _default_window()
    raw_start = payload.get("period_start") or payload.get("start")
    raw_end = payload.get("period_end") or payload.get("end")
    if raw_start:
        start = date.fromisoformat(str(raw_start))
    if raw_end:
        end = date.fromisoformat(str(raw_end))
    batch_size = int(payload.get("batch_size") or 10)
    forced = bool(payload.get("force"))
    symbols_override = payload.get("index_symbols")
    if symbols_override:
        index_symbols = list(symbols_override)
    else:
        index_symbols = list(INDEX_UNIVERSE)

    # Scheduler jobs spawn under their own ``asyncio.run`` event
    # loop. Using the uvicorn-cached engine here raises "Future
    # attached to a different loop"; ``disposable_pg_session``
    # gives us a per-call NullPool engine, scoped to this loop.
    async with disposable_pg_session() as session:
        if payload.get("user_id"):
            user_id = UUID(str(payload["user_id"]))
            creds = await BrokerCredentialsRepo().load(
                session,
                user_id,
            )
            keeper = (
                {"user_id": user_id, "creds": creds} if creds else None
            )
        else:
            keeper = await _resolve_keeper_user(session)
        if not keeper:
            _logger.warning(
                "index-intraday-keeper: no user with valid Kite "
                "credentials — skipping run",
            )
            return {
                "status": "skipped_no_credentials",
                "intervals": intervals,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "forced": forced,
            }
        creds = keeper["creds"]
        if creds.get("access_token_expired") or not creds.get(
            "access_token",
        ):
            _logger.warning(
                "index-intraday-keeper: user %s has expired/"
                "missing access_token — skipping",
                keeper["user_id"],
            )
            return {
                "status": "skipped_token_expired",
                "user_id": str(keeper["user_id"]),
                "intervals": intervals,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "forced": forced,
            }

        if not index_symbols:
            _logger.warning(
                "index-intraday-keeper: empty index universe — "
                "skipping",
            )
            return {
                "status": "skipped_empty_universe",
                "user_id": str(keeper["user_id"]),
                "intervals": intervals,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "forced": forced,
            }

        kite = KiteClient(
            api_key=creds["api_key"],
            access_token=creds["access_token"],
            dry_run=False,
        )

        per_interval: dict[int, dict[str, Any]] = {}
        total_bars = 0
        total_failed = 0
        sample_failures: list[tuple[str, str]] = []
        for interval_sec in intervals:
            stats = await backfill_index_window(
                index_symbols=index_symbols,
                interval_sec=interval_sec,
                period_start=start,
                period_end=end,
                kite_client=kite,
                pg_session=session,
                source="kite_index_daily_keeper",
                batch_size=batch_size,
            )
            per_interval[interval_sec] = {
                "tickers_done": stats.tickers_done,
                "tickers_failed": stats.tickers_failed,
                "bars_written": stats.bars_written,
                "wall_clock_s": round(stats.wall_clock_s, 2),
            }
            total_bars += stats.bars_written
            total_failed += stats.tickers_failed
            if stats.failures and len(sample_failures) < 10:
                sample_failures.extend(
                    stats.failures[: 10 - len(sample_failures)],
                )

    _logger.info(
        "index-intraday-keeper: done symbols=%d intervals=%s "
        "bars=%d failures=%d",
        len(index_symbols),
        intervals,
        total_bars,
        total_failed,
    )
    return {
        "status": "ok",
        "user_id": str(keeper["user_id"]),
        "ticker_count": len(index_symbols),
        "intervals": intervals,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bars_written": total_bars,
        "tickers_failed": total_failed,
        "sample_failures": sample_failures[:10],
        "per_interval": per_interval,
        "forced": forced,
    }
