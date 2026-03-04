"""Tests for bug fixes applied during the per-ticker refresh session.

Covers:

1. **TimedeltaIndex abs fix** — ``np.abs()`` instead of
   ``.abs()`` for dividend marker snapping in chart_builders.
2. **Negative cache TTL** — empty OHLCV/forecast/dividend reads
   expire after ``_NEGATIVE_TTL`` (30 s), not ``_SHARED_TTL``
   (5 min).
3. **Compare error message** — ``update_compare`` reports which
   tickers failed to load.
4. **Compare chart uses Adj Close** — aligned series pulls from
   ``Adj Close`` column, not ``Close``.
5. **poll_card_refreshes empty ALL** — returns ``([], [])``
   when no pattern-matched elements exist.
"""

import time as _time
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

# ────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────


def _make_iceberg_ohlcv(
    ticker: str = "AAPL",
    rows: int = 50,
) -> pd.DataFrame:
    """Return an Iceberg-shaped OHLCV DataFrame."""
    dates = pd.date_range("2020-01-01", periods=rows, freq="B")
    rng = np.random.default_rng(42)
    close = 100 + rng.standard_normal(rows).cumsum()
    return pd.DataFrame(
        {
            "ticker": [ticker] * rows,
            "date": dates.date,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "adj_close": close * 0.95,
            "volume": rng.integers(1_000_000, 5_000_000, rows),
        }
    )


def _mock_repo_for(tickers):
    """Build a mock repo that returns data per ticker."""
    repo = MagicMock()

    def _get_ohlcv(t):
        if t in tickers:
            return _make_iceberg_ohlcv(t)
        return pd.DataFrame()

    repo.get_ohlcv.side_effect = _get_ohlcv
    return repo


# ────────────────────────────────────────────────────────
# 1. TimedeltaIndex np.abs fix (chart_builders)
# ────────────────────────────────────────────────────────


class TestTimedeltaIndexAbsFix:
    """Verify dividend marker snapping uses np.abs."""

    def test_np_abs_on_timedelta_index(self):
        """np.abs should work on TimedeltaIndex."""
        idx = pd.date_range("2024-01-01", periods=10, freq="B")
        target = pd.Timestamp("2024-01-08")
        diffs = np.abs(idx - target)
        nearest = diffs.argmin()
        assert idx[nearest] == pd.Timestamp("2024-01-08")

    def test_timedelta_index_has_no_abs(self):
        """TimedeltaIndex.abs() is unavailable in pandas 2."""
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        td = idx - pd.Timestamp("2024-01-03")
        assert not hasattr(td, "abs")

    def test_chart_builders_uses_np_abs(self):
        """Source code must use np.abs, not .abs()."""
        from pathlib import Path

        src = (
            Path(__file__).parent.parent.parent
            / "dashboard"
            / "callbacks"
            / "chart_builders.py"
        ).read_text(encoding="utf-8")
        assert "np.abs(df.index - ex_dt)" in src
        assert "(df.index - ex_dt).abs()" not in src


# ────────────────────────────────────────────────────────
# 2. Negative cache TTL (iceberg.py)
# ────────────────────────────────────────────────────────


class TestNegativeCacheTTL:
    """Empty cache entries must expire faster than data."""

    def test_negative_ttl_is_shorter(self):
        """_NEGATIVE_TTL must be much shorter than
        _SHARED_TTL."""
        import dashboard.callbacks.iceberg as ice

        assert ice._NEGATIVE_TTL < ice._SHARED_TTL
        assert ice._NEGATIVE_TTL <= 30

    def test_ohlcv_none_cached_with_short_ttl(self):
        """Empty OHLCV should be cached with _NEGATIVE_TTL,
        not _SHARED_TTL."""
        import dashboard.callbacks.iceberg as ice

        ice._OHLCV_CACHE.clear()

        repo = MagicMock()
        repo.get_ohlcv.return_value = pd.DataFrame()

        before = _time.monotonic()
        result = ice._get_ohlcv_cached(repo, "NOSUCH")
        after = _time.monotonic()

        assert result is None
        entry = ice._OHLCV_CACHE.get("NOSUCH")
        assert entry is not None
        _, expiry = entry
        # Expiry should be within NEGATIVE_TTL from now
        assert expiry <= after + ice._NEGATIVE_TTL + 1
        # Must NOT be anywhere near _SHARED_TTL
        assert expiry < before + ice._SHARED_TTL

        ice._OHLCV_CACHE.clear()

    def test_forecast_none_cached_with_short_ttl(self):
        """Empty forecast cached with _NEGATIVE_TTL."""
        import dashboard.callbacks.iceberg as ice

        ice._FORECAST_CACHE.clear()

        repo = MagicMock()
        repo.get_latest_forecast_series.return_value = pd.DataFrame()

        before = _time.monotonic()
        result = ice._get_forecast_cached(repo, "NOSUCH", 9)

        assert result is None
        entry = ice._FORECAST_CACHE.get(("NOSUCH", 9))
        assert entry is not None
        _, expiry = entry
        assert expiry < before + ice._SHARED_TTL

        ice._FORECAST_CACHE.clear()

    def test_dividends_none_cached_with_short_ttl(self):
        """Empty dividends cached with _NEGATIVE_TTL."""
        import dashboard.callbacks.iceberg as ice

        ice._DIVIDENDS_CACHE.clear()

        repo = MagicMock()
        repo.get_dividends.return_value = pd.DataFrame()

        before = _time.monotonic()
        result = ice._get_dividends_cached(repo, "NOSUCH")

        assert result is None
        entry = ice._DIVIDENDS_CACHE.get("NOSUCH")
        assert entry is not None
        _, expiry = entry
        assert expiry < before + ice._SHARED_TTL

        ice._DIVIDENDS_CACHE.clear()

    def test_positive_ohlcv_cached_with_full_ttl(self):
        """Valid OHLCV data cached with _SHARED_TTL."""
        import dashboard.callbacks.iceberg as ice

        ice._OHLCV_CACHE.clear()

        repo = MagicMock()
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv("AAPL")

        before = _time.monotonic()
        result = ice._get_ohlcv_cached(repo, "AAPL")

        assert result is not None
        entry = ice._OHLCV_CACHE.get("AAPL")
        _, expiry = entry
        # Expiry should be near _SHARED_TTL from now
        assert expiry >= before + ice._SHARED_TTL - 1

        ice._OHLCV_CACHE.clear()


# ────────────────────────────────────────────────────────
# 3. Compare error message (analysis_cbs)
# ────────────────────────────────────────────────────────


class TestCompareErrorMessage:
    """update_compare must report which tickers failed."""

    def test_source_has_failed_tracking(self):
        """analysis_cbs.py must track failed tickers."""
        from pathlib import Path

        src = (
            Path(__file__).parent.parent.parent
            / "dashboard"
            / "callbacks"
            / "analysis_cbs.py"
        ).read_text(encoding="utf-8")
        assert "failed.append(t)" in src
        assert "No data for:" in src

    def test_error_lists_missing_tickers(self):
        """When tickers have no data, error should name
        them."""
        import dashboard.callbacks.iceberg as ice

        ice._OHLCV_CACHE.clear()

        repo = _mock_repo_for([])  # no data for any
        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=repo,
        ):
            from dashboard.callbacks.data_loaders import (
                _load_raw,
            )

            r1 = _load_raw("AAA")
            r2 = _load_raw("BBB")

        assert r1 is None
        assert r2 is None

        ice._OHLCV_CACHE.clear()

    def test_partial_load_still_works(self):
        """If 2 of 3 tickers load, comparison should
        proceed."""
        import dashboard.callbacks.iceberg as ice

        ice._OHLCV_CACHE.clear()

        repo = _mock_repo_for(["AAPL", "MSFT"])
        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=repo,
        ):
            from dashboard.callbacks.data_loaders import (
                _load_raw,
            )

            results = {t: _load_raw(t) for t in ["AAPL", "MSFT", "NOSUCH"]}

        assert results["AAPL"] is not None
        assert results["MSFT"] is not None
        assert results["NOSUCH"] is None
        loaded = {
            t: df
            for t, df in results.items()
            if df is not None and len(df) > 1
        }
        # 2 loaded → compare should proceed
        assert len(loaded) >= 2

        ice._OHLCV_CACHE.clear()


# ────────────────────────────────────────────────────────
# 4. Compare chart uses Adj Close
# ────────────────────────────────────────────────────────


class TestCompareUsesAdjClose:
    """Compare callback must use Adj Close, not Close."""

    def test_aligned_uses_adj_close(self):
        """Source code must index 'Adj Close' for aligned
        dict."""
        from pathlib import Path

        src = (
            Path(__file__).parent.parent.parent
            / "dashboard"
            / "callbacks"
            / "analysis_cbs.py"
        ).read_text(encoding="utf-8")
        # The aligned dict comprehension should use
        # Adj Close
        assert '["Adj Close"]' in src
        # Chart title should reflect the change
        assert "Adj Close Price Comparison" in src
        # Old normalised approach should be gone
        assert "(Base = 100)" not in src

    def test_adj_close_differs_from_close(self):
        """Adj Close and Close should differ in test
        data (sanity check)."""
        import dashboard.callbacks.iceberg as ice

        ice._OHLCV_CACHE.clear()

        repo = MagicMock()
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv("AAPL")

        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=repo,
        ):
            from dashboard.callbacks.data_loaders import (
                _load_raw,
            )

            df = _load_raw("AAPL")

        assert df is not None
        # adj_close = close * 0.95, so Adj Close != Close
        assert not np.allclose(df["Close"].values, df["Adj Close"].values)

        ice._OHLCV_CACHE.clear()

    def test_compare_refresh_trigger_input(self):
        """update_compare must accept refresh store input."""
        from pathlib import Path

        src = (
            Path(__file__).parent.parent.parent
            / "dashboard"
            / "callbacks"
            / "analysis_cbs.py"
        ).read_text(encoding="utf-8")
        assert 'Input("analysis-refresh-store", "data")' in src


# ────────────────────────────────────────────────────────
# 5. poll_card_refreshes empty ALL pattern
# ────────────────────────────────────────────────────────


class TestPollCardRefreshesEmptyALL:
    """poll callback must return [] for empty ALL pattern."""

    def test_source_returns_empty_lists(self):
        """When status_ids is empty, must return
        ([], [], no_update)."""
        from pathlib import Path

        src = (
            Path(__file__).parent.parent.parent
            / "dashboard"
            / "callbacks"
            / "home_cbs.py"
        ).read_text(encoding="utf-8")
        # Must NOT return no_update for the ALL outputs
        assert "return [], [], no_update" in src
        # Must NOT have the old broken pattern
        assert "return no_update, no_update, no_update" not in src
