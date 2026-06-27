"""Risk engine: NaN inputs must fail CLOSED (reject), not pass.

When any critical numeric input — last_price, current_equity_inr,
daily_realised_pnl_inr, daily_unrealised_pnl_inr — is NaN the gate
must return outcome="reject" with reason=RejectReason.INVALID_INPUT
rather than silently passing (NaN comparisons are all-False in Python).

Note: Pydantic v2 rejects Decimal("NaN") at AccountState construction
time, so account-field NaN tests use ``model_construct`` to bypass
validation.  This mirrors the realistic failure scenario: a NaN value
that bypasses validation (e.g. raw dict deserialisation, direct ORM
assignment, or a future schema loosening) must still be caught inside
``gate()``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.paper.risk_engine import RiskEngine, _is_non_finite
from backend.algo.paper.types import AccountState, RejectReason, Signal


def _signal(side: str = "BUY", qty: int = 10, ticker: str = "X") -> Signal:
    return Signal(
        strategy_id=uuid4(),
        user_id=uuid4(),
        ticker=ticker,
        side=side,
        qty=qty,
        emitted_at_ns=0,
    )


def _account(**kw) -> AccountState:
    """Build a valid AccountState via normal constructor."""
    base: dict = {
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


def _account_nan(**kw) -> AccountState:
    """Build an AccountState with NaN fields via model_construct,
    bypassing Pydantic's finite-number validation.  Simulates data
    that arrives through deserialization or a future schema change
    without re-validation.
    """
    base: dict = {
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
    return AccountState.model_construct(**base)


_RISK = {
    "per_trade": {"max_qty": 100},
    "portfolio": {
        "max_exposure_pct": 80,
        "max_concentration_pct": 25,
    },
    "daily": {
        "max_loss_pct": 2,
        "max_open_positions": 10,
    },
}

_NAN = Decimal("NaN")
_ENGINE = RiskEngine()


# ---------------------------------------------------------------------------
# Non-finite rejection tests (NaN, inf, -inf)
# ---------------------------------------------------------------------------


def test_nan_last_price_rejects():
    """NaN last_price (Decimal) must be caught and rejected."""
    d = _ENGINE.gate(
        signal=_signal(),
        account=_account(),
        risk=_RISK,
        last_price=_NAN,
    )
    assert d.outcome == "reject"
    assert d.reason == RejectReason.INVALID_INPUT


def test_float_nan_last_price_rejects():
    """float('nan') last_price must be caught and rejected."""
    d = _ENGINE.gate(
        signal=_signal(),
        account=_account(),
        risk=_RISK,
        last_price=Decimal(str(float("nan"))),
    )
    assert d.outcome == "reject"
    assert d.reason == RejectReason.INVALID_INPUT


def test_inf_last_price_rejects():
    """float('inf') last_price must be caught and rejected."""
    d = _ENGINE.gate(
        signal=_signal(),
        account=_account(),
        risk=_RISK,
        last_price=Decimal(str(float("inf"))),
    )
    assert d.outcome == "reject"
    assert d.reason == RejectReason.INVALID_INPUT


def test_nan_current_equity_inr_rejects():
    """NaN current_equity_inr (via model_construct) must reject."""
    d = _ENGINE.gate(
        signal=_signal(),
        account=_account_nan(current_equity_inr=_NAN),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert d.outcome == "reject"
    assert d.reason == RejectReason.INVALID_INPUT


def test_nan_daily_realised_pnl_rejects():
    """NaN daily_realised_pnl_inr (via model_construct) must reject."""
    d = _ENGINE.gate(
        signal=_signal(),
        account=_account_nan(daily_realised_pnl_inr=_NAN),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert d.outcome == "reject"
    assert d.reason == RejectReason.INVALID_INPUT


def test_nan_daily_unrealised_pnl_rejects():
    """NaN daily_unrealised_pnl_inr (via model_construct) must reject."""
    d = _ENGINE.gate(
        signal=_signal(),
        account=_account_nan(daily_unrealised_pnl_inr=_NAN),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert d.outcome == "reject"
    assert d.reason == RejectReason.INVALID_INPUT


# ---------------------------------------------------------------------------
# Happy-path control
# ---------------------------------------------------------------------------


def test_clean_inputs_pass():
    """All-clean inputs within caps must be accepted."""
    d = _ENGINE.gate(
        signal=_signal(qty=10),
        account=_account(),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert d.outcome == "accept"


# ---------------------------------------------------------------------------
# >= boundary test for concentration cap
# ---------------------------------------------------------------------------


def test_concentration_rejects_at_exact_cap():
    """Concentration exactly equal to max_concentration_pct (25%)
    must be rejected (>= boundary, not strict >).

    Setup: 250 shares at 100 = 25 000 notional,
    equity = 100 000 → exactly 25% concentration.
    """
    d = _ENGINE.gate(
        signal=_signal(ticker="X", qty=250),
        account=_account(open_positions={}),
        risk={
            **_RISK,
            "per_trade": {"max_qty": 1000},
        },
        last_price=Decimal("100"),
    )
    assert d.outcome == "reject"
    assert d.reason == RejectReason.POSITION_CAP
