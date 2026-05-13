"""Daily 15:45 IST incremental keeper for ``stocks.intraday_bars``
(ASETPLTFRM-400 slice 1d).

Runs Mon-Fri at 15:45 IST (15 min after NSE close). Pulls the last
2 trading-day window's 15m / 5m / 1m bars for the top-200 universe
plus any ticker referenced in an active intraday LiveRuntime within
the previous 7 days. Idempotent re-runs overwrite existing rows
via the NaN-replaceable upsert in
``backend/algo/backtest/intraday_backfill``.

Wired via ``@register_job("intraday_bars_daily_ingest")`` in
``backend/jobs/executor.py``. The scheduler row is seeded by
``scripts/seed_intraday_keeper_job.py``.

The job is best-effort: per-ticker failures inside
``backfill_window`` are logged with ``exc_info=True`` and the run
continues; the aggregate failure count + sample failed tickers
land in the structured return payload so ``scheduler_runs`` can
surface them on the Data Health dashboard.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.algo.backtest.intraday_backfill import backfill_window
from backend.algo.backtest.intraday_quality import (
    run_post_ingest_assertions,
)
from backend.algo.broker.credentials_repo import BrokerCredentialsRepo
from backend.algo.broker.kite_client import KiteClient
from backend.algo.instruments.repo import InstrumentsRepo
from backend.db.engine import get_session_factory

_logger = logging.getLogger(__name__)


def _ist_today() -> date:
    """IST-local date (UTC+5:30)."""
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _default_window() -> tuple[date, date]:
    """``[yesterday, today]`` in IST. Two trading-day span absorbs
    Kite republishing yesterday's bars during today's session."""
    today = _ist_today()
    return today - timedelta(days=1), today


_DEFAULT_INTERVALS = (900, 300, 60)  # 15m, 5m, 1m
_ACTIVE_MIS_LOOKBACK_DAYS = 7


async def _resolve_keeper_user(session) -> dict[str, Any] | None:
    """Pick the user whose Kite credentials the daily keeper will
    use.

    MVP: the first user in ``algo.broker_credentials`` with a
    non-expired access_token. Long-term this can be specialised
    via payload (``payload={"user_id": "..."}``) so that
    multi-user deployments rotate the keeper or shard the
    universe per user. The current single-user posture matches how
    the instrument-refresh and reconciliation jobs operate.
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


def _resolve_top200_universe(anchor: date) -> list[str]:
    """Latest top-200 cohort as of ``anchor``. Returns empty list
    when ``stocks.universe_snapshot`` is empty (fresh dev box).
    """
    from backend.algo.universe.pit_resolver import resolve_pit_universe

    return resolve_pit_universe(anchor)


def _active_intraday_tickers_last_7d(today: date) -> list[str]:
    """Tickers referenced in any non-daily LiveRuntime in the past
    7 days. Reads ``algo.events`` via DuckDB. Best-effort: returns
    empty on failure rather than blocking the keeper.

    The intent is to keep the daily-keeper covering whatever
    operators are actually trading even if those tickers fall out
    of the top-200 cohort.
    """
    try:
        from backend.db.duckdb_engine import query_iceberg_table
    except Exception:  # pragma: no cover
        return []
    cutoff = today - timedelta(days=_ACTIVE_MIS_LOOKBACK_DAYS)
    try:
        rows = query_iceberg_table(
            "algo.events",
            "SELECT DISTINCT json_extract_string(payload_json,"
            "  '$.ticker') AS ticker "
            "FROM events "
            "WHERE mode = 'live' "
            "  AND ts_date >= ? "
            "  AND json_extract_string(payload_json,"
            "        '$.interval_sec') IS NOT NULL "
            "  AND json_extract_string(payload_json,"
            "        '$.interval_sec') != '86400'",
            [cutoff.strftime("%Y-%m-%d")],
        )
    except Exception as exc:  # pragma: no cover
        _logger.warning(
            "intraday-keeper: active-MIS scan failed: %s",
            exc,
            exc_info=True,
        )
        return []
    return sorted({r["ticker"] for r in rows or [] if r.get("ticker")})


async def _resolve_instrument_tokens(
    session,
    tickers: list[str],
) -> dict[str, int]:
    """Reverse ``InstrumentsRepo.get_tokens_for_tickers``'s
    ``{token: ticker}`` to ``{ticker: token}``."""
    repo = InstrumentsRepo()
    token_to_ticker = await repo.get_tokens_for_tickers(
        session,
        tickers,
    )
    return {t: tok for tok, t in token_to_ticker.items()}


async def run_intraday_bars_daily_ingest_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Daily incremental backfill orchestration.

    Payload keys (all optional):
      - ``user_id``: explicit Kite OAuth user. Default = first
        user with valid credentials.
      - ``intervals``: list of ``interval_sec`` values to keep
        current. Default ``[900, 300, 60]``.
      - ``start`` / ``end``: ISO date strings. Default
        ``[today-1, today]`` in IST.
      - ``batch_size``: passed through to ``backfill_window``.

    Returns a structured summary suitable for ``scheduler_runs``.
    """
    payload = payload or {}
    intervals = list(payload.get("intervals") or _DEFAULT_INTERVALS)
    start, end = _default_window()
    if payload.get("start"):
        start = date.fromisoformat(payload["start"])
    if payload.get("end"):
        end = date.fromisoformat(payload["end"])
    batch_size = int(payload.get("batch_size") or 50)

    factory = get_session_factory()
    async with factory() as session:
        if payload.get("user_id"):
            user_id = UUID(str(payload["user_id"]))
            creds = await BrokerCredentialsRepo().load(
                session,
                user_id,
            )
            keeper = {"user_id": user_id, "creds": creds} if creds else None
        else:
            keeper = await _resolve_keeper_user(session)
        if not keeper:
            _logger.warning(
                "intraday-keeper: no user with valid Kite "
                "credentials — skipping run",
            )
            return {
                "status": "skipped_no_credentials",
                "intervals": intervals,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        creds = keeper["creds"]
        if creds.get("access_token_expired") or not creds.get(
            "access_token",
        ):
            _logger.warning(
                "intraday-keeper: user %s has expired/missing "
                "access_token — skipping",
                keeper["user_id"],
            )
            return {
                "status": "skipped_token_expired",
                "user_id": str(keeper["user_id"]),
                "intervals": intervals,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }

        tickers = sorted(
            set(
                _resolve_top200_universe(end)
                + _active_intraday_tickers_last_7d(end)
            )
        )
        if not tickers:
            _logger.warning(
                "intraday-keeper: empty universe — skipping",
            )
            return {
                "status": "skipped_empty_universe",
                "user_id": str(keeper["user_id"]),
                "intervals": intervals,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        kite = KiteClient(
            api_key=creds["api_key"],
            access_token=creds["access_token"],
            dry_run=False,
        )
        tokens = await _resolve_instrument_tokens(session, tickers)

    per_interval: dict[int, dict[str, Any]] = {}
    total_bars = 0
    total_failed = 0
    sample_failures: list[tuple[str, str]] = []
    quality_violations = 0
    for interval_sec in intervals:
        # Each batch's bars flow through the 5 slice-1e assertions
        # before the next batch starts; failures become
        # ``data_quality_violation`` events on ``algo.events`` and
        # increment the keeper's roll-up counter.
        def _assertions_hook(
            bars: list,
            iv: int,
            interval_sec=interval_sec,
        ) -> None:
            nonlocal quality_violations
            report = run_post_ingest_assertions(
                bars=bars,
                interval_sec=iv,
                pipeline_id="intraday_bars_daily_keeper",
                user_id=str(keeper["user_id"]),
            )
            quality_violations += len(report.failed)

        stats = backfill_window(
            kite=kite,
            tickers=tickers,
            instrument_tokens=tokens,
            interval_sec=interval_sec,
            start=start,
            end=end,
            source="kite_daily_keeper",
            batch_size=batch_size,
            on_batch_written=_assertions_hook,
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
        "intraday-keeper: done tickers=%d intervals=%s "
        "bars=%d failures=%d quality_violations=%d",
        len(tickers),
        intervals,
        total_bars,
        total_failed,
        quality_violations,
    )
    return {
        "status": "ok",
        "user_id": str(keeper["user_id"]),
        "ticker_count": len(tickers),
        "intervals": intervals,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bars_written": total_bars,
        "tickers_failed": total_failed,
        "quality_violations": quality_violations,
        "sample_failures": sample_failures[:10],
        "per_interval": per_interval,
    }
