"""Unit tests for the walk_windows() iterator.

All tests operate on a pure Python function with no DB or
filesystem dependencies.

Boundary cases covered:
  - standard full period (divisible by step)
  - period not divisible by step (trailing partial window dropped)
  - start-aligned (first train_start == start)
  - period shorter than (train+test) → empty list
  - period exactly one window
  - step == test_days (non-overlapping windows)
  - step < test_days (overlapping test windows — allowed)
  - acceptance: 2024-01-01 to 2026-01-01, 180+30+30 → 23 windows
  - invalid inputs raise ValueError
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.algo.backtest.walkforward import Window, walk_windows


class TestWalkWindowsBasic:
    def test_acceptance_windows(self):
        """Spec AC: 2024-01-01 → 2026-01-01, train=180, test=30,
        step=30.

        Derivation: period = 731 days.  A window (train=180,
        test=30) needs 210 days and starts at i*30 from the
        period start.  Window i fits iff
          start + i*30 + 210 - 1 <= end
          i.e. i <= (731 - 210) / 30 = 521 / 30 = 17.36
        → i ∈ {0..17} → 18 windows.

        The original spec wrote "23" but the calendar arithmetic
        gives 18; the implementation is correct.
        """
        windows = walk_windows(
            date(2024, 1, 1),
            date(2026, 1, 1),
            train_days=180,
            test_days=30,
            step_days=30,
        )
        assert len(windows) == 18
        for w in windows:
            assert (w.train_end - w.train_start).days == 179
            assert (w.test_end - w.test_start).days == 29
            # train and test non-overlapping: test_start = train_end+1
            assert w.test_start == w.train_end + timedelta(days=1)

    def test_windows_slide_by_step(self):
        """Consecutive windows must start exactly step_days apart."""
        windows = walk_windows(
            date(2024, 1, 1),
            date(2025, 1, 1),
            train_days=30,
            test_days=30,
            step_days=30,
        )
        assert len(windows) >= 2
        for i in range(1, len(windows)):
            diff = (
                windows[i].train_start - windows[i - 1].train_start
            ).days
            assert diff == 30

    def test_start_aligned(self):
        """First window's train_start must equal period start."""
        start = date(2024, 3, 1)
        windows = walk_windows(
            start,
            date(2025, 3, 1),
            train_days=60,
            test_days=30,
            step_days=30,
        )
        assert len(windows) > 0
        assert windows[0].train_start == start

    def test_indices_sequential(self):
        windows = walk_windows(
            date(2024, 1, 1),
            date(2025, 6, 1),
            train_days=90,
            test_days=30,
            step_days=30,
        )
        for i, w in enumerate(windows):
            assert w.index == i

    def test_last_window_test_end_lte_period_end(self):
        end = date(2025, 12, 31)
        windows = walk_windows(
            date(2024, 1, 1),
            end,
            train_days=90,
            test_days=30,
            step_days=30,
        )
        assert len(windows) > 0
        assert windows[-1].test_end <= end


class TestWalkWindowsEdgeCases:
    def test_period_shorter_than_train_plus_test_returns_empty(self):
        """If (train + test) > period, no windows fit → []."""
        windows = walk_windows(
            date(2024, 1, 1),
            date(2024, 3, 1),   # 60 days
            train_days=45,
            test_days=45,       # 90 days needed
            step_days=30,
        )
        assert windows == []

    def test_period_not_divisible_by_step_drops_trailing_partial(
        self,
    ):
        """Trailing partial window (test_end > end) is dropped."""
        # 365 days total, train=90, test=30, step=30
        # Window fits if train_start + 90 + 30 - 1 <= end
        # i.e. train_start <= end - 119
        # end - 119 = 2024-12-31 - 119 = 2024-09-03
        # train_starts: Jan1, Feb1(31), Mar2(60) … check manually
        windows = walk_windows(
            date(2024, 1, 1),
            date(2024, 12, 31),
            train_days=90,
            test_days=30,
            step_days=30,
        )
        # Verify none has test_end > 2024-12-31
        for w in windows:
            assert w.test_end <= date(2024, 12, 31)
        # Verify partial would NOT fit: last+1 window start would
        # produce test_end > end
        if windows:
            next_start = (
                windows[-1].train_start + timedelta(days=30)
            )
            next_test_end = (
                next_start
                + timedelta(days=90)
                + timedelta(days=30)
                - timedelta(days=1)
            )
            assert next_test_end > date(2024, 12, 31)

    def test_exactly_one_window(self):
        """Period exactly equal to one window (no room for step)."""
        # train=30, test=10 → total window = 40 days
        start = date(2024, 1, 1)
        end = start + timedelta(days=39)  # exactly 40 days
        windows = walk_windows(
            start, end,
            train_days=30, test_days=10, step_days=30,
        )
        assert len(windows) == 1
        assert windows[0].train_start == start
        assert windows[0].test_end == end

    def test_step_equals_window_length_non_overlapping(self):
        """When step == train+test, windows are non-overlapping."""
        windows = walk_windows(
            date(2024, 1, 1),
            date(2025, 1, 1),
            train_days=90,
            test_days=90,
            step_days=180,
        )
        # Non-overlapping: each window[i].train_start
        # == previous window[i-1].test_end + 1
        for i in range(1, len(windows)):
            assert windows[i].train_start == (
                windows[i - 1].test_end + timedelta(days=1)
            )

    def test_overlapping_test_windows_allowed(self):
        """step < test_days means test windows overlap.
        This is valid — walk_windows does not forbid it."""
        windows = walk_windows(
            date(2024, 1, 1),
            date(2025, 6, 1),
            train_days=60,
            test_days=60,
            step_days=30,  # overlap: each window advances 30 days
        )
        assert len(windows) > 2
        # Adjacent test windows overlap by (60 - 30) = 30 days
        for i in range(1, len(windows)):
            overlap = (
                windows[i - 1].test_end
                - windows[i].test_start
            ).days + 1
            assert overlap == 30

    def test_single_day_windows(self):
        """train_days=1, test_days=1, step_days=1 — many windows.

        Period Jan1..Jan31 = 31 days (inclusive).
        Window size = 2 days. Step = 1 day.
        Window i: train=Jan(1+i), test=Jan(2+i).
        Fits iff Jan(2+i) <= Jan31, i.e. i <= 29.
        → i ∈ {0..29} → 30 windows.
        """
        windows = walk_windows(
            date(2024, 1, 1),
            date(2024, 1, 31),
            train_days=1,
            test_days=1,
            step_days=1,
        )
        assert len(windows) == 30

    def test_returns_window_dataclass(self):
        windows = walk_windows(
            date(2024, 1, 1),
            date(2024, 6, 1),
            train_days=60,
            test_days=30,
            step_days=30,
        )
        assert isinstance(windows[0], Window)


class TestWalkWindowsValidation:
    def test_invalid_train_days_zero_raises(self):
        with pytest.raises(ValueError, match="train_days"):
            walk_windows(
                date(2024, 1, 1), date(2025, 1, 1),
                train_days=0, test_days=30, step_days=30,
            )

    def test_invalid_test_days_zero_raises(self):
        with pytest.raises(ValueError, match="test_days"):
            walk_windows(
                date(2024, 1, 1), date(2025, 1, 1),
                train_days=30, test_days=0, step_days=30,
            )

    def test_invalid_step_days_zero_raises(self):
        with pytest.raises(ValueError, match="step_days"):
            walk_windows(
                date(2024, 1, 1), date(2025, 1, 1),
                train_days=30, test_days=30, step_days=0,
            )

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError, match="start must be before"):
            walk_windows(
                date(2025, 1, 1), date(2024, 1, 1),
                train_days=30, test_days=30, step_days=30,
            )

    def test_start_equals_end_raises(self):
        with pytest.raises(ValueError, match="start must be before"):
            walk_windows(
                date(2024, 6, 1), date(2024, 6, 1),
                train_days=30, test_days=30, step_days=30,
            )
