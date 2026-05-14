"""Historical intraday index-bar backfill for
``stocks.index_intraday_bars`` (ASETPLTFRM-402 / FE-6).

Mirrors ``backend.algo.backtest.intraday_backfill`` exactly,
swapping only the destination Iceberg table and the
ticker-resolution path. Where the per-ticker backfill resolves
``our_ticker → instrument_token`` via
``InstrumentsRepo.get_tokens_for_tickers``, this module resolves
the canonical NSE index ``tradingsymbol`` (e.g. ``"NIFTY 50"``)
to ``instrument_token`` via a direct lookup on
``algo.instruments WHERE segment='INDICES' AND exchange='NSE'``
— index rows have ``our_ticker = NULL`` so the standard helper
does not see them.

Public API:

* ``upsert_index_intraday_bars`` — NaN-replaceable upsert keyed
  on ``(ticker, bar_date, interval_sec)`` against
  ``stocks.index_intraday_bars``. Same shape as
  ``upsert_intraday_bars`` for ``stocks.intraday_bars``.
* ``backfill_index_window`` — bulk pull of an index universe
  over ``[period_start, period_end]`` at one cadence. Resolves
  tradingsymbol → instrument_token per symbol, falls back to a
  recorded failure for symbols missing from
  ``algo.instruments`` (logged with ``exc_info=True``). Per-symbol
  Kite errors are caught + logged and the batch continues.

The module reuses the ``BackfillStats`` dataclass from
``intraday_backfill`` so consumers can aggregate index-keeper and
equity-keeper stats with the same shape.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timezone
from typing import Any

import pyarrow as pa
from pyiceberg.expressions import And, EqualTo, In
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.backtest.intraday_backfill import BackfillStats
from backend.algo.backtest.types import BarData
from backend.db.duckdb_engine import invalidate_metadata

_logger = logging.getLogger(__name__)

INDEX_INTRADAY_BARS_TABLE = "stocks.index_intraday_bars"


def _arrow_schema() -> pa.Schema:
    """Arrow schema for ``stocks.index_intraday_bars``.

    Every column is ``nullable=False`` to match the Iceberg schema's
    ``required=True``. Mirrors ``intraday_backfill._arrow_schema``
    exactly because the destination table is shape-identical to
    ``stocks.intraday_bars``.
    """
    return pa.schema(
        [
            pa.field("ticker", pa.string(), nullable=False),
            pa.field("bar_date", pa.string(), nullable=False),
            pa.field("interval_sec", pa.int64(), nullable=False),
            pa.field("bar_open_ts_ns", pa.int64(), nullable=False),
            pa.field("open", pa.float64(), nullable=False),
            pa.field("high", pa.float64(), nullable=False),
            pa.field("low", pa.float64(), nullable=False),
            pa.field("close", pa.float64(), nullable=False),
            pa.field("volume", pa.int64(), nullable=False),
            pa.field("written_at", pa.timestamp("us"), nullable=False),
            pa.field("source", pa.string(), nullable=False),
            pa.field("year_month", pa.string(), nullable=False),
        ]
    )


def _bars_to_arrow(
    bars: list[BarData],
    interval_sec: int,
    source: str,
) -> pa.Table:
    """Convert ``BarData`` list to the Arrow table for
    ``stocks.index_intraday_bars``.

    Rejects bars that fail any of the required-column invariants
    (NaN OHLC, missing ``bar_open_ts_ns``). ``volume`` is coerced
    to ``int`` and 0 is acceptable — pure indices have synthetic
    or zero volume.
    """
    if not bars:
        return pa.table({}, schema=_arrow_schema())

    # Iceberg ``TimestampType`` is tz-naive — strip tzinfo after
    # taking the UTC snapshot. See CLAUDE.md §5.1.
    written_at = datetime.now(timezone.utc).replace(
        microsecond=0, tzinfo=None
    )
    rows: list[dict[str, Any]] = []
    for b in bars:
        if b.bar_open_ts_ns is None:
            continue
        try:
            o, h, lo, c = (
                float(b.open),
                float(b.high),
                float(b.low),
                float(b.close),
            )
        except (TypeError, ValueError):
            continue
        if any(math.isnan(x) for x in (o, h, lo, c)):
            continue
        bar_date_str = b.date.strftime("%Y-%m-%d")
        rows.append(
            {
                "ticker": b.ticker,
                "bar_date": bar_date_str,
                "interval_sec": interval_sec,
                "bar_open_ts_ns": int(b.bar_open_ts_ns),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": int(b.volume or 0),
                "written_at": written_at,
                "source": source,
                "year_month": bar_date_str[:7],
            }
        )
    if not rows:
        return pa.table({}, schema=_arrow_schema())
    cols = {k: [r[k] for r in rows] for k in _arrow_schema().names}
    return pa.table(cols, schema=_arrow_schema())


def upsert_index_intraday_bars(
    bars: list[BarData],
    *,
    interval_sec: int,
    source: str = "kite",
) -> int:
    """NaN-replaceable upsert keyed on
    ``(ticker, bar_date, interval_sec)`` against
    ``stocks.index_intraday_bars``.

    Pre-deletes the cross-product of incoming tickers × bar_dates
    at the given ``interval_sec`` then appends — re-running the
    same window overwrites cleanly. The ``interval_sec`` term in
    the scoped delete prevents a 15m backfill from wiping 5m rows
    for the same dates.

    Returns the row count actually written (after NaN / missing-ts
    filtering).
    """
    if not bars:
        return 0
    from backend.algo._iceberg_retry import retry_iceberg_op

    arrow_tbl = _bars_to_arrow(bars, interval_sec, source)
    if arrow_tbl.num_rows == 0:
        return 0

    tickers = sorted({b.ticker for b in bars if b.bar_open_ts_ns})
    bar_dates = sorted(
        {b.date.strftime("%Y-%m-%d") for b in bars if b.bar_open_ts_ns}
    )

    def _do_upsert() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(INDEX_INTRADAY_BARS_TABLE)
        try:
            tbl.delete(
                And(
                    In("ticker", tickers),
                    In("bar_date", bar_dates),
                    EqualTo("interval_sec", interval_sec),
                ),
            )
        except Exception as exc:  # first run / empty partition
            _logger.debug(
                "index_intraday_bars pre-delete skipped (%s): %s",
                INDEX_INTRADAY_BARS_TABLE,
                exc,
            )
        tbl.append(arrow_tbl)

    retry_iceberg_op(INDEX_INTRADAY_BARS_TABLE, _do_upsert)
    invalidate_metadata(INDEX_INTRADAY_BARS_TABLE)
    return arrow_tbl.num_rows


async def resolve_index_instrument_tokens(
    session: AsyncSession,
    index_symbols: list[str],
) -> dict[str, int]:
    """Map NSE index ``tradingsymbol`` → ``instrument_token`` via
    ``algo.instruments``.

    Filters to ``segment='INDICES' AND exchange='NSE'`` so we never
    accidentally pick up a derivative or equity row that happens
    to share a name. Symbols missing from the table are silently
    omitted from the returned dict — the caller logs + records
    them as ``missing_token`` failures.
    """
    if not index_symbols:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT tradingsymbol, instrument_token "
                    "FROM algo.instruments "
                    "WHERE segment = 'INDICES' "
                    "  AND exchange = 'NSE' "
                    "  AND tradingsymbol = ANY(:syms)"
                ),
                {"syms": index_symbols},
            )
        )
        .mappings()
        .all()
    )
    return {r["tradingsymbol"]: int(r["instrument_token"]) for r in rows}


async def backfill_index_window(
    *,
    index_symbols: list[str],
    interval_sec: int,
    period_start: date,
    period_end: date,
    kite_client: Any,
    pg_session: AsyncSession,
    source: str = "kite_index_daily_keeper",
    batch_size: int = 10,
    on_batch_written: Any = None,
) -> BackfillStats:
    """Fetch + upsert intraday bars for the given NSE index symbols.

    Resolves each ``tradingsymbol`` → ``instrument_token`` via
    ``algo.instruments`` upfront, then iterates per-symbol calling
    ``kite_client.fetch_intraday_historical_window`` and bulk-upserts
    in batches of ``batch_size``. Per-symbol Kite failures and
    missing-token cases are logged with ``exc_info=True`` and
    recorded in ``BackfillStats.failures``; the batch continues so
    one bad index never strands the rest.

    Parameters
    ----------
    index_symbols : list[str]
        Kite tradingsymbols (e.g. ``"NIFTY 50"``,
        ``"NIFTY BANK"``).
    interval_sec : int
        60 (1m), 300 (5m), or 900 (15m).
    period_start, period_end : date
        Inclusive window bounds.
    kite_client : KiteClient
        Pre-authenticated client with a valid access_token.
    pg_session : AsyncSession
        Async PG session used to resolve tradingsymbol →
        instrument_token from ``algo.instruments``.
    source : str
        Stored verbatim in the ``source`` column of every written
        bar.
    batch_size : int
        Number of symbols per upsert commit. Default 10 matches
        the typical universe size — one commit per cadence.
    on_batch_written : Callable[[list[BarData], int], None] | None
        Optional hook invoked after each successful batch upsert
        with ``(batch_bars, interval_sec)``. Hook exceptions are
        caught + logged with ``exc_info=True``.

    Returns
    -------
    BackfillStats
        Aggregate counters + per-symbol failures. Reused from
        ``intraday_backfill`` for cross-keeper stat aggregation.
    """
    stats = BackfillStats()
    t0 = time.monotonic()

    tokens = await resolve_index_instrument_tokens(
        pg_session,
        index_symbols,
    )

    batches = [
        index_symbols[i : i + batch_size]
        for i in range(0, len(index_symbols), batch_size)
    ]
    total = len(index_symbols)
    _logger.info(
        "[index-backfill] start symbols=%d batches=%d "
        "interval_sec=%d start=%s end=%s source=%s",
        total,
        len(batches),
        interval_sec,
        period_start,
        period_end,
        source,
    )
    for bi, batch in enumerate(batches, start=1):
        batch_bars: list[BarData] = []
        for sym in batch:
            token = tokens.get(sym)
            if token is None:
                _logger.warning(
                    "[index-backfill] %s missing instrument "
                    "token in algo.instruments — skipping",
                    sym,
                    exc_info=True,
                )
                stats.tickers_failed += 1
                stats.failures.append((sym, "missing_token"))
                continue
            try:
                bars = kite_client.fetch_intraday_historical_window(
                    ticker=sym,
                    instrument_token=token,
                    interval_sec=interval_sec,
                    start=period_start,
                    end=period_end,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "[index-backfill] %s fetch failed: %s",
                    sym,
                    exc,
                    exc_info=True,
                )
                stats.tickers_failed += 1
                stats.failures.append(
                    (sym, f"fetch:{exc!s}"[:200]),
                )
                continue
            batch_bars.extend(bars)
            stats.tickers_done += 1
        if batch_bars:
            try:
                written = upsert_index_intraday_bars(
                    batch_bars,
                    interval_sec=interval_sec,
                    source=source,
                )
                stats.bars_written += written
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "[index-backfill] batch %d/%d upsert failed: %s",
                    bi,
                    len(batches),
                    exc,
                    exc_info=True,
                )
                for sym in batch:
                    if sym in tokens:
                        stats.tickers_done -= 1
                        stats.tickers_failed += 1
                        stats.failures.append(
                            (sym, f"upsert:{exc!s}"[:200]),
                        )
            else:
                if on_batch_written is not None:
                    try:
                        on_batch_written(batch_bars, interval_sec)
                    except Exception as hook_exc:  # noqa: BLE001
                        _logger.error(
                            "[index-backfill] batch %d/%d "
                            "on_batch_written hook failed: %s",
                            bi,
                            len(batches),
                            hook_exc,
                            exc_info=True,
                        )
        _logger.info(
            "[index-backfill] batch %d/%d done "
            "(running total: %d done, %d failed, %d bars)",
            bi,
            len(batches),
            stats.tickers_done,
            stats.tickers_failed,
            stats.bars_written,
        )
    stats.wall_clock_s = time.monotonic() - t0
    _logger.info(
        "[index-backfill] complete tickers_done=%d "
        "tickers_failed=%d bars_written=%d elapsed=%.1fs",
        stats.tickers_done,
        stats.tickers_failed,
        stats.bars_written,
        stats.wall_clock_s,
    )
    return stats
