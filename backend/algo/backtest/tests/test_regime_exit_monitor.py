"""Tests for the mid-trade regime exit monitor (ASETPLTFRM-435)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.algo.backtest.regime_exit_monitor import (
    check_regime_exit_triggers,
)


# Sample condition tree (dumped form): nifty_above_sma200 >= 1
# AND nifty_30d_return_pct > -5.0  — the v3 regime gate's entry-
# side condition reused as mid-trade check.
_REGIME_CHECK = {
    "type": "and",
    "operands": [
        {
            "type": "compare",
            "left": {"feature": "nifty_above_sma200"},
            "op": ">=",
            "right": {"literal": 1},
        },
        {
            "type": "compare",
            "left": {"feature": "nifty_30d_return_pct"},
            "op": ">",
            "right": {"literal": -5.0},
        },
    ],
}


def test_disabled_when_regime_check_none():
    triggers = check_regime_exit_triggers(
        open_positions={"AAA.NS": {"qty": 100}},
        bar_date=date(2025, 1, 15),
        market_features={
            "nifty_above_sma200": Decimal("0"),
            "nifty_30d_return_pct": Decimal("-10"),
        },
        regime_check=None,
    )
    assert triggers == []


def test_no_op_when_no_open_positions():
    triggers = check_regime_exit_triggers(
        open_positions={},
        bar_date=date(2025, 1, 15),
        market_features={
            "nifty_above_sma200": Decimal("0"),
            "nifty_30d_return_pct": Decimal("-10"),
        },
        regime_check=_REGIME_CHECK,
    )
    assert triggers == []


def test_regime_safe_no_exits():
    triggers = check_regime_exit_triggers(
        open_positions={
            "AAA.NS": {"qty": 100},
            "BBB.NS": {"qty": 50},
        },
        bar_date=date(2025, 1, 15),
        market_features={
            "nifty_above_sma200": Decimal("1"),
            "nifty_30d_return_pct": Decimal("2.5"),
        },
        regime_check=_REGIME_CHECK,
    )
    assert triggers == []


def test_regime_hostile_all_positions_exit():
    triggers = check_regime_exit_triggers(
        open_positions={
            "AAA.NS": {"qty": 100},
            "BBB.NS": {"qty": 50},
            "CCC.NS": {"qty": 25},
        },
        bar_date=date(2025, 1, 15),
        market_features={
            "nifty_above_sma200": Decimal("0"),
            "nifty_30d_return_pct": Decimal("-6.0"),
        },
        regime_check=_REGIME_CHECK,
    )
    # All three positions force-exit (portfolio-wide kill).
    assert len(triggers) == 3
    assert {t.ticker for t in triggers} == {
        "AAA.NS", "BBB.NS", "CCC.NS",
    }
    for t in triggers:
        assert t.bar_date == date(2025, 1, 15)


def test_partial_regime_failure_still_triggers_all():
    # Only one of the AND-operands fails — still failing overall.
    triggers = check_regime_exit_triggers(
        open_positions={"AAA.NS": {"qty": 100}},
        bar_date=date(2025, 1, 15),
        market_features={
            "nifty_above_sma200": Decimal("1"),     # this passes
            "nifty_30d_return_pct": Decimal("-7.5"),  # but this fails
        },
        regime_check=_REGIME_CHECK,
    )
    assert len(triggers) == 1
    assert triggers[0].ticker == "AAA.NS"


def test_missing_market_feature_fails_open():
    # If the regime check references a feature not in market_features,
    # we fail open (no force-exit) so a transient regime-cache gap
    # doesn't trigger a portfolio-wide kill.
    triggers = check_regime_exit_triggers(
        open_positions={"AAA.NS": {"qty": 100}},
        bar_date=date(2025, 1, 15),
        market_features={
            "nifty_above_sma200": Decimal("1"),
            # nifty_30d_return_pct missing
        },
        regime_check=_REGIME_CHECK,
    )
    assert triggers == []


def test_triggers_sorted_for_deterministic_iteration():
    triggers = check_regime_exit_triggers(
        open_positions={
            "ZZZ.NS": {"qty": 1},
            "AAA.NS": {"qty": 1},
            "MMM.NS": {"qty": 1},
        },
        bar_date=date(2025, 1, 15),
        market_features={
            "nifty_above_sma200": Decimal("0"),
            "nifty_30d_return_pct": Decimal("0"),
        },
        regime_check=_REGIME_CHECK,
    )
    # Sorted alphabetically — deterministic order for tests + logs.
    assert [t.ticker for t in triggers] == [
        "AAA.NS", "MMM.NS", "ZZZ.NS",
    ]
