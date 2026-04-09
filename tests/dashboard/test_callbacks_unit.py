"""Unit tests for pure-Python helper functions in dashboard/callbacks.py.

No Dash app is constructed — only the helpers that are safe to call without
a running Dash server or Iceberg catalog are tested here.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import helpers under test (importing the full module would trigger Dash
# app construction; we import just what we need)
# ---------------------------------------------------------------------------


def _import_callbacks():
    """Return the callbacks module, patching heavy imports to avoid side-effects."""
    import importlib
    import sys

    # Patch pyiceberg and dash so the module-level code doesn't crash in CI
    for mod_name in list(sys.modules.keys()):
        if "pyiceberg" in mod_name or "dashboard.app" in mod_name:
            sys.modules.pop(mod_name, None)

    with (
        patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}),
        patch(
            "pyiceberg.catalog.load_catalog",
            side_effect=RuntimeError("no catalog in tests"),
        ),
    ):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "dashboard.callbacks",
            str(
                Path(__file__).parent.parent.parent
                / "dashboard"
                / "callbacks.py"
            ),
        )
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            # Module may raise on Iceberg init — that's OK, we only need the functions
            pass
        return mod


# ---------------------------------------------------------------------------
# _get_market
# ---------------------------------------------------------------------------


class TestGetMarket:
    """Tests for :func:`dashboard.callbacks.utils._get_market`."""

    @pytest.fixture(autouse=True)
    def _mod(self):
        import importlib.util
        from pathlib import Path

        source = (
            Path(__file__).parent.parent.parent
            / "dashboard"
            / "callbacks"
            / "utils.py"
        ).read_text(encoding="utf-8")

        # Extract and exec just the _get_market function
        import ast

        tree = ast.parse(source)
        fn_node = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_get_market"
            ),
            None,
        )
        if fn_node is None:
            pytest.skip("_get_market not found in callbacks/utils.py")

        exec_globals = {}
        exec(
            compile(
                ast.Module(body=[fn_node], type_ignores=[]), "<string>", "exec"
            ),
            exec_globals,
        )
        self._get_market = exec_globals["_get_market"]

    def test_nse_ticker_is_india(self):
        assert self._get_market("RELIANCE.NS") == "india"

    def test_bse_ticker_is_india(self):
        assert self._get_market("TCS.BO") == "india"

    def test_us_ticker_is_us(self):
        assert self._get_market("AAPL") == "us"

    def test_msft_is_us(self):
        assert self._get_market("MSFT") == "us"

    def test_case_insensitive_ns(self):
        assert self._get_market("reliance.ns") == "india"

    def test_case_insensitive_bo(self):
        assert self._get_market("tcs.bo") == "india"


# ---------------------------------------------------------------------------
# _validate_token  (JWT logic)
# ---------------------------------------------------------------------------


class TestValidateToken:
    """Tests for :func:`dashboard.callbacks.auth_utils._validate_token`."""

    @pytest.fixture()
    def _fn(self):
        """Return the _validate_token function with JWT_SECRET_KEY in env."""
        source = (
            __import__("pathlib").Path(__file__).parent.parent.parent
            / "dashboard"
            / "callbacks"
            / "auth_utils.py"
        ).read_text(encoding="utf-8")

        import ast
        import types

        tree = ast.parse(source)
        fn_node = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "_validate_token"
            ),
            None,
        )
        if fn_node is None:
            pytest.skip("_validate_token not found in callbacks.py")

        # We need the imports (os, logging, Optional, Dict) available
        module_code = compile(
            ast.Module(body=[fn_node], type_ignores=[]), "<string>", "exec"
        )
        g = {
            "os": os,
            "logging": __import__("logging"),
            "Optional": __import__("typing").Optional,
            "Dict": __import__("typing").Dict,
            "Any": __import__("typing").Any,
            "logger": __import__("logging").getLogger("test"),
        }
        exec(module_code, g)
        return g["_validate_token"]

    def _make_token(self, payload: dict, secret: str = "test-secret") -> str:
        from jose import jwt

        return jwt.encode(payload, secret, algorithm="HS256")

    def test_none_returns_none(self, _fn):
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _fn(None) is None

    def test_empty_string_returns_none(self, _fn):
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _fn("") is None

    def test_valid_access_token(self, _fn):
        exp = int(
            (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        )
        token = self._make_token(
            {"sub": "user123", "type": "access", "exp": exp}
        )
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            payload = _fn(token)
        assert payload is not None
        assert payload["sub"] == "user123"

    def test_expired_token_returns_none(self, _fn):
        exp = int(
            (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
        )
        token = self._make_token(
            {"sub": "user123", "type": "access", "exp": exp}
        )
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _fn(token) is None

    def test_refresh_token_returns_none(self, _fn):
        """Token of type 'refresh' must be rejected."""
        exp = int(
            (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        )
        token = self._make_token(
            {"sub": "user123", "type": "refresh", "exp": exp}
        )
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _fn(token) is None

    def test_wrong_secret_returns_none(self, _fn):
        exp = int(
            (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        )
        token = self._make_token(
            {"sub": "user123", "type": "access", "exp": exp},
            secret="other-secret",
        )
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _fn(token) is None

    def test_missing_jwt_secret_returns_none(self, _fn):
        exp = int(
            (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        )
        token = self._make_token(
            {"sub": "user123", "type": "access", "exp": exp}
        )
        env_without_secret = {
            k: v for k, v in os.environ.items() if k != "JWT_SECRET_KEY"
        }
        with patch.dict(os.environ, env_without_secret, clear=True):
            assert _fn(token) is None


# ---------------------------------------------------------------------------
# Pagination math
# ---------------------------------------------------------------------------


class TestPaginationMath:
    """Verify the pagination formula used across Insights table tabs."""

    @staticmethod
    def _paginate(data: list, page: int, page_size: int) -> list:
        """Replicate the slice logic used in dashboard callbacks."""
        start = page * page_size
        end = start + page_size
        return data[start:end]

    def test_first_page(self):
        data = list(range(25))
        result = self._paginate(data, page=0, page_size=10)
        assert result == list(range(10))

    def test_second_page(self):
        data = list(range(25))
        result = self._paginate(data, page=1, page_size=10)
        assert result == list(range(10, 20))

    def test_last_partial_page(self):
        data = list(range(25))
        result = self._paginate(data, page=2, page_size=10)
        assert result == list(range(20, 25))

    def test_empty_data(self):
        assert self._paginate([], page=0, page_size=10) == []

    def test_page_beyond_data_returns_empty(self):
        data = list(range(5))
        assert self._paginate(data, page=1, page_size=10) == []


# ---------------------------------------------------------------------------
# Iceberg-backed data loader tests
# ---------------------------------------------------------------------------


class TestOhlcvAdjCloseNanFallback:
    """Tests for _get_ohlcv_cached falling back to close when adj_close is all NaN."""

    def test_adj_close_uses_close_when_all_nan(self):
        """When adj_close is all NaN, Adj Close column should use close values."""
        from unittest.mock import MagicMock, patch

        import numpy as np
        import pandas as pd

        dates = pd.date_range("2020-01-01", periods=50, freq="B")
        rng = np.random.default_rng(42)
        close = 100 + rng.standard_normal(50).cumsum()
        iceberg_df = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 50,
                "date": dates.date,
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "adj_close": [float("nan")] * 50,
                "volume": rng.integers(1_000_000, 5_000_000, 50),
            }
        )

        mock_repo = MagicMock()
        mock_repo.get_ohlcv.return_value = iceberg_df

        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=mock_repo,
        ):
            import dashboard.callbacks.iceberg as _ice

            _ice._OHLCV_CACHE.clear()

            from dashboard.callbacks.data_loaders import _load_raw

            result = _load_raw("AAPL")

        assert result is not None
        # Adj Close should contain close values, not NaN
        assert result["Adj Close"].notna().all()
        assert result["Adj Close"].iloc[-1] == pytest.approx(close[-1])

    def test_adj_close_used_when_valid(self):
        """When adj_close has real data, Adj Close should use those values."""
        from unittest.mock import MagicMock, patch

        import numpy as np
        import pandas as pd

        dates = pd.date_range("2020-01-01", periods=50, freq="B")
        rng = np.random.default_rng(42)
        close = 100 + rng.standard_normal(50).cumsum()
        adj_close = close * 0.95  # Different from close
        iceberg_df = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 50,
                "date": dates.date,
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "adj_close": adj_close,
                "volume": rng.integers(1_000_000, 5_000_000, 50),
            }
        )

        mock_repo = MagicMock()
        mock_repo.get_ohlcv.return_value = iceberg_df

        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=mock_repo,
        ):
            import dashboard.callbacks.iceberg as _ice

            _ice._OHLCV_CACHE.clear()

            from dashboard.callbacks.data_loaders import _load_raw

            result = _load_raw("AAPL")

        assert result is not None
        assert result["Adj Close"].iloc[-1] == pytest.approx(adj_close[-1])


class TestLoadRawFromIceberg:
    """Tests for :func:`dashboard.callbacks.data_loaders._load_raw` (Iceberg)."""

    def test_returns_dataframe_from_iceberg(self):
        """_load_raw must return a DataFrame with OHLCV columns from Iceberg."""
        from unittest.mock import MagicMock, patch

        import numpy as np
        import pandas as pd

        # Build Iceberg-shaped OHLCV data
        dates = pd.date_range("2020-01-01", periods=50, freq="B")
        rng = np.random.default_rng(42)
        close = 100 + rng.standard_normal(50).cumsum()
        iceberg_df = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 50,
                "date": dates.date,
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "adj_close": close,
                "volume": rng.integers(1_000_000, 5_000_000, 50),
            }
        )

        mock_repo = MagicMock()
        mock_repo.get_ohlcv.return_value = iceberg_df

        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=mock_repo,
        ):
            # Clear cache for test isolation
            import dashboard.callbacks.iceberg as _ice

            _ice._OHLCV_CACHE.clear()

            from dashboard.callbacks.data_loaders import _load_raw

            result = _load_raw("AAPL")

        assert result is not None
        assert "Open" in result.columns
        assert "Close" in result.columns
        assert len(result) == 50

    def test_returns_none_when_no_data(self):
        """_load_raw must return None when Iceberg has no OHLCV data."""
        from unittest.mock import MagicMock, patch

        import pandas as pd

        mock_repo = MagicMock()
        mock_repo.get_ohlcv.return_value = pd.DataFrame()

        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=mock_repo,
        ):
            import dashboard.callbacks.iceberg as _ice

            _ice._OHLCV_CACHE.clear()

            from dashboard.callbacks.data_loaders import _load_raw

            result = _load_raw("NOSUCH")

        assert result is None


class TestLoadForecastFromIceberg:
    """Tests for :func:`dashboard.callbacks.data_loaders._load_forecast` (Iceberg)."""

    def test_returns_forecast_from_iceberg(self):
        """_load_forecast must return a DataFrame with ds/yhat columns."""
        from unittest.mock import MagicMock, patch

        import pandas as pd

        dates = pd.date_range("2026-01-01", periods=60, freq="B")
        iceberg_df = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 60,
                "horizon_months": [9] * 60,
                "run_date": [pd.Timestamp("2025-12-31").date()] * 60,
                "forecast_date": dates.date,
                "predicted_price": [150.0 + i * 0.5 for i in range(60)],
                "lower_bound": [145.0 + i * 0.5 for i in range(60)],
                "upper_bound": [155.0 + i * 0.5 for i in range(60)],
            }
        )

        mock_repo = MagicMock()
        mock_repo.get_latest_forecast_series.return_value = iceberg_df

        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=mock_repo,
        ):
            import dashboard.callbacks.iceberg as _ice

            _ice._FORECAST_CACHE.clear()

            from dashboard.callbacks.data_loaders import _load_forecast

            result = _load_forecast("AAPL", 9)

        assert result is not None
        assert "ds" in result.columns
        assert "yhat" in result.columns
        assert len(result) == 60

    def test_returns_none_when_no_forecast(self):
        """_load_forecast must return None when Iceberg has no forecast."""
        from unittest.mock import MagicMock, patch

        import pandas as pd

        mock_repo = MagicMock()
        mock_repo.get_latest_forecast_series.return_value = pd.DataFrame()

        with patch(
            "dashboard.callbacks.iceberg._get_iceberg_repo",
            return_value=mock_repo,
        ):
            import dashboard.callbacks.iceberg as _ice

            _ice._FORECAST_CACHE.clear()

            from dashboard.callbacks.data_loaders import _load_forecast

            result = _load_forecast("NOSUCH", 9)

        assert result is None
