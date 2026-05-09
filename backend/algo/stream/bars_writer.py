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


def _row(bar: Bar) -> dict[str, Any]:
    bar_date = datetime.fromtimestamp(
        bar.bar_open_ts_ns / 1_000_000_000, tz=timezone.utc,
    ).date().isoformat()
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
    arrow = pa.Table.from_pylist([_row(b) for b in bars])
    repo._retry_commit(  # noqa: SLF001
        "algo.intraday_bars", "append", arrow,
    )
    _logger.info("flushed %d intraday_bars rows", len(bars))
