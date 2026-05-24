"""HTTP routers for the algo trading module."""

from backend.algo.routes.attribution import (
    create_attribution_router,
)
from backend.algo.routes.backtest import create_backtest_router
from backend.algo.routes.broker import create_broker_router
from backend.algo.routes.drift import create_drift_router
from backend.algo.routes.daily_factor_coverage import (
    create_daily_factor_coverage_router,
)
from backend.algo.routes.factors import create_factors_router
from backend.algo.routes.feature_coverage import (
    create_feature_coverage_router,
)
from backend.algo.routes.feature_importance import (
    create_feature_importance_router,
)
from backend.algo.routes.fees import create_fees_router
from backend.algo.routes.instruments import (
    create_instruments_router,
)
from backend.algo.routes.kill_switch import (
    create_kill_switch_router,
)
from backend.algo.routes.live import create_live_router
from backend.algo.routes.ltp import create_ltp_router
from backend.algo.routes.paper import create_paper_router
from backend.algo.routes.performance import (
    create_performance_router,
)
from backend.algo.routes.regime import create_regime_router
from backend.algo.routes.replay import create_replay_router
from backend.algo.routes.shap_analysis import (
    create_shap_analysis_router,
)
from backend.algo.routes.strategies import (
    create_strategies_router,
)
from backend.algo.routes.sweep import (
    create_sweep_router,
)
from backend.algo.routes.universe_snapshot import (
    create_universe_snapshot_router,
)
from backend.algo.routes.walkforward import (
    create_walkforward_router,
)
from backend.algo.routes.webhooks import (
    create_webhooks_router,
)

__all__ = [
    "create_attribution_router",
    "create_backtest_router",
    "create_broker_router",
    "create_drift_router",
    "create_daily_factor_coverage_router",
    "create_factors_router",
    "create_feature_coverage_router",
    "create_feature_importance_router",
    "create_fees_router",
    "create_instruments_router",
    "create_kill_switch_router",
    "create_live_router",
    "create_ltp_router",
    "create_paper_router",
    "create_performance_router",
    "create_regime_router",
    "create_replay_router",
    "create_shap_analysis_router",
    "create_strategies_router",
    "create_sweep_router",
    "create_universe_snapshot_router",
    "create_walkforward_router",
    "create_webhooks_router",
]
