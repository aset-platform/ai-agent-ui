"""Daily-cadence (interval_sec=86400) feature compute step —
FE-15a (ASETPLTFRM-419).

Reads the Nifty 500 universe's daily ``stocks.ohlcv`` rows over
a configurable window with warmup tail, runs the daily feature
engine
(:func:`backend.algo.features.daily_engine.compute_daily_features_for_universe`),
and bulk-writes the resulting long-format feature rows to
``stocks.intraday_features`` at ``interval_sec=86400`` via the
same NaN-replaceable upsert pattern as the intraday job.

Daily features land in the SAME Iceberg table as the intraday
features (per FE-15 spec §6 — store stays cadence-agnostic;
``interval_sec`` column is the discriminator). The cross-cadence
overlay in FE-15b's per-bar helper renames them with a ``_1d``
suffix when injected into an intraday strategy's per-bar
features dict (per FE-15 spec §5).

Re-runs are idempotent: scoped pre-delete on
``(ticker, bar_date, interval_sec=86400)`` for the touched
batch, then append.

Wired via ``@register_job("daily_features_daily_compute")`` in
``backend/jobs/executor.py``. Scheduled daily at 23:30 IST
(after ``compute_daily_factors`` at 23:00).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import pyarrow as pa
from pyiceberg.expressions import And, In

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.algo.backtest.types import BarData
from backend.algo.features.daily_engine import (
    compute_daily_features_for_universe,
)
from backend.algo.features.version import FEATURE_SET_VERSION
from backend.cache import get_cache
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)
from backend.db.engine import disposable_pg_session

_logger = logging.getLogger(__name__)

OHLCV_TABLE = "stocks.ohlcv"
INTRADAY_FEATURES_TABLE = "stocks.intraday_features"

INTERVAL_SEC = 86400  # daily cadence — hardcoded for this job.
DEFAULT_BATCH_SIZE = 50
# Warmup tail in calendar days. SMA200 needs ~200 trading days
# ≈ 290 calendar days incl. weekends/holidays. We use 320 to
# tolerate a long holiday block (Diwali / Christmas / Holi)
# without losing the first window-day's SMA200.
DEFAULT_WARMUP_DAYS = 320
# Default rolling-window for scheduled runs — write the last
# 30 days each run. Idempotent overwrite via scoped pre-delete.
DEFAULT_WRITE_WINDOW_DAYS = 30


def _ist_today() -> date:
    """IST-local date (UTC+5:30)."""
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def _default_write_window() -> tuple[date, date]:
    """Default write window = trailing
    ``DEFAULT_WRITE_WINDOW_DAYS`` IST calendar days."""
    today = _ist_today()
    return today - timedelta(days=DEFAULT_WRITE_WINDOW_DAYS - 1), today


def _utc_midnight_ns(d: date) -> int:
    """Deterministic ``bar_open_ts_ns`` for a daily bar — UTC
    midnight of ``d`` in nanoseconds since the Unix epoch.

    Used as the primary-key component on rows at
    ``interval_sec=86400``. Pure function of the bar_date; no
    real-time / timezone gymnastics needed.
    """
    return int(
        datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp()
        * 1_000_000_000
    )


def _features_arrow_schema() -> pa.Schema:
    """Arrow schema matching ``stocks.intraday_features`` —
    identical to the intraday job (same Iceberg table). Every
    column is ``nullable=False`` to match the Iceberg schema's
    ``required=True``.
    """
    return pa.schema(
        [
            pa.field("ticker", pa.string(), nullable=False),
            pa.field("bar_open_ts_ns", pa.int64(), nullable=False),
            pa.field("bar_date", pa.string(), nullable=False),
            pa.field("year_month", pa.string(), nullable=False),
            pa.field("interval_sec", pa.int64(), nullable=False),
            pa.field("feature_name", pa.string(), nullable=False),
            pa.field("feature_value", pa.float64(), nullable=False),
            pa.field(
                "feature_set_version",
                pa.string(),
                nullable=False,
            ),
            pa.field(
                "written_at",
                pa.timestamp("us"),
                nullable=False,
            ),
        ]
    )


async def _resolve_nifty500_universe(session) -> list[str]:
    """Return ``<symbol>.NS`` tickers tagged ``nifty500`` in
    ``stock_master``. Single source of truth shared with the
    intraday feature compute job + bars keeper.
    """
    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT sm.yf_ticker "
                "FROM stock_master sm "
                "JOIN stock_tags st ON st.stock_id = sm.id "
                "WHERE st.tag = 'nifty500' "
                "  AND st.removed_at IS NULL "
                "  AND sm.is_active "
                "ORDER BY sm.yf_ticker"
            ),
        )
    ).all()
    return [r[0] for r in rows if r[0]]


def _load_daily_bars_for_tickers(
    *,
    tickers: list[str],
    start: date,
    end: date,
) -> dict[str, list[BarData]]:
    """Batched read of ``stocks.ohlcv`` for a list of tickers
    over the ``[start, end]`` window.

    Executes ONE DuckDB query with ``WHERE ticker IN (...)`` and
    groups the result by ticker (CLAUDE.md §4.1 #1).

    Returns ``{ticker: [bars...]}``. Each bar's
    ``bar_open_ts_ns`` is synthesised from ``date`` as UTC
    midnight ns so downstream compute / write code can treat
    daily and intraday bars uniformly.
    """
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    sql = (
        "SELECT ticker, date, open, high, low, close, volume "
        "FROM ohlcv "
        f"WHERE ticker IN ({placeholders}) "
        "  AND date BETWEEN ? AND ? "
        "ORDER BY ticker, date"
    )
    params: list[Any] = list(tickers) + [
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    ]
    try:
        rows = query_iceberg_table(OHLCV_TABLE, sql, params)
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[daily-features] batched bar load failed "
            "(tickers=%d, %s..%s): %s",
            len(tickers),
            start,
            end,
            exc,
            exc_info=True,
        )
        return {}
    out: dict[str, list[BarData]] = {}
    for r in rows or []:
        raw_date = r.get("date")
        if isinstance(raw_date, date):
            bar_d = raw_date
        else:
            try:
                bar_d = date.fromisoformat(str(raw_date)[:10])
            except (TypeError, ValueError):
                continue
        try:
            bar = BarData(
                ticker=r["ticker"],
                date=bar_d,
                open=Decimal(str(r["open"])),
                high=Decimal(str(r["high"])),
                low=Decimal(str(r["low"])),
                close=Decimal(str(r["close"])),
                volume=int(r["volume"] or 0),
                bar_open_ts_ns=_utc_midnight_ns(bar_d),
            )
        except (TypeError, ValueError, KeyError) as exc:
            _logger.warning(
                "[daily-features] dropping malformed bar row "
                "for %s @ %s: %s",
                r.get("ticker"),
                raw_date,
                exc,
            )
            continue
        out.setdefault(bar.ticker, []).append(bar)
    return out


def _panel_to_arrow_rows(
    *,
    panel: dict[str, dict[int, dict[str, Any]]],
    bars_by_ticker: dict[str, list[BarData]],
    feature_set_version: str,
    written_at: datetime,
    write_window: tuple[date, date] | None,
) -> list[dict[str, Any]]:
    """Flatten the per-ticker daily panel into long-format Arrow
    rows. Skips NaN / non-finite numeric values and rows whose
    ``bar_date`` falls outside ``write_window`` (the warmup tail
    is read but not written).
    """
    rows: list[dict[str, Any]] = []
    write_start, write_end = (
        write_window if write_window is not None else (None, None)
    )
    for ticker, by_ts in panel.items():
        bar_lookup = {
            b.bar_open_ts_ns: b
            for b in bars_by_ticker.get(ticker, [])
            if b.bar_open_ts_ns is not None
        }
        for ts_ns, feats in by_ts.items():
            bar = bar_lookup.get(ts_ns)
            if bar is None:
                continue
            if write_start is not None and bar.date < write_start:
                continue
            if write_end is not None and bar.date > write_end:
                continue
            bar_date_str = bar.date.strftime("%Y-%m-%d")
            year_month = bar_date_str[:7]
            for feat_name, feat_val in feats.items():
                try:
                    fv = float(feat_val)
                except (TypeError, ValueError):
                    # Non-numeric (shouldn't happen for daily —
                    # daily engine emits only numeric features
                    # — but skip defensively).
                    continue
                if math.isnan(fv) or math.isinf(fv):
                    continue
                rows.append(
                    {
                        "ticker": ticker,
                        "bar_open_ts_ns": int(ts_ns),
                        "bar_date": bar_date_str,
                        "year_month": year_month,
                        "interval_sec": INTERVAL_SEC,
                        "feature_name": str(feat_name),
                        "feature_value": fv,
                        "feature_set_version": feature_set_version,
                        "written_at": written_at,
                    }
                )
    return rows


def _write_features_batch(
    *,
    arrow_rows: list[dict[str, Any]],
) -> int:
    """NaN-replaceable upsert for one batch of daily feature rows.

    Pre-deletes the cross-product of incoming ``(ticker,
    bar_date)`` at ``interval_sec=86400``, then appends. Scoped
    to the batch — never wipes intraday rows or other tickers.

    Daily-only scope on ``interval_sec`` is critical: a
    coarser predicate would wipe the 47.8M intraday rows on
    every daily run.
    """
    if not arrow_rows:
        return 0
    schema = _features_arrow_schema()
    cols = {k: [r[k] for r in arrow_rows] for k in schema.names}
    arrow_tbl = pa.table(cols, schema=schema)

    tickers = sorted({r["ticker"] for r in arrow_rows})
    bar_dates = sorted({r["bar_date"] for r in arrow_rows})
    year_months = sorted({r["year_month"] for r in arrow_rows})

    def _do_upsert() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(INTRADAY_FEATURES_TABLE)
        try:
            tbl.delete(
                And(
                    In("ticker", tickers),
                    And(
                        In("bar_date", bar_dates),
                        In("interval_sec", [INTERVAL_SEC]),
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.debug(
                "daily_features pre-delete skipped (%s): %s",
                INTRADAY_FEATURES_TABLE,
                exc,
            )
        tbl.append(arrow_tbl)

    retry_iceberg_op(INTRADAY_FEATURES_TABLE, _do_upsert)
    invalidate_metadata(INTRADAY_FEATURES_TABLE)
    _invalidate_feature_chunk_cache(year_months=year_months)
    return arrow_tbl.num_rows


def _invalidate_feature_chunk_cache(*, year_months: list[str]) -> None:
    """Best-effort wildcard invalidation of feature-chunk cache
    keys for the months that were just written. Mirrors the
    ``cache:feature:chunk:{ticker}:{year_month}:{interval_sec}``
    schema used by the FE-4 loader.
    """
    try:
        cache = get_cache()
        for ym in year_months:
            cache.invalidate(f"cache:feature:chunk:*:{ym}:{INTERVAL_SEC}")
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[daily-features] cache invalidate skipped "
            "year_months=%s: %s",
            year_months,
            exc,
            exc_info=True,
        )


def _compute_and_write_batch(
    *,
    tickers: list[str],
    read_start: date,
    read_end: date,
    write_window: tuple[date, date],
    feature_set_version: str,
    stats: dict[str, Any],
) -> int:
    """Per-batch worker — load bars (with warmup tail), compute
    daily features, write rows whose bar_date falls in the
    write window.

    Per-ticker compute failures append to ``stats.failures``
    with ``(ticker, reason[:200])`` but don't abort the batch.
    Returns the row count actually written.
    """
    try:
        bars_by_ticker = _load_daily_bars_for_tickers(
            tickers=tickers,
            start=read_start,
            end=read_end,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[daily-features] batched bar fetch crashed "
            "(tickers=%d, %s..%s): %s",
            len(tickers),
            read_start,
            read_end,
            exc,
            exc_info=True,
        )
        for tk in tickers:
            stats["tickers_failed"] += 1
            stats["failures"].append((tk, f"fetch:{exc!s}"[:200]))
        return 0
    if not bars_by_ticker:
        return 0
    try:
        panel = compute_daily_features_for_universe(
            bars_by_ticker,
            feature_set_version=feature_set_version,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[daily-features] compute batch failed "
            "(tickers=%d): %s",
            len(bars_by_ticker),
            exc,
            exc_info=True,
        )
        for tk in bars_by_ticker:
            stats["tickers_failed"] += 1
            stats["failures"].append(
                (tk, f"compute:{exc!s}"[:200]),
            )
        return 0

    written_at = datetime.now(timezone.utc).replace(
        microsecond=0,
        tzinfo=None,
    )
    arrow_rows = _panel_to_arrow_rows(
        panel=panel,
        bars_by_ticker=bars_by_ticker,
        feature_set_version=feature_set_version,
        written_at=written_at,
        write_window=write_window,
    )
    if not arrow_rows:
        for tk in bars_by_ticker:
            stats["tickers_processed"] += 1
        return 0
    try:
        written = _write_features_batch(arrow_rows=arrow_rows)
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[daily-features] batch upsert failed "
            "(tickers=%d, rows=%d): %s",
            len(bars_by_ticker),
            len(arrow_rows),
            exc,
            exc_info=True,
        )
        for tk in bars_by_ticker:
            stats["tickers_failed"] += 1
            stats["failures"].append(
                (tk, f"upsert:{exc!s}"[:200]),
            )
        return 0
    for tk in bars_by_ticker:
        stats["tickers_processed"] += 1
    return written


def _empty_stats(
    *,
    write_start: date,
    write_end: date,
    universe_size: int = 0,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> dict[str, Any]:
    """Skeleton stats dict consumed by the scheduler."""
    return {
        "universe_size": universe_size,
        "tickers_processed": 0,
        "tickers_failed": 0,
        "rows_written": 0,
        "feature_set_version": feature_set_version,
        "window": [write_start.isoformat(), write_end.isoformat()],
        "interval_sec": INTERVAL_SEC,
        "failures": [],
    }


async def run_daily_features_daily_compute_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute daily (1d) features for the Nifty 500 universe
    over a write window with warmup tail. Writes to
    ``stocks.intraday_features`` at ``interval_sec=86400``.

    Payload keys (all optional):
      - ``force`` (bool, default False): accepted for symmetry;
        re-runs are cheap thanks to NaN-replaceable upsert.
      - ``period_start`` / ``period_end`` (ISO ``YYYY-MM-DD``):
        explicit WRITE window. Default trailing 30 days IST.
      - ``warmup_days`` (int, default 320): how many calendar
        days BEFORE ``period_start`` to also read for indicator
        warmup. SMA200 needs ~200 trading days ≈ 290 cal days;
        320 gives buffer for holiday blocks.
      - ``tickers`` (list[str]): explicit universe override.
        Default = Nifty 500 (same tag the bar keeper uses).
      - ``batch_size`` (int, default 50): tickers per upsert.

    Returns a structured stats dict suitable for
    ``scheduler_runs``::

        {
            "universe_size": int,
            "tickers_processed": int,
            "tickers_failed": int,
            "rows_written": int,
            "feature_set_version": str,
            "window": [start_iso, end_iso],
            "interval_sec": 86400,
            "failures": [(ticker, reason_str), ...],
            "status": "ok" | "skipped_empty_universe" | "error",
        }
    """
    payload = payload or {}
    feature_set_version = payload.get(
        "feature_set_version", FEATURE_SET_VERSION
    )
    batch_size = int(payload.get("batch_size", DEFAULT_BATCH_SIZE))
    warmup_days = int(payload.get("warmup_days", DEFAULT_WARMUP_DAYS))

    # Resolve write window.
    if "period_start" in payload and "period_end" in payload:
        write_start = date.fromisoformat(str(payload["period_start"]))
        write_end = date.fromisoformat(str(payload["period_end"]))
    else:
        write_start, write_end = _default_write_window()
    read_start = write_start - timedelta(days=warmup_days)
    read_end = write_end

    # Resolve universe — explicit override OR Nifty 500.
    if payload.get("tickers"):
        universe = list(payload["tickers"])
    else:
        async with disposable_pg_session() as s:
            universe = await _resolve_nifty500_universe(s)
    if not universe:
        stats = _empty_stats(
            write_start=write_start,
            write_end=write_end,
            universe_size=0,
            feature_set_version=feature_set_version,
        )
        stats["status"] = "skipped_empty_universe"
        return stats

    # Batch the universe.
    batches = [
        universe[i : i + batch_size]
        for i in range(0, len(universe), batch_size)
    ]
    stats = _empty_stats(
        write_start=write_start,
        write_end=write_end,
        universe_size=len(universe),
        feature_set_version=feature_set_version,
    )
    _logger.info(
        "[daily-features] start tickers=%d batches=%d "
        "interval_sec=%d write=%s..%s read=%s..%s version=%s",
        len(universe),
        len(batches),
        INTERVAL_SEC,
        write_start,
        write_end,
        read_start,
        read_end,
        feature_set_version,
    )

    for batch_idx, batch in enumerate(batches, start=1):
        written = _compute_and_write_batch(
            tickers=batch,
            read_start=read_start,
            read_end=read_end,
            write_window=(write_start, write_end),
            feature_set_version=feature_set_version,
            stats=stats,
        )
        stats["rows_written"] += written
        _logger.info(
            "[daily-features] batch %d/%d done "
            "(processed=%d failed=%d rows=%d)",
            batch_idx,
            len(batches),
            stats["tickers_processed"],
            stats["tickers_failed"],
            stats["rows_written"],
        )

    _logger.info(
        "[daily-features] complete universe=%d processed=%d "
        "failed=%d rows_written=%d",
        stats["universe_size"],
        stats["tickers_processed"],
        stats["tickers_failed"],
        stats["rows_written"],
    )
    stats["status"] = "ok"
    return stats
