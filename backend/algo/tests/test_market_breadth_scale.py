"""market_breadth_pct_above_sma200 must be fraction-scale (0.35 =
35%), matching the daily factor-library sibling pct_above_200sma —
both measure "% of universe above SMA200" and were previously on
conflicting scales (this one was ×100'd, the other wasn't), the
same class of bug as the distance_from_sma50 units mismatch fixed
in PR #300.

ASETPLTFRM-468.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.algo.features.engine import compute_intraday_features_for_universe


def _bars(ticker: str, closes: list[float]) -> list[BarData]:
    start = date(2026, 1, 1)
    out = []
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        ts = datetime(d.year, d.month, d.day, 3, 45, tzinfo=timezone.utc)
        out.append(BarData(
            ticker=ticker, date=d,
            open=Decimal(str(c)), high=Decimal(str(c)),
            low=Decimal(str(c)), close=Decimal(str(c)),
            volume=1000, bar_open_ts_ns=int(ts.timestamp() * 1e9),
        ))
    return out


class TestMarketBreadthIsFractionScale:
    def test_breadth_is_fraction_not_percentage(self):
        """3 tickers, 200 flat bars each to settle sma_200, then a
        final bar each: 2 close above their own sma_200, 1 below.
        Expected breadth = 2/3 ≈ 0.667, NOT 66.7."""
        closes_flat = [100.0] * 200
        bars_by_ticker = {
            "A.NS": _bars("A.NS", closes_flat + [110.0]),  # above
            "B.NS": _bars("B.NS", closes_flat + [110.0]),  # above
            "C.NS": _bars("C.NS", closes_flat + [90.0]),  # below
        }
        index_bars = {
            "NIFTY 50": _bars("NIFTY 50", closes_flat + [100.0]),
        }

        panel = compute_intraday_features_for_universe(
            bars_by_ticker,
            index_bars_by_symbol=index_bars,
        )

        last_ts = max(panel["A.NS"].keys())
        breadth = panel["A.NS"][last_ts]["market_breadth_pct_above_sma200"]

        assert Decimal("0.6") < breadth < Decimal("0.7"), (
            f"expected a fraction near 0.667 (2/3), got {breadth} -- "
            f"looks like it's still percentage-scale (66.x)"
        )
