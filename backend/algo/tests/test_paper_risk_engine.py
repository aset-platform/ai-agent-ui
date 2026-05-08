"""3-tier risk engine: per-trade / portfolio / daily."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.paper.risk_engine import RiskEngine
from backend.algo.paper.types import (
    AccountState, RejectReason, Signal,
)


def _signal(side="BUY", qty=10, ticker="X") -> Signal:
    return Signal(
        strategy_id=uuid4(), user_id=uuid4(),
        ticker=ticker, side=side, qty=qty,
        emitted_at_ns=0,
    )


def _account(**kw) -> AccountState:
    base = {
        "user_id": uuid4(),
        "day_date": date(2026, 4, 1),
        "initial_capital_inr": Decimal("100000"),
        "current_equity_inr": Decimal("100000"),
        "daily_realised_pnl_inr": Decimal("0"),
        "daily_unrealised_pnl_inr": Decimal("0"),
        "open_positions": {},
        "open_position_count": 0,
        "kill_switch_active": False,
    }
    base.update(kw)
    return AccountState(**base)


_RISK = {
    "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
    "portfolio": {
        "max_exposure_pct": 80,
        "max_concentration_pct": 25,
    },
    "daily": {
        "max_loss_pct": 2,
        "max_open_positions": 10,
    },
}


def test_accept_when_all_caps_clear():
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(qty=10),
        account=_account(),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "accept"


def test_kill_switch_short_circuits():
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(),
        account=_account(kill_switch_active=True),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.KILL_SWITCH


def test_reject_when_qty_exceeds_per_trade_max():
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(qty=101),
        account=_account(),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.MAX_QTY


def test_reject_when_daily_loss_cap_breached():
    engine = RiskEngine()
    # 2% of 100k = 2000; current loss = 2500 → already past cap.
    decision = engine.gate(
        signal=_signal(),
        account=_account(
            daily_realised_pnl_inr=Decimal("-2500"),
        ),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.DAILY_LOSS_CAP


def test_reject_when_max_open_positions_reached():
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(ticker="NEW"),
        account=_account(open_position_count=10),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.MAX_OPEN_POSITIONS


def _wide_max_qty_risk() -> dict:
    """Risk config with relaxed per-trade max_qty so portfolio
    tests aren't short-circuited by the per-trade gate."""
    risk = {**_RISK}
    risk["per_trade"] = {"stop_loss_pct": 5, "max_qty": 1000}
    return risk


def test_reject_when_concentration_breached():
    """A 30k order at 100/share + existing 10k position = 40k in
    one ticker, which is 40% of 100k equity > 25% cap.
    """
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(ticker="X", qty=300),
        account=_account(
            open_positions={"X": 100},  # already 10k notional
        ),
        risk=_wide_max_qty_risk(),
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.POSITION_CAP


def test_scale_when_exposure_cap_partially_blocks():
    """Existing 70k open exposure + a 20k order = 90k of 80k cap.
    Engine scales the order down to 10k worth (= 100 shares at 100).
    """
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(ticker="NEW", qty=200),
        account=_account(
            open_positions={"OTHER": 700},  # 70k notional
            open_position_count=1,
        ),
        risk=_wide_max_qty_risk(),
        last_price=Decimal("100"),
    )
    assert decision.outcome == "scale"
    assert decision.adjusted_qty == 100


def test_sell_signals_skip_exposure_checks():
    """SELL reduces exposure — should never be blocked by
    portfolio caps even when existing exposure dwarfs the cap."""
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(side="SELL", ticker="X", qty=300),
        account=_account(
            open_positions={"X": 1000},  # large existing long
            open_position_count=1,
        ),
        risk=_wide_max_qty_risk(),
        last_price=Decimal("100"),
    )
    assert decision.outcome == "accept"
