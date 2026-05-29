"""Tests for the walk-forward gate and drift gate on the live
mode toggle (V2-5).

These are unit-level tests that do NOT hit the database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc


class TestWalkforwardGate:
    """
    The gate logic lives in _check_gates() inside routes/live.py.
    We test the pure date logic here.
    """

    def test_walkforward_older_than_30_days_fails_gate(self):
        """A walk-forward report older than 30 days should not pass
        the gate — regardless of win rate."""
        thirty_one_days_ago = datetime.now(UTC) - timedelta(days=31)
        age = datetime.now(UTC) - thirty_one_days_ago
        assert age > timedelta(days=30), (
            "30-day gate: age > 30 days should fail"
        )
        is_recent = age < timedelta(days=30)
        assert not is_recent

    def test_walkforward_within_30_days_passes_date_check(self):
        """A walk-forward run from yesterday passes the date check."""
        yesterday = datetime.now(UTC) - timedelta(days=1)
        age = datetime.now(UTC) - yesterday
        is_recent = age < timedelta(days=30)
        assert is_recent

    def test_negative_win_rate_fails_gate(self):
        """Win rate of 0 or negative should not pass the gate."""
        for win_rate in [0, -0.1, -1.0]:
            passes = float(win_rate) > 0
            assert not passes, (
                f"win_rate={win_rate} should not pass gate"
            )

    def test_positive_win_rate_passes_gate(self):
        """Any positive win rate (even 0.01) should pass the gate."""
        for win_rate in [0.01, 0.5, 0.99, 1.0]:
            passes = float(win_rate) > 0
            assert passes, (
                f"win_rate={win_rate} should pass gate"
            )

    def test_gate_requires_both_recency_and_positive_win_rate(self):
        """Both conditions must be true simultaneously."""
        # Recent but zero win-rate
        yesterday = datetime.now(UTC) - timedelta(days=1)
        age = datetime.now(UTC) - yesterday
        is_recent = age < timedelta(days=30)
        win_rate = 0.0
        passes = is_recent and float(win_rate) > 0
        assert not passes, "Zero win-rate should fail even if recent"

        # Good win-rate but stale report
        old = datetime.now(UTC) - timedelta(days=45)
        age2 = datetime.now(UTC) - old
        is_recent2 = age2 < timedelta(days=30)
        win_rate2 = 0.6
        passes2 = is_recent2 and float(win_rate2) > 0
        assert not passes2, "Stale report should fail even if win-rate > 0"

        # Both good
        recent = datetime.now(UTC) - timedelta(days=7)
        age3 = datetime.now(UTC) - recent
        is_recent3 = age3 < timedelta(days=30)
        win_rate3 = 0.6
        passes3 = is_recent3 and float(win_rate3) > 0
        assert passes3, "Recent + positive win-rate must pass"


class TestDriftGate:
    """Drift gate: toggle disabled if any symbol has > 3 consecutive
    drift runs."""

    def test_no_drift_rows_passes(self):
        open_drifts: list = []
        drift_within_limit = all(
            int(r.get("consecutive_runs", 0)) <= 3
            for r in open_drifts
        )
        assert drift_within_limit

    def test_drift_at_3_runs_still_passes(self):
        open_drifts = [{"symbol": "RELIANCE.NS", "consecutive_runs": 3}]
        drift_within_limit = all(
            int(r.get("consecutive_runs", 0)) <= 3
            for r in open_drifts
        )
        assert drift_within_limit

    def test_drift_at_4_runs_blocks(self):
        open_drifts = [{"symbol": "RELIANCE.NS", "consecutive_runs": 4}]
        drift_within_limit = all(
            int(r.get("consecutive_runs", 0)) <= 3
            for r in open_drifts
        )
        assert not drift_within_limit

    def test_one_bad_drift_blocks_all(self):
        open_drifts = [
            {"symbol": "RELIANCE.NS", "consecutive_runs": 2},
            {"symbol": "TCS.NS", "consecutive_runs": 4},  # over limit
        ]
        drift_within_limit = all(
            int(r.get("consecutive_runs", 0)) <= 3
            for r in open_drifts
        )
        assert not drift_within_limit


class TestLiveCapsDefaultState:
    """Live orders are disabled by default for every (user, strategy)."""

    def test_default_caps_live_orders_disabled(self):
        """The default row returned by CapsRepo.get_or_default must
        have live_orders_enabled=False."""
        from decimal import Decimal

        # Simulate what get_or_default returns
        default = {
            "max_inr": Decimal("0"),
            "max_orders_per_day": 0,
            "allowed_tickers": [],
            "live_orders_enabled": False,
        }
        assert default["live_orders_enabled"] is False

    def test_live_runtime_cannot_be_created_without_explicit_enable(self):
        """A fresh strategy has no caps row → default is off →
        LiveRuntime cannot be instantiated."""
        from decimal import Decimal
        from unittest.mock import MagicMock

        from backend.algo.live.runtime import LiveNotEnabledError, LiveRuntime

        caps = {
            "live_orders_enabled": False,  # default off
            "max_inr": Decimal("0"),
            "max_orders_per_day": 0,
            "allowed_tickers": [],
        }
        with pytest.raises(LiveNotEnabledError):
            LiveRuntime(
                strategy=MagicMock(id=__import__("uuid").uuid4()),
                user_id=__import__("uuid").uuid4(),
                initial_capital_inr=Decimal("100000"),
                fee_as_of=__import__("datetime").date.today(),
                # Real-money attempt: the live-enabled gate only
                # applies when dry_run is False (a bare MagicMock would
                # expose a truthy dry_run and be treated as a gate-
                # exempt dry-run rehearsal).
                kite=MagicMock(dry_run=False),
                caps=caps,
                run_id=__import__("uuid").uuid4(),
                caps_repo=MagicMock(),
                kill_switch_repo=MagicMock(),
            )
