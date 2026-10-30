"""Tests for ``compute_market_distance_from_sma200`` — the
continuous-band counterpart to ``compute_market_regime``'s
binary ``nifty_above_sma200`` flag (2026-08-10 regime feature).

Mirrors ``compute_market_regime``'s shape: same ``^NSEI`` load
via ``load_ohlcv_window``, same ``_rolling_sma`` primitive. We
patch ``load_ohlcv_window`` at its SOURCE module
(``backend.algo.backtest.data_source``) rather than the importer
(CLAUDE.md rule 16) since ``indicators.py`` imports it locally
inside the function body.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from backend.algo.backtest.indicators import (
    compute_market_distance_from_sma200,
)
from backend.algo.backtest.types import BarData

_TICKER = "^NSEI"


def _bar(*, d: date, close: Decimal) -> BarData:
    return BarData(
        ticker=_TICKER,
        date=d,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
    )


def _series(closes: list[Decimal], start: date) -> list[BarData]:
    return [
        _bar(d=start + timedelta(days=i), close=c)
        for i, c in enumerate(closes)
    ]


def test_last_close_above_sma_yields_positive_pct():
    """5-bar synthetic series, SMA window 5: the 5th (final) bar
    settles the first SMA point. Last close above the settled
    SMA must yield the exact ``(close - sma) / sma * 100`` with a
    positive sign.
    """
    start = date(2026, 1, 1)
    closes = [
        Decimal("100"), Decimal("100"), Decimal("100"),
        Decimal("100"), Decimal("110"),
    ]
    bars = _series(closes, start)

    with patch(
        "backend.algo.backtest.data_source.load_ohlcv_window",
        return_value={_TICKER: bars},
    ):
        result = compute_market_distance_from_sma200(
            period_start=start,
            period_end=start + timedelta(days=4),
            sma_window=5,
        )

    last_date = bars[-1].date
    sma_expected = sum(closes, Decimal("0")) / Decimal(5)
    expected_pct = (
        (bars[-1].close - sma_expected) / sma_expected * Decimal("100")
    )
    assert result[last_date] == expected_pct
    assert result[last_date] > 0


def test_last_close_below_sma_yields_negative_pct():
    """Same shape, last bar dips BELOW the settled SMA — sign
    must flip negative.
    """
    start = date(2026, 1, 1)
    closes = [
        Decimal("100"), Decimal("100"), Decimal("100"),
        Decimal("100"), Decimal("90"),
    ]
    bars = _series(closes, start)

    with patch(
        "backend.algo.backtest.data_source.load_ohlcv_window",
        return_value={_TICKER: bars},
    ):
        result = compute_market_distance_from_sma200(
            period_start=start,
            period_end=start + timedelta(days=4),
            sma_window=5,
        )

    last_date = bars[-1].date
    sma_expected = sum(closes, Decimal("0")) / Decimal(5)
    expected_pct = (
        (bars[-1].close - sma_expected) / sma_expected * Decimal("100")
    )
    assert result[last_date] == expected_pct
    assert result[last_date] < 0


def test_bars_before_sma_settles_are_absent():
    """Bars before the SMA window has accumulated enough history
    must be ABSENT from the output dict (not zero — callers fall
    back to Decimal(0) themselves), matching
    ``compute_market_regime``'s contract.
    """
    start = date(2026, 1, 1)
    closes = [Decimal("100")] * 5
    bars = _series(closes, start)

    with patch(
        "backend.algo.backtest.data_source.load_ohlcv_window",
        return_value={_TICKER: bars},
    ):
        result = compute_market_distance_from_sma200(
            period_start=start,
            period_end=start + timedelta(days=4),
            sma_window=5,
        )

    # Only the 5th (final) bar has a settled SMA(5).
    assert len(result) == 1
    assert bars[0].date not in result
    assert bars[-1].date in result


def test_series_shorter_than_sma_window_is_empty():
    """A series shorter than the SMA window never settles — the
    whole output dict is empty, mirroring ``compute_market_regime``.
    """
    start = date(2026, 1, 1)
    closes = [Decimal("100"), Decimal("101"), Decimal("102")]
    bars = _series(closes, start)

    with patch(
        "backend.algo.backtest.data_source.load_ohlcv_window",
        return_value={_TICKER: bars},
    ):
        result = compute_market_distance_from_sma200(
            period_start=start,
            period_end=start + timedelta(days=2),
            sma_window=5,
        )

    assert result == {}


def test_missing_regime_ticker_is_empty():
    """``^NSEI`` absent from the OHLCV load entirely (no rows in
    the window) → empty dict, never a KeyError.
    """
    start = date(2026, 1, 1)
    with patch(
        "backend.algo.backtest.data_source.load_ohlcv_window",
        return_value={},
    ):
        result = compute_market_distance_from_sma200(
            period_start=start,
            period_end=start + timedelta(days=4),
            sma_window=5,
        )

    assert result == {}
