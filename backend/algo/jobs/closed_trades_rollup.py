"""Daily rollup job — reads algo.events (paper + live fills) and
materializes closed trades into algo.closed_trades.

Runs at 16:30 IST Mon-Fri (see scripts/seed_closed_trades_rollup.py),
well after market close (15:30 IST) and after the 15:45 IST budget
reconciliation job so any late postback fills have settled.

Per CLAUDE.md §5.1: reads Iceberg via query_iceberg_table (no
market-hours load — this never runs during trading hours), writes
Postgres via disposable_pg_session (NullPool, per-call).

Re-derives trades from a trailing window every run (default 400
days, matching the OHLCV warmup convention) rather than carrying
forward state, because a position can open on day N and close on
day N+40. The idempotent unique key (buy_event_id, sell_event_id)
on algo.closed_trades makes re-running safe — ON CONFLICT DO
NOTHING skips trades already materialized.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from backend.algo.attribution.trade_pairing import (
    pair_fills_by_strategy_and_ticker,
)

_logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 400
_ROLLUP_MODES = ("paper", "live")


def _ist_today() -> date:
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def run_closed_trades_rollup_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sync entrypoint (scheduler / seed / backfill callers).

    Payload keys (all optional):
      - ``window_days``: trailing lookback for the Iceberg scan.
        Default 400. The one-time backfill script (Task 5) passes
        a larger bounded value (3650, ~10 years) — never
        unbounded, per the "no full-table scans" constraint.
      - ``today``: ISO date override (testing). Default IST-today.
      - ``dry_run``: compute + log counts, skip the PG upsert.
    """
    import asyncio

    return asyncio.run(_run(payload or {}))


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.db.engine import disposable_pg_session

    today = (
        date.fromisoformat(payload["today"])
        if payload.get("today")
        else _ist_today()
    )
    window_days = payload.get("window_days", _DEFAULT_WINDOW_DAYS)
    dry_run = bool(payload.get("dry_run", False))

    events = _fetch_fill_events(today, window_days)
    trades = pair_fills_by_strategy_and_ticker(events)
    # Attach user_id/mode back onto each trade — the pairing helper
    # only tracks strategy_id/ticker, so recover them from the
    # source events keyed by buy_event_id.
    meta_by_event_id = {
        ev["event_id"]: (ev.get("user_id"), ev.get("mode"))
        for ev in events
    }
    for t in trades:
        user_id, mode = meta_by_event_id.get(
            t["buy_event_id"], (None, None),
        )
        t["user_id"] = user_id
        t["mode"] = mode
    trades = [t for t in trades if t["user_id"] and t["mode"]]

    _logger.info(
        "closed-trades-rollup: today=%s window_days=%s "
        "events=%d trades=%d dry_run=%s",
        today.isoformat(), window_days, len(events),
        len(trades), dry_run,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "today": today.isoformat(),
            "events_scanned": len(events),
            "trades_computed": len(trades),
        }

    upserted = 0
    if trades:
        async with disposable_pg_session() as session:
            for t in trades:
                result = await session.execute(
                    text(
                        "INSERT INTO algo.closed_trades "
                        "(user_id, strategy_id, mode, ticker, qty, "
                        " avg_price, fill_price, opened_at, "
                        " closed_at, opened_at_ts_ns, "
                        " closed_at_ts_ns, realised_pnl_inr, "
                        " return_pct, exit_reason, dry_run, "
                        " buy_event_id, sell_event_id) "
                        "VALUES (:user_id, :strategy_id, :mode, "
                        " :ticker, :qty, :avg_price, :fill_price, "
                        " :opened_at, :closed_at, "
                        " :opened_at_ts_ns, :closed_at_ts_ns, "
                        " :realised_pnl_inr, :return_pct, "
                        " :exit_reason, :dry_run, :buy_event_id, "
                        " :sell_event_id) "
                        "ON CONFLICT (buy_event_id, sell_event_id) "
                        "DO NOTHING"
                    ),
                    {
                        "user_id": t["user_id"],
                        "strategy_id": t["strategy_id"],
                        "mode": t["mode"],
                        "ticker": t["ticker"],
                        "qty": t["qty"],
                        "avg_price": t["avg_price"],
                        "fill_price": t["fill_price"],
                        "opened_at": t["opened_at"],
                        "closed_at": t["closed_at"],
                        "opened_at_ts_ns": t["opened_at_ts_ns"],
                        "closed_at_ts_ns": t["closed_at_ts_ns"],
                        "realised_pnl_inr": t["realised_pnl_inr"],
                        "return_pct": t["return_pct"],
                        "exit_reason": t["exit_reason"],
                        "dry_run": t["dry_run"],
                        "buy_event_id": t["buy_event_id"],
                        "sell_event_id": t["sell_event_id"],
                    },
                )
                if result.rowcount:
                    upserted += 1
            await session.commit()

    if upserted:
        try:
            from cache import get_cache
            get_cache().invalidate("cache:algo:perf:*")
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "closed-trades-rollup: cache invalidate "
                "failed (non-fatal): %s", exc,
            )

    return {
        "status": "ok",
        "today": today.isoformat(),
        "events_scanned": len(events),
        "trades_computed": len(trades),
        "trades_upserted": upserted,
    }


def _fetch_fill_events(
    today: date, window_days: int,
) -> list[dict[str, Any]]:
    """Pull paper + live fill events (plus order_submitted_live, whose
    ``source='panic_close'`` marker is the only place a panic exit's
    intent survives — the fill itself carries no exit_reason) across
    every user for the trailing window. Column-projected,
    date-filtered — never a full-table scan, including the backfill
    caller (Task 5), which passes a large but bounded ``window_days``
    rather than an unbounded scan.

    ``order_submitted_live`` rows are ignored by the pairing loop's
    fill-type filter; they are carried only so the pairing helper can
    build its kite_order_id → panic-close map."""
    from backend.db.duckdb_engine import query_iceberg_table

    modes_clause = " OR ".join(
        "mode = ?" for _ in _ROLLUP_MODES
    )
    start = today - timedelta(days=window_days)
    sql = (
        "SELECT event_id, user_id, strategy_id, mode, type, "
        "       payload_json, ts_ns "
        "FROM events "
        f"WHERE ({modes_clause}) "
        "  AND type IN ('order_filled', 'order_filled_live', "
        "               'order_submitted_live') "
        "  AND ts_date >= ? AND ts_date <= ? "
        "ORDER BY ts_ns"
    )
    params = [*_ROLLUP_MODES, start.isoformat(), today.isoformat()]

    try:
        return query_iceberg_table("algo.events", sql, params)
    except Exception:  # noqa: BLE001
        _logger.exception(
            "closed-trades-rollup: events query failed",
        )
        return []
