"""Pinned-failure tests confirming the data-source layer cannot
return rows past the requested ``period_end`` even if the caller
sets a clamping date in the future. These guards block the
single most common backtest bug — peeking at tomorrow's close.
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.backtest.data_source import (
    BackedFutureBarError,
    load_ohlcv_window,
)


def test_load_rejects_period_end_in_future():
    with pytest.raises(BackedFutureBarError):
        load_ohlcv_window(
            tickers=["TCS.NS"],
            period_start=date(2024, 1, 1),
            period_end=date(9999, 1, 1),
        )


def test_load_rejects_period_start_after_period_end():
    with pytest.raises(ValueError, match="period_start"):
        load_ohlcv_window(
            tickers=["TCS.NS"],
            period_start=date(2024, 6, 1),
            period_end=date(2024, 1, 1),
        )


def test_load_empty_tickers_returns_empty_dict():
    out = load_ohlcv_window(
        tickers=[],
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 31),
    )
    assert out == {}
