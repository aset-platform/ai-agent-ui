"""Tests for the pure stop-loss trigger function."""

from decimal import Decimal

import pytest

from backend.algo.backtest.stop_loss_monitor import (
    StopLossTrigger,
    check_stop_loss_triggers,
)


def _open_position(qty: int = 100,
                   avg_price: float = 100.0) -> dict:
    return {"qty": qty, "avg_price": Decimal(str(avg_price))}


def test_trigger_when_loss_exceeds_threshold():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("96")},
        stop_loss_pct=3.0,
    )
    assert len(triggers) == 1
    t = triggers[0]
    assert t.ticker == "AAA.NS"
    assert t.avg_price == Decimal("100")
    assert t.current_close == Decimal("96")
    assert t.loss_pct == Decimal("-4")
    assert t.stop_loss_pct == Decimal("3.0")


def test_no_trigger_when_loss_below_threshold():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("98")},
        stop_loss_pct=3.0,
    )
    assert triggers == []


def test_trigger_at_exact_boundary_is_inclusive():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("97")},
        stop_loss_pct=3.0,
    )
    assert len(triggers) == 1
    assert triggers[0].loss_pct == Decimal("-3")


def test_no_trigger_when_position_gains():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("105")},
        stop_loss_pct=3.0,
    )
    assert triggers == []


def test_disabled_when_stop_loss_pct_zero():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("50")},
        stop_loss_pct=0.0,
    )
    assert triggers == []


def test_skip_ticker_with_no_current_close():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={},
        stop_loss_pct=3.0,
    )
    assert triggers == []


def test_skip_position_with_zero_avg_price():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=0)},
        current_closes={"AAA.NS": Decimal("50")},
        stop_loss_pct=3.0,
    )
    assert triggers == []


def test_multi_position_independence():
    triggers = check_stop_loss_triggers(
        open_positions={
            "AAA.NS": _open_position(avg_price=100),
            "BBB.NS": _open_position(avg_price=100),
            "CCC.NS": _open_position(avg_price=100),
        },
        current_closes={
            "AAA.NS": Decimal("96"),
            "BBB.NS": Decimal("99"),
            "CCC.NS": Decimal("105"),
        },
        stop_loss_pct=3.0,
    )
    assert len(triggers) == 1
    assert triggers[0].ticker == "AAA.NS"
