"""Tests for backend.tools._analysis_indicators.

Exercises ``_calculate_technical_indicators`` to ensure the
RSI(2) column is emitted with correct NaN-warmup behaviour and
range bounds.
"""

import numpy as np
import pandas as pd
from tools._analysis_indicators import _calculate_technical_indicators


def _synthetic_ohlcv(n: int = 50) -> pd.DataFrame:
    """Build a synthetic OHLCV frame with an oscillating close."""
    # Oscillating closes drive RSI off the floor/ceiling so the
    # [0, 100] bounds check is meaningful.
    rng = np.random.default_rng(seed=42)
    base = 100.0 + np.cumsum(rng.normal(0, 1, size=n))
    df = pd.DataFrame(
        {
            "Open": base,
            "High": base + 1.0,
            "Low": base - 1.0,
            "Close": base,
            "Volume": rng.integers(1_000, 10_000, size=n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )
    return df


class TestRSI2Column:
    """``_calculate_technical_indicators`` must emit RSI_2."""

    def test_rsi_2_column_present(self):
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        assert "RSI_2" in df.columns

    def test_rsi_2_warmup_is_nan(self):
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        # Wilder RSI with window=2 needs >= 2 prior closes.
        # First row is NaN; subsequent rows are finite.
        assert pd.isna(df["RSI_2"].iloc[0])

    def test_rsi_2_in_valid_range(self):
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        non_nan = df["RSI_2"].dropna()
        assert len(non_nan) > 0
        assert (non_nan >= 0).all() and (non_nan <= 100).all()

    def test_rsi_2_does_not_displace_rsi_14(self):
        """Regression: RSI_14 column must remain present."""
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        assert "RSI_14" in df.columns
        assert "RSI_2" in df.columns


class TestShortWindowSMAColumns:
    """``_calculate_technical_indicators`` must emit SMA_5/10/20."""

    def test_columns_present(self):
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        assert {"SMA_5", "SMA_10", "SMA_20"}.issubset(
            df.columns,
        )

    def test_warmup_is_nan(self):
        # window=N needs N prior closes; row 0 has only 1.
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        assert pd.isna(df["SMA_5"].iloc[0])
        assert pd.isna(df["SMA_10"].iloc[0])
        assert pd.isna(df["SMA_20"].iloc[0])

    def test_matches_manual_rolling_mean(self):
        """SMA_N must equal a plain rolling mean of Close."""
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        expected_5 = df["Close"].rolling(window=5).mean()
        expected_20 = df["Close"].rolling(window=20).mean()
        pd.testing.assert_series_equal(
            df["SMA_5"], expected_5, check_names=False,
        )
        pd.testing.assert_series_equal(
            df["SMA_20"], expected_20, check_names=False,
        )

    def test_does_not_displace_existing_sma_columns(self):
        """Regression: SMA_50/SMA_200 must remain present."""
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        assert "SMA_50" in df.columns
        assert "SMA_200" in df.columns
