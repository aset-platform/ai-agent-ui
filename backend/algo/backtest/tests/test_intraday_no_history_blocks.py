from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.coverage import TickerCoverage
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest
from backend.algo.strategy.ast import parse_strategy
from backend.algo.backtest.tests.test_trailing_stop_integration import (
    _v5_strategy,
)

_BASE = date(2026, 1, 1)


def test_1m_strategy_blocks_when_no_1m_history():
    strategy = parse_strategy(_v5_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id, period_start=_BASE,
        period_end=_BASE + timedelta(days=5), interval_sec=60,
    )
    # only 15m exists for the ticker
    cov = {"FAKE.NS": TickerCoverage(
        "FAKE.NS", 900, _BASE, _BASE + timedelta(days=5), 5)}
    with patch(
        "backend.algo.backtest.runner.intraday_coverage", return_value=cov,
    ), patch("backend.algo.backtest.runner.flush_events"):
        with pytest.raises(ValueError, match="No 1m history"):
            run_backtest(
                strategy=strategy, request=req,
                user_id=uuid4(), universe=["FAKE.NS"],
            )
