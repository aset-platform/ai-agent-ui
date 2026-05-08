"""HTTP routers for the algo trading module."""

from backend.algo.routes.backtest import create_backtest_router
from backend.algo.routes.broker import create_broker_router
from backend.algo.routes.fees import create_fees_router
from backend.algo.routes.instruments import create_instruments_router
from backend.algo.routes.strategies import create_strategies_router

__all__ = [
    "create_backtest_router",
    "create_broker_router",
    "create_fees_router",
    "create_instruments_router",
    "create_strategies_router",
]
