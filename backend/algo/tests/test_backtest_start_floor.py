"""2007-01-01 backtest start floor (REGIME-7).

Pydantic v2 validator on both BacktestRequest and
WalkForwardConfig — pre-2007 starts are rejected at parse time
because they exclude the 2008 bear-market regime needed for
survivorship + regime-stratification validation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.algo.backtest.types import BacktestRequest
from backend.algo.backtest.walkforward import WalkForwardConfig


def test_2007_accepted_in_backtest_request() -> None:
    req = BacktestRequest(
        strategy_id=uuid4(),
        period_start=date(2007, 1, 1),
        period_end=date(2008, 1, 1),
    )
    assert req.period_start == date(2007, 1, 1)


def test_2026_accepted_in_backtest_request() -> None:
    """Sanity: post-floor dates still work."""
    req = BacktestRequest(
        strategy_id=uuid4(),
        period_start=date(2026, 1, 1),
        period_end=date(2026, 6, 1),
    )
    assert req.period_start == date(2026, 1, 1)


def test_2006_rejected_in_backtest_request() -> None:
    with pytest.raises(ValidationError, match="2007-01-01"):
        BacktestRequest(
            strategy_id=uuid4(),
            period_start=date(2006, 12, 31),
            period_end=date(2008, 1, 1),
        )


def test_1970_rejected_in_backtest_request() -> None:
    with pytest.raises(ValidationError, match="2007-01-01"):
        BacktestRequest(
            strategy_id=uuid4(),
            period_start=date(1970, 1, 1),
            period_end=date(2008, 1, 1),
        )


def test_2007_accepted_in_walkforward_config() -> None:
    cfg = WalkForwardConfig(
        strategy_id=uuid4(),
        period_start=date(2007, 1, 1),
        period_end=date(2010, 1, 1),
        train_days=180,
        test_days=30,
        step_days=30,
    )
    assert cfg.period_start == date(2007, 1, 1)


def test_2006_rejected_in_walkforward_config() -> None:
    with pytest.raises(ValidationError, match="2007-01-01"):
        WalkForwardConfig(
            strategy_id=uuid4(),
            period_start=date(2006, 12, 31),
            period_end=date(2010, 1, 1),
            train_days=180,
            test_days=30,
            step_days=30,
        )


def test_initial_capital_still_works() -> None:
    """Sanity: existing field validators (initial_capital >=
    1000) are not broken by adding the new validator."""
    req = BacktestRequest(
        strategy_id=uuid4(),
        period_start=date(2026, 1, 1),
        period_end=date(2026, 6, 1),
        initial_capital_inr=Decimal("250000.00"),
    )
    assert req.initial_capital_inr == Decimal("250000.00")
