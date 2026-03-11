"""Tests for user-ticker wiring across dashboard callbacks.

Covers:
- ``_fetch_user_tickers()`` helper (dict/list/error)
- Marketplace pagination math (edge cases)
- Quarterly pagination math (edge cases)

These are pure-function / unit tests — no Dash app needed.
"""

from unittest.mock import MagicMock, patch

import pytest

# -----------------------------------------------------------
# _fetch_user_tickers
# -----------------------------------------------------------


class TestFetchUserTickers:
    """Tests for ``auth_utils._fetch_user_tickers``."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.callbacks.auth_utils import (
            _fetch_user_tickers,
        )

        self._fn = _fetch_user_tickers

    def test_none_token_returns_none(self):
        """No token → None (show all tickers)."""
        assert self._fn(None) is None

    def test_empty_token_returns_none(self):
        """Empty string → None."""
        assert self._fn("") is None

    def test_dict_response(self):
        """Backend returns ``{tickers: [...]}``."""
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {
            "tickers": ["AAPL", "MSFT"],
        }
        with patch(
            "dashboard.callbacks.auth_utils._api_call",
            return_value=resp,
        ):
            result = self._fn("valid-token")
        assert result == {"AAPL", "MSFT"}

    def test_list_response(self):
        """Backend returns plain list."""
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = ["TSLA", "GOOG"]
        with patch(
            "dashboard.callbacks.auth_utils._api_call",
            return_value=resp,
        ):
            result = self._fn("valid-token")
        assert result == {"TSLA", "GOOG"}

    def test_api_failure_returns_none(self):
        """Connection error → None."""
        with patch(
            "dashboard.callbacks.auth_utils._api_call",
            return_value=None,
        ):
            assert self._fn("valid-token") is None

    def test_http_error_returns_none(self):
        """Non-2xx response → None."""
        resp = MagicMock()
        resp.ok = False
        with patch(
            "dashboard.callbacks.auth_utils._api_call",
            return_value=resp,
        ):
            assert self._fn("valid-token") is None

    def test_empty_tickers_dict(self):
        """Backend returns ``{tickers: []}``."""
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"tickers": []}
        with patch(
            "dashboard.callbacks.auth_utils._api_call",
            return_value=resp,
        ):
            result = self._fn("valid-token")
        assert result == set()

    def test_unexpected_type_returns_none(self):
        """Backend returns unexpected type → None."""
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = 42
        with patch(
            "dashboard.callbacks.auth_utils._api_call",
            return_value=resp,
        ):
            assert self._fn("valid-token") is None


# -----------------------------------------------------------
# Pagination math (1-based, as used in callbacks)
# -----------------------------------------------------------


class TestOneBasedPaginationMath:
    """Verify the 1-based pagination formula used in
    marketplace, quarterly, and insights callbacks.
    """

    @staticmethod
    def _paginate(total, page, page_size):
        """Replicate the slice logic from callbacks.

        Returns (start_idx, end_idx, max_pages, clamped_page).
        """
        max_pages = max(1, -(-total // page_size))
        page = min(page or 1, max_pages)
        start = (page - 1) * page_size
        end = start + page_size
        return start, end, max_pages, page

    def test_first_page(self):
        s, e, mx, p = self._paginate(25, 1, 10)
        assert (s, e) == (0, 10)
        assert mx == 3
        assert p == 1

    def test_last_partial_page(self):
        s, e, mx, p = self._paginate(25, 3, 10)
        assert (s, e) == (20, 30)
        assert mx == 3

    def test_page_clamped_to_max(self):
        """Page beyond max is clamped."""
        _, _, mx, p = self._paginate(5, 99, 10)
        assert mx == 1
        assert p == 1

    def test_empty_data(self):
        _, _, mx, p = self._paginate(0, 1, 10)
        assert mx == 1
        assert p == 1

    def test_exact_multiple(self):
        """20 items / 10 per page = 2 pages."""
        _, _, mx, _ = self._paginate(20, 1, 10)
        assert mx == 2

    def test_page_size_25(self):
        _, _, mx, _ = self._paginate(100, 1, 25)
        assert mx == 4

    def test_page_size_50(self):
        _, _, mx, _ = self._paginate(100, 1, 50)
        assert mx == 2

    def test_single_item(self):
        s, e, mx, p = self._paginate(1, 1, 10)
        assert (s, e) == (0, 10)
        assert mx == 1
        assert p == 1


# -----------------------------------------------------------
# Marketplace sort column definitions
# -----------------------------------------------------------


class TestMarketplaceColDefs:
    """Verify marketplace column definitions match the
    expected keys used in the render callback.
    """

    def test_column_keys_present(self):
        from dashboard.callbacks.marketplace_cbs import (
            _MARKETPLACE_COL_DEFS,
        )

        keys = [d["key"] for d in _MARKETPLACE_COL_DEFS]
        assert "ticker" in keys
        assert "company" in keys
        assert "_action" in keys

    def test_all_defs_have_key_and_label(self):
        from dashboard.callbacks.marketplace_cbs import (
            _MARKETPLACE_COL_DEFS,
        )

        for d in _MARKETPLACE_COL_DEFS:
            assert "key" in d, f"Missing 'key' in {d}"
            assert "label" in d, f"Missing 'label' in {d}"
