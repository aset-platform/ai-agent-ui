"""Pydantic models shared across the tick-stream pipeline.

Tick = one wire message (from Kite WS or replay fixture).
Bar  = one resampled OHLCV row, written append-only to
       algo.intraday_bars at the close of its interval.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IntervalSec = Literal[60, 300]  # 1m, 5m


class Tick(BaseModel):
    """One quote message from Kite (or a replay fixture)."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    ts_ns: int = Field(ge=0)
    ltp: float = Field(gt=0)
    volume: int = Field(ge=0)


class Bar(BaseModel):
    """One resampled OHLCV bar."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    interval_sec: int
    bar_open_ts_ns: int = Field(ge=0)
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(ge=0)
    written_at: datetime
