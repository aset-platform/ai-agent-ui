"""Tests for auto_link_ticker thread-local + repo flow."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestAutoLinkTicker:
    """Verify auto_link_ticker fires correctly."""

    def test_links_when_user_set(self, monkeypatch):
        """Calls repo.link_ticker with user + ticker."""
        from tools._ticker_linker import (
            auto_link_ticker,
            set_current_user,
        )

        mock_repo = MagicMock()
        mock_repo.link_ticker = AsyncMock(
            return_value=True,
        )

        set_current_user("user-123")
        with patch(
            "auth.endpoints.helpers._get_repo",
            return_value=mock_repo,
        ):
            auto_link_ticker("AAPL")

        mock_repo.link_ticker.assert_awaited_once_with(
            "user-123", "AAPL", source="chat",
        )
        # Cleanup
        set_current_user(None)

    def test_noop_when_no_user(self):
        """Returns silently when no user is set."""
        from tools._ticker_linker import (
            auto_link_ticker,
            set_current_user,
        )

        set_current_user(None)
        # Should not raise
        auto_link_ticker("AAPL")

    def test_catches_exceptions(self, monkeypatch):
        """Exceptions are caught, not raised."""
        from tools._ticker_linker import (
            auto_link_ticker,
            set_current_user,
        )

        set_current_user("user-456")
        with patch(
            "auth.endpoints.helpers._get_repo",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise
            auto_link_ticker("TCS.NS")
        set_current_user(None)
