"""Idempotent writer for ``algo.intraday_bars``.

Each flush does a scoped pre-delete of exactly the
(ticker, interval_sec, bar_open_ts_ns) triplets in the batch,
then appends, all under ``retry_iceberg_op``.  Re-flushing the
same batch (late tick, crash-replay, sweep re-trigger) therefore
yields no duplicate rows.

SAFETY: the delete predicate is built as an OR of per-triplet
And(EqualTo, EqualTo, EqualTo) — never a cross-product
In(tickers) x In(opens), which would silently delete rows NOT in
this batch when multiple tickers share the same bar_open_ts_ns
bucket.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import reduce
from typing import Any

import pyarrow as pa
from pyiceberg.expressions import And, EqualTo, Or

from backend.algo.stream.types import Bar

_logger = logging.getLogger(__name__)

_ALGO_INTRADAY_BARS_TABLE = "algo.intraday_bars"

# Explicit PyArrow schema with ``nullable=`` mirroring the Iceberg
# ``required`` flags in :func:`backend.algo.iceberg_init
# ._intraday_bars_schema`.  ``pa.Table.from_pylist`` infers fields
# as nullable by default, which trips PyIceberg's strict schema
# compatibility check (raises ``ValueError: Mismatch in fields``).
# Passing this schema explicitly keeps the writer + table contract
# aligned.
_INTRADAY_BARS_ARROW_SCHEMA = pa.schema([
    pa.field("ticker", pa.string(), nullable=False),
    pa.field("bar_date", pa.date32(), nullable=False),
    pa.field("interval_sec", pa.int64(), nullable=False),
    pa.field("bar_open_ts_ns", pa.int64(), nullable=False),
    pa.field("open", pa.float64(), nullable=False),
    pa.field("high", pa.float64(), nullable=False),
    pa.field("low", pa.float64(), nullable=False),
    pa.field("close", pa.float64(), nullable=False),
    pa.field("volume", pa.int64(), nullable=False),
    pa.field("written_at", pa.timestamp("us"), nullable=False),
])


def _row(bar: Bar) -> dict[str, Any]:
    # bar_date is a ``DateType`` Iceberg column (per CLAUDE.md
    # §4.3 #22 universal rule — never ``StringType("YYYY-MM-DD")``)
    # so the partition spec's ``MonthTransform`` can prune at scan
    # time.  Send a ``datetime.date`` object; PyArrow / PyIceberg
    # round-trip it to Iceberg's date32 representation.
    bar_date = datetime.fromtimestamp(
        bar.bar_open_ts_ns / 1_000_000_000, tz=timezone.utc,
    ).date()
    return {
        "ticker": bar.ticker,
        "bar_date": bar_date,
        "interval_sec": bar.interval_sec,
        "bar_open_ts_ns": bar.bar_open_ts_ns,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": int(bar.volume),
        "written_at": bar.written_at.replace(tzinfo=None),
    }


def _dedup_predicate(bars: list[Bar]):
    """Build an exact OR-of-Ands predicate for the distinct
    (ticker, interval_sec, bar_open_ts_ns) triplets in *bars*.

    Returns ``None`` when *bars* is empty (caller should skip the
    delete).  Each term is an exact three-column conjunction —
    never a cross-product In() that could match rows outside the
    batch.
    """
    keys = sorted(
        {(b.ticker, b.interval_sec, b.bar_open_ts_ns) for b in bars},
    )
    if not keys:
        return None
    terms = [
        And(
            EqualTo("ticker", t),
            And(
                EqualTo("interval_sec", i),
                EqualTo("bar_open_ts_ns", b),
            ),
        )
        for (t, i, b) in keys
    ]
    return reduce(Or, terms)


def flush_bars(bars: list[Bar]) -> None:
    """Idempotent Iceberg commit.  No-op on empty list.

    Scoped pre-delete of exactly the incoming triplets, then
    append, all wrapped in ``retry_iceberg_op`` for commit-conflict
    resilience.
    """
    if not bars:
        return

    from backend.algo._iceberg_retry import retry_iceberg_op
    from backend.db.duckdb_engine import invalidate_metadata

    arrow = pa.Table.from_pylist(
        [_row(b) for b in bars],
        schema=_INTRADAY_BARS_ARROW_SCHEMA,
    )
    predicate = _dedup_predicate(bars)

    def _do_upsert() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(_ALGO_INTRADAY_BARS_TABLE)
        try:
            tbl.delete(predicate)
        except Exception as exc:  # first run on empty table is fine
            _logger.debug(
                "intraday_bars pre-delete skipped: %s", exc,
            )
        tbl.append(arrow)

    retry_iceberg_op(_ALGO_INTRADAY_BARS_TABLE, _do_upsert)
    invalidate_metadata(_ALGO_INTRADAY_BARS_TABLE)
    _logger.info("flushed %d intraday_bars rows", len(bars))
