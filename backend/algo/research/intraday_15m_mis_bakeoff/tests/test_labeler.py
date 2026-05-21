"""Pure-function tests for the vol-normalized 3-class labeler."""

import pandas as pd
import pytest

from backend.algo.research.intraday_15m_mis_bakeoff.labeler import (
    LABEL_FLAT,
    LABEL_LONG,
    LABEL_SHORT,
    label_bars,
)


def _bars(closes: list[float], opens: list[float] | None = None,
          atr_pct: float = 0.01) -> pd.DataFrame:
    """One ticker, sequential 15-min bars, controllable ATR."""
    opens = opens or closes
    n = len(closes)
    return pd.DataFrame({
        "ticker": ["T"] * n,
        "bar_open_ts_ns": list(range(n)),
        "bar_date": [pd.Timestamp("2026-01-01").date()] * n,
        "open":  opens,
        "close": closes,
        "atr_14": [c * atr_pct for c in closes],
    })


def test_long_when_forward_return_above_half_sigma():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    out = label_bars(bars, threshold=0.5)
    assert out.iloc[0]["label"] == LABEL_LONG


def test_short_when_forward_return_below_minus_half_sigma():
    bars = _bars(closes=[100, 100, 100, 100, 99.0, 99.0])
    out = label_bars(bars, threshold=0.5)
    assert out.iloc[0]["label"] == LABEL_SHORT


def test_flat_when_forward_return_in_band():
    bars = _bars(closes=[100, 100, 100, 100, 100.3, 100.3])
    out = label_bars(bars, threshold=0.5)
    assert out.iloc[0]["label"] == LABEL_FLAT


def test_boundary_at_exactly_plus_half_sigma_is_long():
    bars = _bars(closes=[100, 100, 100, 100, 100.5, 100.5])
    out = label_bars(bars, threshold=0.5)
    assert out.iloc[0]["label"] == LABEL_LONG


def test_nan_atr_skips_row():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    bars.loc[0, "atr_14"] = float("nan")
    out = label_bars(bars, threshold=0.5)
    assert (out["bar_open_ts_ns"] == 0).sum() == 0


def test_zero_atr_skips_row():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    bars.loc[0, "atr_14"] = 0.0
    out = label_bars(bars, threshold=0.5)
    assert (out["bar_open_ts_ns"] == 0).sum() == 0


def test_negative_price_raises():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    bars.loc[0, "close"] = -1.0
    with pytest.raises(ValueError, match="non-positive"):
        label_bars(bars, threshold=0.5)


def test_label_window_crossing_date_boundary_is_dropped():
    bars = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    bars.loc[3:, "bar_date"] = pd.Timestamp("2026-01-02").date()
    out = label_bars(bars, threshold=0.5)
    assert (out["bar_open_ts_ns"] == 0).sum() == 0


def test_no_label_when_fewer_than_5_forward_bars():
    bars = _bars(closes=[100, 100, 100, 100])
    out = label_bars(bars, threshold=0.5)
    assert len(out) == 0


def test_multi_ticker_independence():
    a = _bars(closes=[100, 100, 100, 100, 101.0, 101.0])
    b = _bars(closes=[200, 200, 200, 200, 198.0, 198.0])
    b["ticker"] = "B"
    out = label_bars(pd.concat([a, b], ignore_index=True), threshold=0.5)
    assert out[out["ticker"] == "T"].iloc[0]["label"] == LABEL_LONG
    assert out[out["ticker"] == "B"].iloc[0]["label"] == LABEL_SHORT
