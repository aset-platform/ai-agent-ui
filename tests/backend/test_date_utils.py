"""Tests for tools._date_utils — date parsing and recency."""

import time
from datetime import datetime, timezone

from tools._date_utils import (
    is_within_window,
    parse_published,
    time_decay_weight,
)


class TestParsePublished:
    """parse_published handles multiple formats."""

    def test_unix_timestamp_int(self):
        dt = parse_published("1711540200")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_unix_timestamp_recent(self):
        ts = str(int(time.time()) - 3600)
        dt = parse_published(ts)
        assert dt is not None
        age = (
            datetime.now(timezone.utc) - dt
        ).total_seconds()
        assert 3500 < age < 3700

    def test_rfc2822(self):
        raw = "Thu, 27 Mar 2026 14:30:00 GMT"
        dt = parse_published(raw)
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 27

    def test_iso8601_z(self):
        dt = parse_published("2026-03-27T14:30:00Z")
        assert dt is not None
        assert dt.year == 2026

    def test_iso8601_offset(self):
        dt = parse_published(
            "2026-03-27T14:30:00+05:30",
        )
        assert dt is not None

    def test_iso8601_date_only(self):
        dt = parse_published("2026-03-27")
        assert dt is not None
        assert dt.day == 27

    def test_empty_string(self):
        assert parse_published("") is None

    def test_none_like(self):
        assert parse_published(None) is None

    def test_garbage(self):
        assert parse_published("not-a-date") is None

    def test_zero_ts_rejected(self):
        """Epoch 0 is outside valid range."""
        assert parse_published("0") is None


class TestIsWithinWindow:
    """is_within_window filters by age."""

    def test_recent_article_passes(self):
        ts = str(int(time.time()) - 3600)
        assert is_within_window(ts, 7) is True

    def test_old_article_rejected(self):
        ts = str(int(time.time()) - 30 * 86400)
        assert is_within_window(ts, 7) is False

    def test_unparseable_kept(self):
        """Conservative: unknown dates pass."""
        assert is_within_window("", 7) is True
        assert is_within_window("garbage", 7) is True

    def test_just_inside_boundary(self):
        ts = str(int(time.time()) - 7 * 86400 + 60)
        assert is_within_window(ts, 7) is True

    def test_just_over_boundary(self):
        ts = str(int(time.time()) - 7 * 86400 - 60)
        assert is_within_window(ts, 7) is False


class TestTimeDecayWeight:
    """time_decay_weight returns correct brackets."""

    def test_fresh_1_day(self):
        ts = str(int(time.time()) - 86400)
        assert time_decay_weight(ts) == 1.0

    def test_3_days(self):
        ts = str(int(time.time()) - 3 * 86400)
        assert time_decay_weight(ts) == 0.5

    def test_5_days(self):
        ts = str(int(time.time()) - 5 * 86400)
        assert time_decay_weight(ts) == 0.5

    def test_10_days(self):
        ts = str(int(time.time()) - 10 * 86400)
        assert time_decay_weight(ts) == 0.25

    def test_60_days(self):
        ts = str(int(time.time()) - 60 * 86400)
        assert time_decay_weight(ts) == 0.1

    def test_unparseable_neutral(self):
        assert time_decay_weight("") == 0.5
        assert time_decay_weight("garbage") == 0.5
