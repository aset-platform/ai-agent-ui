"""Feature dictionary registry — single source of truth for the
leaf vocabulary the strategy AST can reference. Mirrors are
generated for the frontend at
``frontend/components/algo-trading/strategyFeatureCatalog.ts``;
drift caught by ``test_feature_registry_sync.py``.

Each feature has a stable key, a UI label, a numeric type
(int / float), and a source identifier — used by the
backtest engine in Slice 7 to know which Iceberg/PG table
to pull the value from. Adding a feature is a 4-step PR:
1. Add to FEATURES below.
2. Add the matching entry to strategyFeatureCatalog.ts.
3. Implement the resolver in Slice 7's runtime.
4. Add a sample backtest case if the feature is non-obvious.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

FeatureType = Literal["int", "float"]
FeatureSource = Literal[
    "ohlcv",
    # rsi, sma_50, sma_200, golden_cross_days_ago
    "technical",
    "fundamentals",       # pscore, debt_to_eq, roce, sales/profit
    "recommendation",
    "forecast",
]


class Feature(BaseModel):
    """A single feature in the strategy vocabulary."""

    key: str
    label: str
    type: FeatureType
    source: FeatureSource


# Initial feature dictionary — equity, daily-bar features only.
# Slice 6 adds intraday-bar features. F&O features = v2.
FEATURES: list[Feature] = [
    # OHLCV
    Feature(
        key="today_ltp", label="Today LTP", type="float", source="ohlcv"
    ),
    Feature(
        key="prev_day_ltp",
        label="Prev day LTP",
        type="float",
        source="ohlcv",
    ),
    Feature(
        key="today_vol", label="Today volume", type="int", source="ohlcv"
    ),
    Feature(
        key="today_x_vol",
        label="Today × Vol (vs avg)",
        type="float",
        source="ohlcv",
    ),
    Feature(
        key="away_from_52week_high",
        label="Away from 52w high (%)",
        type="float",
        source="ohlcv",
    ),
    # Technical
    Feature(
        key="golden_cross_days_ago",
        label="Golden cross (days ago)",
        type="int",
        source="technical",
    ),
    Feature(key="sma_50", label="SMA 50", type="float", source="technical"),
    Feature(key="sma_200", label="SMA 200", type="float", source="technical"),
    Feature(key="rsi", label="RSI (14)", type="float", source="technical"),
    Feature(
        key="today_dpc",
        label="Today delivery %",
        type="float",
        source="technical",
    ),
    # Fundamentals
    Feature(
        key="pscore",
        label="P-Score (Piotroski)",
        type="int",
        source="fundamentals",
    ),
    Feature(
        key="debt_to_eq",
        label="Debt / Equity",
        type="float",
        source="fundamentals",
    ),
    Feature(key="roce", label="ROCE %", type="float", source="fundamentals"),
    Feature(
        key="sales_growth_3yrs",
        label="Sales growth 3y %",
        type="float",
        source="fundamentals",
    ),
    Feature(
        key="prft_growth_3yrs",
        label="Profit growth 3y %",
        type="float",
        source="fundamentals",
    ),
    # Recommendation
    Feature(
        key="recommendation_score",
        label="Recommendation score",
        type="float",
        source="recommendation",
    ),
    # Forecast
    Feature(
        key="forecast_30d_pct_change",
        label="Forecast 30d % change",
        type="float",
        source="forecast",
    ),
    Feature(
        key="forecast_confidence",
        label="Forecast confidence",
        type="float",
        source="forecast",
    ),
]


FEATURE_KEYS: frozenset[str] = frozenset(f.key for f in FEATURES)
FEATURE_BY_KEY: dict[str, Feature] = {f.key: f for f in FEATURES}
