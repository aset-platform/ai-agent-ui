"""Tests for backend.market_hours: shared is_market_open helper."""

from datetime import datetime
from unittest.mock import patch

import pytest

from market_hours import IST, is_market_open


def _make_now(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


class TestIsMarketOpen:
    @pytest.mark.parametrize(
        "hour,minute,expected",
        [
            (8, 59, False),   # pre-open
            (9, 0, True),     # open edge
            (10, 30, True),   # mid-session
            (15, 30, True),   # close edge
            (15, 31, False),  # post-close
        ],
    )
    def test_weekday_window(self, hour, minute, expected):
        # 2026-06-01 is a Monday.
        with patch("market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = _make_now(
                2026, 6, 1, hour, minute,
            )
            assert is_market_open() is expected

    def test_saturday_closed(self):
        # 2026-06-06 is a Saturday.
        with patch("market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = _make_now(
                2026, 6, 6, 10, 0,
            )
            assert is_market_open() is False

    def test_sunday_closed(self):
        # 2026-06-07 is a Sunday.
        with patch("market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = _make_now(
                2026, 6, 7, 10, 0,
            )
            assert is_market_open() is False
