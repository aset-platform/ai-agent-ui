"""Pipeline-step wrappers for the regime / factors / attribution
jobs (REGIME-1..7).

Two responsibilities the underlying ``run_*`` helpers don't carry:

1. **Signature adapter** — the scheduler + pipeline executor invoke
   step handlers as
   ``executor_fn(scope, run_id, repo, cancel_event=None, force=False)``.
   The original helpers take a free-form ``payload: dict``. These
   wrappers bridge the two.

2. **Idempotency + force override** — running a daily job twice in
   the same IST day re-does work that's already in the table. NaN-
   replaceable upsert handles correctness but wastes minutes of
   compute. These wrappers query the relevant output Iceberg/PG
   table for today's row and SKIP unless ``force=True`` is set.

   On ``force=True`` the wrapper pre-deletes today's row(s) so the
   re-run produces a clean overwrite (matches the "scoped delete +
   upsert" pattern from CLAUDE.md §5.1 / §4.3 #18).

All four wrappers are India-only — they read NIFTY/INDIAVIX-derived
features and join `.NS` tickers. ``scope`` arg is validated; non-
"india"/"all" → no-op skip.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from typing import Any

_logger = logging.getLogger(__name__)
IST_OFFSET_HOURS = 5.5


# --------------------------------------------------------------
# Helpers
# --------------------------------------------------------------

def _today_ist() -> date:
    """Today in IST — matches the scheduler's local TZ
    (docker-compose backend uses TZ=Asia/Kolkata) but stays
    explicit so a future TZ change doesn't silently drift."""
    from datetime import timedelta as _td
    now_utc = datetime.now(timezone.utc)
    ist_now = now_utc + _td(hours=IST_OFFSET_HOURS)
    return ist_now.date()


def _scope_supported(scope: str) -> bool:
    """India-only pipeline. ``all`` permitted because the
    canonical India Daily Pipeline uses scope="india", but the
    operator may park these in a scope=all pipeline too."""
    if not scope:
        return True
    return scope.lower() in {"india", "all"}


def _delete_iceberg_rows(
    table: str, predicate,
) -> None:
    """Best-effort scoped delete + DuckDB metadata invalidate.
    Skips silently if the table is empty or the catalog is
    unreachable — caller still proceeds with the recompute."""
    try:
        from stocks.create_tables import _get_catalog
        from backend.db.duckdb_engine import invalidate_metadata
        cat = _get_catalog()
        tbl = cat.load_table(table)
        tbl.delete(predicate)
        invalidate_metadata(table)
    except Exception as exc:  # pragma: no cover
        _logger.debug(
            "scoped delete on %s skipped: %s", table, exc,
        )


# --------------------------------------------------------------
# Step 1 — regime_classifier_daily
# --------------------------------------------------------------

def run_regime_classifier_step(
    scope: str,
    run_id: str,
    repo: Any,
    cancel_event: threading.Event | None = None,
    force: bool = False,
) -> dict:
    """Classifier wrapper — see module docstring."""
    if not _scope_supported(scope):
        _logger.info(
            "regime_classifier skipped: scope=%s not india/all",
            scope,
        )
        return {"skipped": True, "reason": "scope"}
    today = _today_ist()
    if not force and _regime_history_has_today(today):
        _logger.info(
            "regime_classifier skipped: row exists for %s",
            today,
        )
        return {"skipped": True, "reason": "already_ran_today"}
    if force:
        from pyiceberg.expressions import EqualTo
        _delete_iceberg_rows(
            "stocks.regime_history",
            EqualTo("bar_date", today),
        )
    from backend.algo.regime.classifier_job import (
        run_classifier_job,
    )
    out = run_classifier_job({}) or {}

    # ASETPLTFRM-380 — post-step data-quality assertions. The 2026-
    # 05-11 stale-VIX silent-success run motivated this: pipeline
    # reported status=success while writing vix_close=16.84 against
    # an actual ^INDIAVIX close of 18.55. Assertions surface that
    # delta as a data_quality_violation event so admin Data Health
    # can flag the run without blocking subsequent steps.
    try:
        _run_regime_classifier_assertions(
            out, run_id=run_id, today=today,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "regime_classifier assertion run failed: %s — "
            "step output left unflagged", exc,
        )

    return {"forced": force, **out}


def _run_regime_classifier_assertions(
    out: dict,
    *,
    run_id: str,
    today: date,
) -> None:
    """Evaluate post-step assertions on the regime classifier's
    output and emit ``data_quality_violation`` events for any
    failures (ASETPLTFRM-380)."""
    from backend.algo.backtest.event_writer import flush_events
    from backend.algo.pipeline.quality import (
        cross_source_close_enough,
        emit_violation_events,
        evaluate_assertions,
        value_in_range,
        value_is_not_nan,
    )
    from backend.db.duckdb_engine import query_iceberg_table

    rule_inputs = out.get("rule_inputs") or {}
    ctx: dict[str, Any] = {
        "vix_close": rule_inputs.get("vix_close"),
        "pct_above_50sma": rule_inputs.get("pct_above_50sma"),
        "stress_prob": out.get("stress_prob"),
    }

    # Cross-source freshness: pull what stocks.ohlcv has for
    # ^INDIAVIX on the same day. If both are populated, the
    # cross_source_close_enough assertion catches the stale-VIX
    # case (16.84 vs 18.55 → ~9% delta → warn).
    try:
        rows = query_iceberg_table(
            "stocks.ohlcv",
            "SELECT close FROM ohlcv "
            "WHERE ticker = '^INDIAVIX' "
            "ORDER BY date DESC LIMIT 1",
            [],
        )
        if rows and rows[0].get("close") is not None:
            ctx["expected_vix_close"] = float(rows[0]["close"])
    except Exception:  # noqa: BLE001
        pass

    assertions = [
        value_is_not_nan("vix_close"),
        value_is_not_nan("pct_above_50sma"),
        value_in_range(
            "pct_above_50sma", 0.0, 1.0, severity="error",
        ),
        value_in_range(
            "stress_prob", 0.0, 1.0, severity="warn",
        ),
        cross_source_close_enough(
            "vix_close",
            "expected_vix_close",
            tolerance_pct=5.0,
            severity="warn",
        ),
    ]
    report = evaluate_assertions(
        "regime_classifier_daily", assertions, ctx,
    )
    if not report.failed:
        return
    events: list[dict] = []
    emit_violation_events(
        report,
        pipeline_id="india_regime_daily",
        run_id=run_id,
        events_sink=events.append,
    )
    if events:
        try:
            flush_events(events)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "data_quality_violation flush failed",
                exc_info=True,
            )


def _regime_history_has_today(today: date) -> bool:
    from backend.db.duckdb_engine import query_iceberg_table
    try:
        rows = query_iceberg_table(
            "stocks.regime_history",
            "SELECT 1 FROM regime_history "
            "WHERE bar_date = ? LIMIT 1",
            [today],
        )
        return bool(rows)
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------
# Step 2 — regime_change_notifier
# --------------------------------------------------------------

def run_regime_notifier_step(
    scope: str,
    run_id: str,
    repo: Any,
    cancel_event: threading.Event | None = None,
    force: bool = False,
) -> dict:
    if not _scope_supported(scope):
        return {"skipped": True, "reason": "scope"}
    today = _today_ist()
    if not force and _regime_changed_event_today(today):
        _logger.info(
            "regime_notifier skipped: event already emitted %s",
            today,
        )
        return {"skipped": True, "reason": "event_already_emitted"}
    from backend.algo.jobs.regime_change_notifier import run_notifier
    out = run_notifier(as_of=today)
    return {"forced": force, "emitted": out is not None,
            "payload": out}


def _regime_changed_event_today(today: date) -> bool:
    """Check algo.events for a regime_changed payload with
    bar_date == today (string compare on ISO date inside JSON)."""
    from backend.db.duckdb_engine import query_iceberg_table
    try:
        rows = query_iceberg_table(
            "algo.events",
            "SELECT 1 FROM events "
            "WHERE type = 'regime_changed' "
            "AND ts_date = ? LIMIT 1",
            [today.isoformat()],
        )
        return bool(rows)
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------
# Step 3 — compute_daily_factors
# --------------------------------------------------------------

def run_factors_compute_step(
    scope: str,
    run_id: str,
    repo: Any,
    cancel_event: threading.Event | None = None,
    force: bool = False,
) -> dict:
    if not _scope_supported(scope):
        return {"skipped": True, "reason": "scope"}
    today = _today_ist()
    if not force and _daily_factors_has_today(today):
        _logger.info(
            "compute_daily_factors skipped: rows exist for %s",
            today,
        )
        return {"skipped": True, "reason": "already_ran_today"}
    if force:
        from pyiceberg.expressions import EqualTo
        _delete_iceberg_rows(
            "stocks.daily_factors",
            EqualTo("bar_date", today),
        )
    from backend.algo.factors.compute_job import run_compute_job
    n = run_compute_job(as_of=today, days=1)
    return {"forced": force, "rows_written": n}


def _daily_factors_has_today(today: date) -> bool:
    from backend.db.duckdb_engine import query_iceberg_table
    try:
        rows = query_iceberg_table(
            "stocks.daily_factors",
            "SELECT 1 FROM daily_factors "
            "WHERE bar_date = ? LIMIT 1",
            [today],
        )
        return bool(rows)
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------
# Step 4 — attribution_daily_brinson
# --------------------------------------------------------------

def run_attribution_brinson_step(
    scope: str,
    run_id: str,
    repo: Any,
    cancel_event: threading.Event | None = None,
    force: bool = False,
) -> dict:
    if not _scope_supported(scope):
        return {"skipped": True, "reason": "scope"}
    today = _today_ist()
    if not force and _attribution_daily_has_today(today):
        _logger.info(
            "attribution_brinson skipped: rows exist for %s",
            today,
        )
        return {"skipped": True, "reason": "already_ran_today"}
    if force:
        _delete_attribution_today(today)
    from backend.algo.attribution.job import daily_brinson_job
    out = daily_brinson_job({"as_of": today.isoformat()})
    return {"forced": force, **(out or {})}


def _attribution_daily_has_today(today: date) -> bool:
    """attribution_daily lives in PG. The job module already
    uses async sessions; mirror that via asyncio.run() so we
    don't introduce a new sync engine."""
    import asyncio

    async def _check() -> bool:
        from sqlalchemy import text
        from db.engine import get_session_factory
        async with get_session_factory()() as s:
            row = (await s.execute(text(
                "SELECT 1 FROM algo.attribution_daily "
                "WHERE bar_date = :d LIMIT 1"
            ), {"d": today})).first()
            return row is not None

    try:
        return asyncio.run(_check())
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------
# Step 5 — universe_snapshot_monthly (monthly-skip)
# --------------------------------------------------------------

def run_universe_snapshot_step(
    scope: str,
    run_id: str,
    repo: Any,
    cancel_event: threading.Event | None = None,
    force: bool = False,
) -> dict:
    """Universe snapshot wrapper — runs only when today is the
    1st of the month (matches the standalone monthly cron).
    Skips on every other day so the daily pipeline stays clean.
    Idempotency within the month: skips if the current month's
    rebalance row already exists in stocks.universe_snapshot."""
    if not _scope_supported(scope):
        return {"skipped": True, "reason": "scope"}
    today = _today_ist()
    if today.day != 1 and not force:
        return {
            "skipped": True,
            "reason": "monthly_only_runs_on_first",
        }
    if not force and _universe_snapshot_has_month(today):
        _logger.info(
            "universe_snapshot skipped: month %s-%02d done",
            today.year, today.month,
        )
        return {
            "skipped": True,
            "reason": "already_ran_this_month",
        }
    if force and today.day == 1:
        from pyiceberg.expressions import EqualTo
        _delete_iceberg_rows(
            "stocks.universe_snapshot",
            EqualTo("rebalance_date", today),
        )
    from backend.algo.universe.snapshot_job import (
        rebuild_universe_snapshot,
    )
    out = rebuild_universe_snapshot(today)
    return {"forced": force, **(out or {})}


def _universe_snapshot_has_month(today: date) -> bool:
    from backend.db.duckdb_engine import query_iceberg_table
    try:
        rows = query_iceberg_table(
            "stocks.universe_snapshot",
            "SELECT 1 FROM universe_snapshot "
            "WHERE rebalance_date = ? LIMIT 1",
            [date(today.year, today.month, 1)],
        )
        return bool(rows)
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------
# Step 6 — attribution_monthly_regression (monthly-skip)
# --------------------------------------------------------------

def run_attribution_regression_step(
    scope: str,
    run_id: str,
    repo: Any,
    cancel_event: threading.Event | None = None,
    force: bool = False,
) -> dict:
    """Monthly factor regression wrapper — runs only on 1st of
    month. Idempotency: skips if a row exists in PG
    algo.factor_regression with period_end == today's first-of-
    month minus 1 day (i.e. last month's regression already
    persisted)."""
    if not _scope_supported(scope):
        return {"skipped": True, "reason": "scope"}
    today = _today_ist()
    if today.day != 1 and not force:
        return {
            "skipped": True,
            "reason": "monthly_only_runs_on_first",
        }
    if not force and _factor_regression_has_month(today):
        _logger.info(
            "factor_regression skipped: month %s-%02d done",
            today.year, today.month,
        )
        return {
            "skipped": True,
            "reason": "already_ran_this_month",
        }
    if force and today.day == 1:
        _delete_factor_regression_month(today)
    from backend.algo.attribution.job import (
        monthly_factor_regression_job,
    )
    out = monthly_factor_regression_job({"as_of": today.isoformat()})
    return {"forced": force, **(out or {})}


def _factor_regression_has_month(today: date) -> bool:
    """Has *any* regression row been written for today's
    rebalance month? Mirrors the attribution check via async."""
    import asyncio

    async def _check() -> bool:
        from sqlalchemy import text
        from db.engine import get_session_factory
        async with get_session_factory()() as s:
            row = (await s.execute(text(
                "SELECT 1 FROM algo.factor_regression "
                "WHERE EXTRACT(YEAR FROM period_end) = :y "
                "  AND EXTRACT(MONTH FROM period_end) = :m "
                "LIMIT 1"
            ), {"y": today.year, "m": today.month})).first()
            return row is not None

    try:
        return asyncio.run(_check())
    except Exception:  # pragma: no cover
        return False


def _delete_factor_regression_month(today: date) -> None:
    import asyncio

    async def _delete() -> None:
        from sqlalchemy import text
        from db.engine import get_session_factory
        async with get_session_factory()() as s:
            await s.execute(text(
                "DELETE FROM algo.factor_regression "
                "WHERE EXTRACT(YEAR FROM period_end) = :y "
                "  AND EXTRACT(MONTH FROM period_end) = :m"
            ), {"y": today.year, "m": today.month})
            await s.commit()

    try:
        asyncio.run(_delete())
    except Exception as exc:  # pragma: no cover
        _logger.debug(
            "factor_regression pre-delete skipped: %s", exc,
        )


def _delete_attribution_today(today: date) -> None:
    import asyncio

    async def _delete() -> None:
        from sqlalchemy import text
        from db.engine import get_session_factory
        async with get_session_factory()() as s:
            await s.execute(text(
                "DELETE FROM algo.attribution_daily "
                "WHERE bar_date = :d"
            ), {"d": today})
            await s.commit()

    try:
        asyncio.run(_delete())
    except Exception as exc:  # pragma: no cover
        _logger.debug(
            "attribution_daily pre-delete skipped: %s", exc,
        )
