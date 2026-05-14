"""Daily compute step for ``stocks.intraday_features``
(ASETPLTFRM-402 / FE-3).

Chain position (between ``intraday_bars_daily_ingest`` and
``intraday_bars_retention`` per spec §1 diagram)::

    [ingest bars] -> [compute features] -> [retention] -> [maintenance]

Reads the Nifty 500 universe's recent ``stocks.intraday_bars`` rows
(default window ``[yesterday, today]`` IST), runs the centralized
:func:`backend.algo.features.compute_intraday_features_for_universe`
engine, and bulk-writes the resulting long-format feature rows to
``stocks.intraday_features`` via the same NaN-replaceable upsert
pattern as :mod:`backend.algo.backtest.intraday_backfill` (scoped
pre-delete on ``(ticker, year_month)`` for the touched batch, then
:meth:`Table.append`).

Re-runs of the same window are safe: the pre-delete is scoped to the
incoming ``(ticker, year_month)`` tuples so re-emitted feature rows
overwrite cleanly without growing the table.

The :func:`backfill_features_window` helper exposes the same code
path for ad-hoc / on-demand backfills used by FE-4's loader when a
partition chunk is absent on a cache miss.

Wired via ``@register_job("intraday_features_daily_compute")`` in
``backend/jobs/executor.py``.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pyarrow as pa
from pyiceberg.expressions import And, In

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.algo.backtest.types import BarData
from backend.algo.features import (
    FEATURE_SET_VERSION,
    compute_intraday_features_for_universe,
)
from backend.cache import get_cache
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)
from backend.db.engine import disposable_pg_session

_logger = logging.getLogger(__name__)

INTRADAY_BARS_TABLE = "stocks.intraday_bars"
INTRADAY_FEATURES_TABLE = "stocks.intraday_features"

_ALLOWED_INTERVALS = (900, 300, 60)
_DEFAULT_INTERVAL_SEC = 900
_DEFAULT_BATCH_SIZE = 50


def _ist_today() -> date:
    """IST-local date (UTC+5:30)."""
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _default_window() -> tuple[date, date]:
    """Default compute window = ``[yesterday, today]`` IST."""
    today = _ist_today()
    return today - timedelta(days=1), today


def _features_arrow_schema() -> pa.Schema:
    """Arrow schema matching ``stocks.intraday_features`` (FE-1).

    Every column is ``nullable=False`` to match the Iceberg schema's
    ``required=True`` — PyArrow defaults to nullable=True which
    PyIceberg rejects on append against a required-column schema.
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
    intraday bars daily keeper.
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


def _load_intraday_bars_for_ticker(
    *,
    ticker: str,
    interval_sec: int,
    start: date,
    end: date,
) -> list[BarData]:
    """Read ``stocks.intraday_bars`` rows for ``ticker`` over the
    ``[start, end]`` window at ``interval_sec`` cadence.

    Returns an empty list on any read failure (logged with
    ``exc_info=True``) so a single missing-ticker scan never strands
    the batch.
    """
    try:
        rows = query_iceberg_table(
            INTRADAY_BARS_TABLE,
            "SELECT ticker, bar_date, bar_open_ts_ns, "
            "       open, high, low, close, volume "
            "FROM intraday_bars "
            "WHERE ticker = ? AND interval_sec = ? "
            "  AND bar_date BETWEEN ? AND ? "
            "ORDER BY bar_open_ts_ns",
            [
                ticker,
                interval_sec,
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d"),
            ],
        )
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[intraday-features] bar load failed for %s "
            "(interval_sec=%d, %s..%s): %s",
            ticker,
            interval_sec,
            start,
            end,
            exc,
            exc_info=True,
        )
        return []
    out: list[BarData] = []
    for r in rows or []:
        raw_date = r.get("bar_date")
        if isinstance(raw_date, date):
            bar_d = raw_date
        else:
            try:
                bar_d = date.fromisoformat(str(raw_date)[:10])
            except (TypeError, ValueError):
                continue
        try:
            out.append(
                BarData(
                    ticker=r["ticker"],
                    date=bar_d,
                    open=Decimal(str(r["open"])),
                    high=Decimal(str(r["high"])),
                    low=Decimal(str(r["low"])),
                    close=Decimal(str(r["close"])),
                    volume=int(r["volume"] or 0),
                    bar_open_ts_ns=int(r["bar_open_ts_ns"]),
                )
            )
        except (TypeError, ValueError, KeyError) as exc:
            _logger.warning(
                "[intraday-features] dropping malformed bar row "
                "for %s @ %s: %s",
                ticker,
                raw_date,
                exc,
            )
            continue
    return out


def _panel_to_arrow_rows(
    *,
    panel: dict[str, dict[int, dict[str, Any]]],
    bars_by_ticker: dict[str, list[BarData]],
    interval_sec: int,
    feature_set_version: str,
    written_at: datetime,
) -> list[dict[str, Any]]:
    """Flatten the per-ticker feature panel into long-format Arrow
    rows. Skips NaN / non-finite numeric values (they represent
    "feature not computable" and the reader interprets NaN as
    missing; we never want to write them).
    """
    rows: list[dict[str, Any]] = []
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
            bar_date_str = bar.date.strftime("%Y-%m-%d")
            year_month = bar_date_str[:7]
            for feat_name, feat_val in feats.items():
                try:
                    fv = float(feat_val)
                except (TypeError, ValueError):
                    # Non-numeric feature (e.g. ``time_of_day_bucket``
                    # is a str). Skipping is the safe choice — the
                    # FE-1 Iceberg schema constrains
                    # ``feature_value`` to ``DoubleType``.
                    continue
                if math.isnan(fv) or math.isinf(fv):
                    continue
                rows.append(
                    {
                        "ticker": ticker,
                        "bar_open_ts_ns": int(ts_ns),
                        "bar_date": bar_date_str,
                        "year_month": year_month,
                        "interval_sec": int(interval_sec),
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
    """NaN-replaceable upsert for one batch of feature rows.

    Pre-deletes the cross-product of incoming ``(ticker,
    year_month)`` pairs at the given ``interval_sec`` set, then
    appends. Scoped to the incoming batch — never wipes other
    tickers / months. Returns the number of rows actually written.
    """
    if not arrow_rows:
        return 0
    schema = _features_arrow_schema()
    cols = {k: [r[k] for r in arrow_rows] for k in schema.names}
    arrow_tbl = pa.table(cols, schema=schema)

    tickers = sorted({r["ticker"] for r in arrow_rows})
    year_months = sorted({r["year_month"] for r in arrow_rows})
    interval_secs = sorted({r["interval_sec"] for r in arrow_rows})

    def _do_upsert() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(INTRADAY_FEATURES_TABLE)
        try:
            tbl.delete(
                And(
                    In("ticker", tickers),
                    In("year_month", year_months),
                    In("interval_sec", interval_secs),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # First-run / empty-partition pre-delete failure is
            # benign — there is nothing to delete yet.
            _logger.debug(
                "intraday_features pre-delete skipped (%s): %s",
                INTRADAY_FEATURES_TABLE,
                exc,
            )
        tbl.append(arrow_tbl)

    retry_iceberg_op(INTRADAY_FEATURES_TABLE, _do_upsert)
    invalidate_metadata(INTRADAY_FEATURES_TABLE)
    # FE-4 — invalidate the partition-chunk Redis cache so the
    # next loader read after a successful write picks up the
    # fresh rows immediately (per CLAUDE.md §5.13 write-through
    # invalidation pattern).
    _invalidate_feature_chunk_cache(year_months=year_months)
    return arrow_tbl.num_rows


def _invalidate_feature_chunk_cache(*, year_months: list[str]) -> None:
    """Best-effort wildcard invalidation of feature-chunk cache
    keys for the months that were just written. Mirrors the
    ``cache:feature:chunk:{ticker}:{year_month}:{interval_sec}``
    schema used by the FE-4 loader. Per CLAUDE.md ``redis-cache-
    layer``: ``cache.invalidate`` is glob-pattern; failure is
    logged and swallowed (cache outage must never block a write).
    """
    try:
        cache = get_cache()
        for ym in year_months:
            cache.invalidate(f"cache:feature:chunk:*:{ym}:*")
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[intraday-features] cache invalidate skipped "
            "year_months=%s: %s",
            year_months,
            exc,
            exc_info=True,
        )


def _compute_and_write_batch(
    *,
    tickers: list[str],
    interval_sec: int,
    start: date,
    end: date,
    feature_set_version: str,
    stats: dict[str, Any],
) -> int:
    """Per-batch worker — load bars, run engine, write features.

    Per-ticker fetch / compute failures append to ``stats.failures``
    with ``(ticker, reason[:200])`` but do NOT abort the batch.
    Returns the row count actually written.
    """
    bars_by_ticker: dict[str, list[BarData]] = {}
    for tk in tickers:
        try:
            bars = _load_intraday_bars_for_ticker(
                ticker=tk,
                interval_sec=interval_sec,
                start=start,
                end=end,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[intraday-features] %s bar fetch crashed: %s",
                tk,
                exc,
                exc_info=True,
            )
            stats["tickers_failed"] += 1
            stats["failures"].append((tk, f"fetch:{exc!s}"[:200]))
            continue
        if not bars:
            # No bars in the window — not a failure (ticker may be
            # newly tagged or off-market); skip silently.
            continue
        bars_by_ticker[tk] = bars
    if not bars_by_ticker:
        return 0
    try:
        panel = compute_intraday_features_for_universe(
            bars_by_ticker,
            feature_set_version=feature_set_version,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[intraday-features] compute batch failed "
            "(tickers=%d, interval_sec=%d): %s",
            len(bars_by_ticker),
            interval_sec,
            exc,
            exc_info=True,
        )
        for tk in bars_by_ticker:
            stats["tickers_failed"] += 1
            stats["failures"].append(
                (tk, f"compute:{exc!s}"[:200]),
            )
        return 0

    # Iceberg ``TimestampType`` is tz-naive — strip tzinfo after the
    # UTC snapshot per ``iceberg-tz-naive-timestamps``.
    written_at = datetime.now(timezone.utc).replace(
        microsecond=0,
        tzinfo=None,
    )
    arrow_rows = _panel_to_arrow_rows(
        panel=panel,
        bars_by_ticker=bars_by_ticker,
        interval_sec=interval_sec,
        feature_set_version=feature_set_version,
        written_at=written_at,
    )
    if not arrow_rows:
        # Engine emitted nothing computable for this batch (e.g.
        # all tickers under warmup) — record per-ticker success but
        # zero rows written.
        for tk in bars_by_ticker:
            stats["tickers_processed"] += 1
        return 0
    try:
        written = _write_features_batch(arrow_rows=arrow_rows)
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[intraday-features] batch upsert failed "
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
    interval_sec: int,
    start: date,
    end: date,
    universe_size: int = 0,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> dict[str, Any]:
    """Skeleton stats dict consumed by ``scheduler_runs``."""
    return {
        "universe_size": universe_size,
        "tickers_processed": 0,
        "tickers_failed": 0,
        "rows_written": 0,
        "feature_set_version": feature_set_version,
        "window": [start.isoformat(), end.isoformat()],
        "interval_sec": interval_sec,
        "failures": [],
    }


async def run_intraday_features_daily_compute_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute Phase-1 features for the Nifty 500 universe over a
    short rolling window (default ``[yesterday, today]`` IST) from
    ``stocks.intraday_bars`` and write to
    ``stocks.intraday_features``.

    Payload keys (all optional):
      - ``force`` (bool, default False): accepted for symmetry with
        ``intraday_bars_retention``. No gate by default — this step
        is cheap to re-run thanks to the NaN-replaceable upsert.
      - ``period_start`` / ``period_end`` (ISO ``YYYY-MM-DD``):
        explicit window. Default ``[yesterday, today]`` IST.
      - ``tickers`` (list[str]): explicit universe override. Default
        = Nifty 500 (same tag the bar keeper uses).
      - ``interval_sec`` (int, default 900): bar cadence. Must be
        one of ``(900, 300, 60)`` — others are rejected with a
        structured error in the payload.
      - ``batch_size`` (int, default 50): tickers per upsert commit.

    Returns a structured stats dict suitable for ``scheduler_runs``::

        {
            "universe_size": int,
            "tickers_processed": int,
            "tickers_failed": int,
            "rows_written": int,
            "feature_set_version": str,
            "window": [iso_start, iso_end],
            "interval_sec": int,
            "failures": list[tuple[str, str]],
        }
    """
    payload = payload or {}
    interval_sec = int(payload.get("interval_sec") or _DEFAULT_INTERVAL_SEC)
    start, end = _default_window()
    if payload.get("period_start"):
        start = date.fromisoformat(payload["period_start"])
    if payload.get("period_end"):
        end = date.fromisoformat(payload["period_end"])
    batch_size = int(payload.get("batch_size") or _DEFAULT_BATCH_SIZE)
    feature_set_version = str(
        payload.get("feature_set_version") or FEATURE_SET_VERSION,
    )

    if interval_sec not in _ALLOWED_INTERVALS:
        stats = _empty_stats(
            interval_sec=interval_sec,
            start=start,
            end=end,
            feature_set_version=feature_set_version,
        )
        stats["status"] = "error"
        stats["error"] = (
            f"interval_sec={interval_sec} not in "
            f"{list(_ALLOWED_INTERVALS)}"
        )
        _logger.error("[intraday-features] %s", stats["error"])
        return stats

    explicit_tickers = payload.get("tickers")
    if explicit_tickers:
        universe = sorted(
            {str(t).strip() for t in explicit_tickers if str(t).strip()},
        )
    else:
        # Scheduler jobs spawn under their own ``asyncio.run`` event
        # loop. ``disposable_pg_session`` gives us a per-call NullPool
        # engine, scoped to this loop (cached factory would raise
        # "Future attached to a different loop").
        async with disposable_pg_session() as session:
            universe = await _resolve_nifty500_universe(session)

    if not universe:
        stats = _empty_stats(
            interval_sec=interval_sec,
            start=start,
            end=end,
            feature_set_version=feature_set_version,
        )
        stats["status"] = "skipped_empty_universe"
        _logger.warning(
            "[intraday-features] empty universe — skipping",
        )
        return stats

    stats: dict[str, Any] = _empty_stats(
        interval_sec=interval_sec,
        start=start,
        end=end,
        universe_size=len(universe),
        feature_set_version=feature_set_version,
    )

    batches = [
        universe[i : i + batch_size]
        for i in range(0, len(universe), batch_size)
    ]
    _logger.info(
        "[intraday-features] start tickers=%d batches=%d "
        "interval_sec=%d start=%s end=%s version=%s",
        len(universe),
        len(batches),
        interval_sec,
        start.isoformat(),
        end.isoformat(),
        feature_set_version,
    )
    for bi, batch in enumerate(batches, start=1):
        try:
            written = _compute_and_write_batch(
                tickers=batch,
                interval_sec=interval_sec,
                start=start,
                end=end,
                feature_set_version=feature_set_version,
                stats=stats,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[intraday-features] batch %d/%d crashed: %s",
                bi,
                len(batches),
                exc,
                exc_info=True,
            )
            for tk in batch:
                stats["tickers_failed"] += 1
                stats["failures"].append(
                    (tk, f"batch:{exc!s}"[:200]),
                )
            continue
        stats["rows_written"] += written
        _logger.info(
            "[intraday-features] batch %d/%d done "
            "(processed=%d failed=%d rows=%d)",
            bi,
            len(batches),
            stats["tickers_processed"],
            stats["tickers_failed"],
            stats["rows_written"],
        )

    stats["status"] = "ok"
    # Cap the failures list so scheduler_runs doesn't store an
    # unbounded blob; keep the head so the most-frequent failure
    # category surfaces.
    if len(stats["failures"]) > 50:
        stats["failures"] = stats["failures"][:50]
    _logger.info(
        "[intraday-features] complete universe=%d processed=%d "
        "failed=%d rows_written=%d",
        stats["universe_size"],
        stats["tickers_processed"],
        stats["tickers_failed"],
        stats["rows_written"],
    )
    return stats
