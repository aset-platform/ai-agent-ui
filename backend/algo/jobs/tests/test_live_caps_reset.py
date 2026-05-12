"""Tests for live_caps_reset startup catch-up + main reset.

ASETPLTFRM-375 — verifies ``run_if_missed_today`` correctly
detects a missed 09:00 IST reset and replays it, but is a no-op
on weekends / pre-09:00 / already-reset-today.

Also verifies ``run_live_caps_daily_reset`` stamps the Redis
last-reset key on success so the next startup skips the replay.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.algo.jobs import live_caps_reset as mod


UTC = timezone.utc
IST = timedelta(hours=5, minutes=30)


def _ist(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build a UTC datetime that, when viewed in IST, hits the
    given wall-clock. We use this as the *underlying UTC* the
    module reads via ``datetime.now(UTC)``.
    """
    return datetime(year, month, day, hour, minute, tzinfo=UTC) - IST


class _FakeRedis:
    """Minimal sync redis mock — get/set only."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = str(value)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Replace ``_get_redis_sync`` with a stub returning a stable
    in-memory fake. All env-driven paths route through this single
    instance so set-then-get in a test sees its own writes.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(mod, "_get_redis_sync", lambda: fake)
    return fake


# ---- run_if_missed_today ----------------------------------------------


class TestRunIfMissedToday:
    async def test_weekend_skip(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis,
    ) -> None:
        # 2026-05-09 is a Saturday IST.
        monkeypatch.setattr(
            mod, "_ist_now",
            lambda: datetime(2026, 5, 9, 10, 0, tzinfo=UTC),
        )
        result = await mod.run_if_missed_today()
        assert result == {"skipped": True, "reason": "weekend"}

    async def test_pre_reset_hour_skip(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis,
    ) -> None:
        # 2026-05-12 (Tuesday) at 08:30 IST — scheduler will fire at 09:00.
        monkeypatch.setattr(
            mod, "_ist_now",
            lambda: datetime(2026, 5, 12, 8, 30, tzinfo=UTC),
        )
        result = await mod.run_if_missed_today()
        assert result == {
            "skipped": True, "reason": "before_reset_hour",
        }

    async def test_already_reset_today_skip(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis,
    ) -> None:
        monkeypatch.setattr(
            mod, "_ist_now",
            lambda: datetime(2026, 5, 12, 10, 0, tzinfo=UTC),
        )
        # Pre-populate the last-reset stamp to today.
        fake_redis.set(mod._LAST_RESET_KEY, "2026-05-12")
        result = await mod.run_if_missed_today()
        assert result == {"skipped": True, "reason": "already_reset"}

    async def test_missed_reset_replays(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis,
    ) -> None:
        monkeypatch.setattr(
            mod, "_ist_now",
            lambda: datetime(2026, 5, 12, 10, 30, tzinfo=UTC),
        )
        # Yesterday's stamp — today is uncovered.
        fake_redis.set(mod._LAST_RESET_KEY, "2026-05-11")

        # Mock the inner repo so we don't touch a real DB.
        fake_repo = MagicMock()
        fake_repo.reset_daily_counters = AsyncMock(return_value=3)
        with patch(
            "backend.algo.live.caps_repo.CapsRepo",
            return_value=fake_repo,
        ):
            result = await mod.run_if_missed_today()
        assert result == {
            "skipped": False, "rows_reset": 3, "catchup": True,
        }
        # The reset should have stamped today's date.
        assert fake_redis.get(mod._LAST_RESET_KEY) == "2026-05-12"
        fake_repo.reset_daily_counters.assert_awaited_once_with(
            user_id=None,
        )

    async def test_missing_stamp_replays(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis,
    ) -> None:
        # Same as previous, but the Redis key was never set
        # (e.g. fresh deploy or post-FLUSHALL). Should still fire.
        monkeypatch.setattr(
            mod, "_ist_now",
            lambda: datetime(2026, 5, 12, 11, 0, tzinfo=UTC),
        )
        fake_repo = MagicMock()
        fake_repo.reset_daily_counters = AsyncMock(return_value=1)
        with patch(
            "backend.algo.live.caps_repo.CapsRepo",
            return_value=fake_repo,
        ):
            result = await mod.run_if_missed_today()
        assert result["skipped"] is False
        assert result["rows_reset"] == 1
        assert result["catchup"] is True

    async def test_redis_unavailable_still_replays(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If Redis is down, fall through to running the reset.
        Better to redundantly zero than miss a missed reset.
        """
        monkeypatch.setattr(
            mod, "_ist_now",
            lambda: datetime(2026, 5, 12, 12, 0, tzinfo=UTC),
        )
        monkeypatch.setattr(mod, "_get_redis_sync", lambda: None)
        fake_repo = MagicMock()
        fake_repo.reset_daily_counters = AsyncMock(return_value=2)
        with patch(
            "backend.algo.live.caps_repo.CapsRepo",
            return_value=fake_repo,
        ):
            result = await mod.run_if_missed_today()
        assert result["skipped"] is False
        assert result["catchup"] is True


# ---- run_live_caps_daily_reset stamping --------------------------------


class TestResetStampsRedis:
    async def test_success_writes_stamp(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis,
    ) -> None:
        monkeypatch.setattr(
            mod, "_ist_now",
            lambda: datetime(2026, 5, 12, 9, 0, tzinfo=UTC),
        )
        fake_repo = MagicMock()
        fake_repo.reset_daily_counters = AsyncMock(return_value=5)
        with patch(
            "backend.algo.live.caps_repo.CapsRepo",
            return_value=fake_repo,
        ):
            result = await mod.run_live_caps_daily_reset()
        assert result == {"skipped": False, "rows_reset": 5}
        assert fake_redis.get(mod._LAST_RESET_KEY) == "2026-05-12"

    async def test_weekend_does_not_stamp(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis,
    ) -> None:
        monkeypatch.setattr(
            mod, "_ist_now",
            lambda: datetime(2026, 5, 9, 10, 0, tzinfo=UTC),
        )
        result = await mod.run_live_caps_daily_reset()
        assert result["skipped"] is True
        assert fake_redis.get(mod._LAST_RESET_KEY) is None
