"""Append-only writer for ``algo.intraday_bars``.

Single Iceberg commit on flush. Mirrors the pattern from
``backend/algo/backtest/event_writer.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa

from backend.algo.stream.types import Bar
from stocks.repository import StockRepository

_logger = logging.getLogger(__name__)


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


def flush_bars(bars: list[Bar]) -> None:
    """Single Iceberg commit. No-op on empty list."""
    if not bars:
        return
    repo = StockRepository()
    arrow = pa.Table.from_pylist(
        [_row(b) for b in bars],
        schema=_INTRADAY_BARS_ARROW_SCHEMA,
    )
    repo._retry_commit(  # noqa: SLF001
        "algo.intraday_bars", "append", arrow,
    )
    _logger.info("flushed %d intraday_bars rows", len(bars))
