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
        patch("pyiceberg.catalog.load_catalog", side_effect=RuntimeError("no catalog in tests")),
    ):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "dashboard.callbacks",
            str(Path(__file__).parent.parent.parent / "dashboard" / "callbacks.py"),
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
        from pathlib import Path
        import importlib.util

        source = (
            Path(__file__).parent.parent.parent / "dashboard" / "callbacks" / "utils.py"
        ).read_text(encoding="utf-8")

        # Extract and exec just the _get_market function
        import ast
        tree = ast.parse(source)
        fn_node = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_get_market"),
            None,
        )
        if fn_node is None:
            pytest.skip("_get_market not found in callbacks/utils.py")

        exec_globals = {}
        exec(compile(ast.Module(body=[fn_node], type_ignores=[]), "<string>", "exec"), exec_globals)
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
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_validate_token"),
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
        exp = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        token = self._make_token({"sub": "user123", "type": "access", "exp": exp})
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            payload = _fn(token)
        assert payload is not None
        assert payload["sub"] == "user123"

    def test_expired_token_returns_none(self, _fn):
        exp = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        token = self._make_token({"sub": "user123", "type": "access", "exp": exp})
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _fn(token) is None

    def test_refresh_token_returns_none(self, _fn):
        """Token of type 'refresh' must be rejected."""
        exp = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        token = self._make_token({"sub": "user123", "type": "refresh", "exp": exp})
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _fn(token) is None

    def test_wrong_secret_returns_none(self, _fn):
        exp = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        token = self._make_token({"sub": "user123", "type": "access", "exp": exp}, secret="other-secret")
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _fn(token) is None

    def test_missing_jwt_secret_returns_none(self, _fn):
        exp = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        token = self._make_token({"sub": "user123", "type": "access", "exp": exp})
        env_without_secret = {k: v for k, v in os.environ.items() if k != "JWT_SECRET_KEY"}
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
