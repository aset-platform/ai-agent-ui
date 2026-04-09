"""One-time Iceberg table initialisation for the stocks namespace.

Creates all 15 Iceberg tables in the ``stocks`` namespace using the
shared SQLite catalog (``data/iceberg/catalog.db``).  The script is
idempotent — if tables already exist it exits without error.

Usage::

    cd ai-agent-ui
    source ~/.ai-agent-ui/venv/bin/activate
    python stocks/create_tables.py

The catalog is configured via ``.pyiceberg.yaml`` in the project root.
Both ``data/iceberg/catalog.db`` and ``data/iceberg/warehouse/`` are
created automatically if they do not already exist.

Tables created
--------------
- ``stocks.company_info``
- ``stocks.ohlcv``
- ``stocks.dividends``
- ``stocks.technical_indicators``
- ``stocks.analysis_summary``
- ``stocks.forecast_runs``
- ``stocks.forecasts``
- ``stocks.quarterly_results``
- ``stocks.llm_pricing``
- ``stocks.llm_usage``
- ``stocks.sentiment_scores``
- ``stocks.portfolio_transactions``
- ``stocks.query_log``
- ``stocks.data_gaps``
- ``stocks.piotroski_scores``

Migrated to PostgreSQL (no longer created here)
------------------------------------------------
- ``stocks.registry``
- ``stocks.scheduled_jobs``

Still on Iceberg (append-only, not migrated)
--------------------------------------------
- ``stocks.scheduler_runs``
"""

import logging
import os
import sys

# Ensure backend/ is on sys.path so paths module can be imported
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_SCRIPT_DIR, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from paths import ICEBERG_CATALOG_URI, ICEBERG_WAREHOUSE_URI  # noqa: E402

os.environ.setdefault("PYICEBERG_CATALOG__LOCAL__URI", ICEBERG_CATALOG_URI)
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__WAREHOUSE", ICEBERG_WAREHOUSE_URI
)

from pyiceberg.catalog.sql import SqlCatalog  # noqa: E402
from pyiceberg.partitioning import PartitionField, PartitionSpec  # noqa: E402
from pyiceberg.schema import Schema  # noqa: E402
from pyiceberg.transforms import IdentityTransform  # noqa: E402
from pyiceberg.types import (  # noqa: E402
    BooleanType,
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestampType,
)

_logger = logging.getLogger(__name__)

_NAMESPACE = "stocks"

# Table identifiers
_COMPANY_INFO_TABLE = f"{_NAMESPACE}.company_info"
_OHLCV_TABLE = f"{_NAMESPACE}.ohlcv"
_DIVIDENDS_TABLE = f"{_NAMESPACE}.dividends"
_TECHNICAL_INDICATORS_TABLE = f"{_NAMESPACE}.technical_indicators"
_ANALYSIS_SUMMARY_TABLE = f"{_NAMESPACE}.analysis_summary"
_FORECAST_RUNS_TABLE = f"{_NAMESPACE}.forecast_runs"
_FORECASTS_TABLE = f"{_NAMESPACE}.forecasts"
_QUARTERLY_RESULTS_TABLE = f"{_NAMESPACE}.quarterly_results"
_LLM_PRICING_TABLE = f"{_NAMESPACE}.llm_pricing"
_LLM_USAGE_TABLE = f"{_NAMESPACE}.llm_usage"
_SENTIMENT_SCORES_TABLE = f"{_NAMESPACE}.sentiment_scores"
_QUERY_LOG_TABLE = f"{_NAMESPACE}.query_log"
_DATA_GAPS_TABLE = f"{_NAMESPACE}.data_gaps"
_PIOTROSKI_SCORES_TABLE = f"{_NAMESPACE}.piotroski_scores"


def _get_catalog() -> SqlCatalog:
    """Load the local Iceberg SqlCatalog from ``.pyiceberg.yaml``.

    Returns:
        SqlCatalog: A configured Iceberg catalog instance.

    Raises:
        RuntimeError: If the catalog cannot be loaded.
    """
    from pyiceberg.catalog import load_catalog

    try:
        return load_catalog("local")
    except Exception as exc:
        raise RuntimeError(
            "Failed to load Iceberg catalog. "
            "Check that .pyiceberg.yaml exists in the project root."
        ) from exc


def _company_info_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.company_info``.

    Returns:
        Schema: Append-only company metadata snapshots;
            query latest by fetched_at DESC.
    """
    return Schema(
        NestedField(
            field_id=1, name="info_id", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=2, name="ticker", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=3,
            name="company_name",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=4, name="sector", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=5,
            name="industry",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="market_cap",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="pe_ratio",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="week_52_high",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="week_52_low",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="current_price",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="currency",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="fetched_at",
            field_type=TimestampType(),
            required=False,
        ),
        # Extended fundamentals (Phase 3 extension)
        NestedField(
            field_id=13,
            name="exchange",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=14,
            name="country",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="employees",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=16,
            name="dividend_yield",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=17, name="beta", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=18,
            name="book_value",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=19,
            name="price_to_book",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=20,
            name="earnings_growth",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=21,
            name="revenue_growth",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=22,
            name="profit_margins",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=23,
            name="avg_volume",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=24,
            name="float_shares",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=25,
            name="short_ratio",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=26,
            name="analyst_target",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=27,
            name="recommendation",
            field_type=DoubleType(),
            required=False,
        ),
    )


def _ohlcv_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.ohlcv``.

    Returns:
        Schema: OHLCV price history; composite key
            (ticker, date); partitioned by ticker.
    """
    return Schema(
        NestedField(
            field_id=1, name="ticker", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=2, name="date", field_type=DateType(), required=False
        ),
        NestedField(
            field_id=3, name="open", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=4, name="high", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=5, name="low", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=6, name="close", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=7,
            name="adj_close",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=8, name="volume", field_type=LongType(), required=False
        ),
        NestedField(
            field_id=9,
            name="fetched_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _dividends_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.dividends``.

    Returns:
        Schema: Dividend payments; composite key (ticker, ex_date).
    """
    return Schema(
        NestedField(
            field_id=1, name="ticker", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=2, name="ex_date", field_type=DateType(), required=False
        ),
        NestedField(
            field_id=3,
            name="dividend_amount",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="currency",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="fetched_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _technical_indicators_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.technical_indicators``.

    Returns:
        Schema: Computed technical indicators; 1:1
            with ohlcv (ticker, date);
            partitioned by ticker.
    """
    return Schema(
        NestedField(
            field_id=1, name="ticker", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=2, name="date", field_type=DateType(), required=False
        ),
        NestedField(
            field_id=3, name="sma_50", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=4, name="sma_200", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=5, name="ema_20", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=6, name="rsi_14", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=7, name="macd", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=8,
            name="macd_signal",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="macd_hist",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="bb_upper",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="bb_middle",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="bb_lower",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=13, name="atr_14", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=14,
            name="daily_return",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="computed_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _analysis_summary_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.analysis_summary``.

    Returns:
        Schema: Daily analysis snapshots; structured
            replacement for flat text cache files.
    """
    return Schema(
        NestedField(
            field_id=1,
            name="summary_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2, name="ticker", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=3,
            name="analysis_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="bull_phase_pct",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="bear_phase_pct",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="max_drawdown_pct",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="max_drawdown_duration_days",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="annualized_volatility_pct",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="annualized_return_pct",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="sharpe_ratio",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="all_time_high",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="all_time_high_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=13,
            name="all_time_low",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=14,
            name="all_time_low_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="support_levels",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=16,
            name="resistance_levels",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=17,
            name="sma_50_signal",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=18,
            name="sma_200_signal",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=19,
            name="rsi_signal",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=20,
            name="macd_signal_text",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=21,
            name="best_month",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=22,
            name="worst_month",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=23,
            name="best_year",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=24,
            name="worst_year",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=25,
            name="computed_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _forecast_runs_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.forecast_runs``.

    Returns:
        Schema: One row per Prophet run; stores
            targets at 3/6/9 months plus accuracy
            metrics.
    """
    return Schema(
        NestedField(
            field_id=1, name="run_id", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=2, name="ticker", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=3,
            name="horizon_months",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=4, name="run_date", field_type=DateType(), required=False
        ),
        NestedField(
            field_id=5,
            name="sentiment",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="current_price_at_run",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="target_3m_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="target_3m_price",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="target_3m_pct_change",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="target_3m_lower",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="target_3m_upper",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="target_6m_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=13,
            name="target_6m_price",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=14,
            name="target_6m_pct_change",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="target_6m_lower",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=16,
            name="target_6m_upper",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=17,
            name="target_9m_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=18,
            name="target_9m_price",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=19,
            name="target_9m_pct_change",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=20,
            name="target_9m_lower",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=21,
            name="target_9m_upper",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=22, name="mae", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=23, name="rmse", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=24, name="mape", field_type=DoubleType(), required=False
        ),
        NestedField(
            field_id=25,
            name="computed_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _forecasts_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.forecasts``.

    Returns:
        Schema: Full Prophet output series;
            partitioned by (ticker, horizon_months).
    """
    return Schema(
        NestedField(
            field_id=1, name="ticker", field_type=StringType(), required=False
        ),
        NestedField(
            field_id=2,
            name="horizon_months",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=3, name="run_date", field_type=DateType(), required=False
        ),
        NestedField(
            field_id=4,
            name="forecast_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="predicted_price",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="lower_bound",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="upper_bound",
            field_type=DoubleType(),
            required=False,
        ),
    )


def _quarterly_results_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.quarterly_results``.

    Returns:
        Schema: One row per (ticker, quarter_end, statement_type).
    """
    return Schema(
        NestedField(
            field_id=1,
            name="ticker",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="quarter_end",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="fiscal_year",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="fiscal_quarter",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="statement_type",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="revenue",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="net_income",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="gross_profit",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="operating_income",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="ebitda",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="eps_basic",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="eps_diluted",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=13,
            name="total_assets",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=14,
            name="total_liabilities",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="total_equity",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=16,
            name="total_debt",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=17,
            name="cash_and_equivalents",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=18,
            name="operating_cashflow",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=19,
            name="capex",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=20,
            name="free_cashflow",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=22,
            name="current_assets",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=23,
            name="current_liabilities",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=24,
            name="shares_outstanding",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=21,
            name="updated_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _piotroski_scores_schema() -> Schema:
    """Return Iceberg schema for ``stocks.piotroski_scores``.

    Returns:
        Schema: One row per (ticker, score_date).
    """
    return Schema(
        NestedField(
            field_id=1,
            name="score_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="ticker",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="score_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="total_score",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="label",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="roa_positive",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="operating_cf_positive",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="roa_increasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="cf_gt_net_income",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="leverage_decreasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="current_ratio_increasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="no_dilution",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=13,
            name="gross_margin_increasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=14,
            name="asset_turnover_increasing",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="market_cap",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=16,
            name="revenue",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=17,
            name="avg_volume",
            field_type=LongType(),
            required=False,
        ),
        NestedField(
            field_id=18,
            name="sector",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=19,
            name="industry",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=20,
            name="company_name",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=21,
            name="computed_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _llm_pricing_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.llm_pricing``.

    Returns:
        Schema: LLM model pricing rate card with
            effective date ranges for historical
            billing accuracy.
    """
    return Schema(
        NestedField(
            field_id=1,
            name="pricing_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="provider",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="model",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="input_cost_per_1m",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="output_cost_per_1m",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="effective_from",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="effective_to",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="currency",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="updated_by",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="created_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _llm_usage_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.llm_usage``.

    Returns:
        Schema: Per-request LLM event log with
            snapshotted pricing for audit-proof
            billing; partitioned by request_date.
    """
    return Schema(
        NestedField(
            field_id=1,
            name="usage_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="request_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="timestamp",
            field_type=TimestampType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="user_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="agent_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="model",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="provider",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="tier_index",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="event_type",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="cascade_reason",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="cascade_from_model",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="prompt_tokens",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=13,
            name="completion_tokens",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=14,
            name="total_tokens",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="input_cost_per_1m",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=16,
            name="output_cost_per_1m",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=17,
            name="estimated_cost_usd",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=18,
            name="latency_ms",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=19,
            name="success",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=20,
            name="error_code",
            field_type=StringType(),
            required=False,
        ),
    )


def _provider_partition_spec(schema: Schema) -> PartitionSpec:
    """Return a partition spec by ``provider``.

    Args:
        schema: Schema containing a ``provider`` field.

    Returns:
        PartitionSpec: Identity partition on provider.
    """
    provider_fid = schema.find_field("provider").field_id
    return PartitionSpec(
        PartitionField(
            source_id=provider_fid,
            field_id=1000,
            transform=IdentityTransform(),
            name="provider",
        )
    )


def _request_date_partition_spec(
    schema: Schema,
) -> PartitionSpec:
    """Return a partition spec by ``request_date``.

    Args:
        schema: Schema containing a ``request_date``
            field.

    Returns:
        PartitionSpec: Identity partition on
            request_date.
    """
    date_fid = schema.find_field("request_date").field_id
    return PartitionSpec(
        PartitionField(
            source_id=date_fid,
            field_id=1000,
            transform=IdentityTransform(),
            name="request_date",
        )
    )


def _ticker_partition_spec(schema: Schema) -> PartitionSpec:
    """Return a partition spec that partitions by the ``ticker`` field.

    Args:
        schema: The Iceberg schema containing a ``ticker`` field.

    Returns:
        PartitionSpec: Identity partition on ``ticker``.
    """
    ticker_field_id = schema.find_field("ticker").field_id
    return PartitionSpec(
        PartitionField(
            source_id=ticker_field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name="ticker",
        )
    )


def _ticker_horizon_partition_spec(schema: Schema) -> PartitionSpec:
    """Return a partition spec by ``ticker`` and
    ``horizon_months``.

    Args:
        schema: The Iceberg schema containing
            ``ticker`` and ``horizon_months`` fields.

    Returns:
        PartitionSpec: Identity partition on ticker then horizon_months.
    """
    ticker_fid = schema.find_field("ticker").field_id
    horizon_fid = schema.find_field("horizon_months").field_id
    return PartitionSpec(
        PartitionField(
            source_id=ticker_fid,
            field_id=1000,
            transform=IdentityTransform(),
            name="ticker",
        ),
        PartitionField(
            source_id=horizon_fid,
            field_id=1001,
            transform=IdentityTransform(),
            name="horizon_months",
        ),
    )


def _portfolio_transactions_schema() -> Schema:
    """Return the schema for ``stocks.portfolio_transactions``.

    Append-only transaction ledger for user portfolio
    holdings. Supports BUY now; SELL, DIVIDEND, SPLIT
    planned for future phases.
    """
    return Schema(
        NestedField(
            field_id=1,
            name="transaction_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="user_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="ticker",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="side",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="quantity",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="price",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="currency",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="market",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="trade_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="fees",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="notes",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=12,
            name="created_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _sentiment_scores_schema() -> Schema:
    """Schema for ``stocks.sentiment_scores``.

    Daily sentiment per stock ticker, scored by LLM
    or FinBERT.  Range: -1.0 (bearish) to +1.0 (bullish).
    """
    return Schema(
        NestedField(
            field_id=1,
            name="ticker",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="score_date",
            field_type=DateType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="avg_score",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="headline_count",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="source",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="scored_at",
            field_type=TimestampType(),
            required=False,
        ),
    )


def _query_log_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.query_log``.

    Returns:
        Schema: Per-query metadata for analytics and
            data-gap detection.
    """
    return Schema(
        NestedField(
            field_id=1,
            name="id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="timestamp",
            field_type=TimestampType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="user_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="query_text",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="classified_intent",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="sub_agent_invoked",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="tools_used",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=8,
            name="data_sources_used",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=9,
            name="was_local_sufficient",
            field_type=BooleanType(),
            required=False,
        ),
        NestedField(
            field_id=10,
            name="response_time_ms",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="gap_tickers",
            field_type=StringType(),
            required=False,
        ),
    )


def _data_gaps_schema() -> Schema:
    """Return the Iceberg schema for ``stocks.data_gaps``.

    Returns:
        Schema: Tracks tickers with stale or missing
            data detected during query processing.
    """
    return Schema(
        NestedField(
            field_id=1,
            name="id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=2,
            name="detected_at",
            field_type=TimestampType(),
            required=False,
        ),
        NestedField(
            field_id=3,
            name="ticker",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=4,
            name="data_type",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=5,
            name="query_count",
            field_type=IntegerType(),
            required=False,
        ),
        NestedField(
            field_id=6,
            name="resolved_at",
            field_type=TimestampType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="resolution",
            field_type=StringType(),
            required=False,
        ),
    )


def _create_table(
    catalog: SqlCatalog,
    identifier: str,
    schema: Schema,
    partition_spec: PartitionSpec,
) -> None:
    """Create a single Iceberg table, skipping if it already exists.

    Args:
        catalog: The Iceberg catalog to use.
        identifier: Fully qualified table name (e.g. ``"stocks.ohlcv"``).
        schema: The Iceberg schema for the table.
        partition_spec: Partition specification for the table.
    """
    try:
        catalog.create_table(
            identifier=identifier,
            schema=schema,
            partition_spec=partition_spec,
        )
        _logger.info("Created Iceberg table '%s'.", identifier)
    except Exception:
        _logger.info("Table '%s' already exists — skipping.", identifier)


def create_tables() -> None:
    """Create all ``stocks`` Iceberg tables.

    This function is idempotent — calling it on an already-initialised
    catalog simply logs and returns.  Creates the ``stocks`` namespace
    first if it does not exist.

    Raises:
        RuntimeError: If the catalog cannot be loaded.

    Example:
        >>> create_tables()  # doctest: +SKIP
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    catalog = _get_catalog()

    # Create namespace
    try:
        catalog.create_namespace(_NAMESPACE)
        _logger.info("Created Iceberg namespace '%s'.", _NAMESPACE)
    except Exception:
        _logger.info("Namespace '%s' already exists — skipping.", _NAMESPACE)

    # No-partition tables
    empty_spec = PartitionSpec()

    _create_table(
        catalog, _COMPANY_INFO_TABLE, _company_info_schema(), empty_spec
    )
    _create_table(catalog, _DIVIDENDS_TABLE, _dividends_schema(), empty_spec)
    _create_table(
        catalog,
        _ANALYSIS_SUMMARY_TABLE,
        _analysis_summary_schema(),
        empty_spec,
    )
    _create_table(
        catalog, _FORECAST_RUNS_TABLE, _forecast_runs_schema(), empty_spec
    )
    _create_table(
        catalog,
        _QUARTERLY_RESULTS_TABLE,
        _quarterly_results_schema(),
        empty_spec,
    )
    _create_table(
        catalog,
        _PIOTROSKI_SCORES_TABLE,
        _piotroski_scores_schema(),
        empty_spec,
    )

    # Partitioned tables
    ohlcv_schema = _ohlcv_schema()
    _create_table(
        catalog,
        _OHLCV_TABLE,
        ohlcv_schema,
        _ticker_partition_spec(ohlcv_schema),
    )

    ti_schema = _technical_indicators_schema()
    _create_table(
        catalog,
        _TECHNICAL_INDICATORS_TABLE,
        ti_schema,
        _ticker_partition_spec(ti_schema),
    )

    forecasts_schema = _forecasts_schema()
    _create_table(
        catalog,
        _FORECASTS_TABLE,
        forecasts_schema,
        _ticker_horizon_partition_spec(forecasts_schema),
    )

    # LLM pricing (partitioned by provider)
    pricing_schema = _llm_pricing_schema()
    _create_table(
        catalog,
        _LLM_PRICING_TABLE,
        pricing_schema,
        _provider_partition_spec(pricing_schema),
    )

    # LLM usage (partitioned by request_date)
    usage_schema = _llm_usage_schema()
    _create_table(
        catalog,
        _LLM_USAGE_TABLE,
        usage_schema,
        _request_date_partition_spec(usage_schema),
    )

    # Portfolio transactions (no partition — small table)
    _create_table(
        catalog,
        f"{_NAMESPACE}.portfolio_transactions",
        _portfolio_transactions_schema(),
        empty_spec,
    )

    ss_schema = _sentiment_scores_schema()
    _create_table(
        catalog,
        _SENTIMENT_SCORES_TABLE,
        ss_schema,
        _ticker_partition_spec(ss_schema),
    )

    # Query log (no partition — moderate volume)
    _create_table(
        catalog,
        _QUERY_LOG_TABLE,
        _query_log_schema(),
        empty_spec,
    )

    # Data gaps (no partition — small table)
    _create_table(
        catalog,
        _DATA_GAPS_TABLE,
        _data_gaps_schema(),
        empty_spec,
    )

    _logger.info("Stocks Iceberg table initialisation complete.")


def evolve_quarterly_results_schema() -> None:
    """Add Piotroski fields to existing quarterly_results.

    Adds current_assets, current_liabilities,
    shares_outstanding (field_id 22-24). Idempotent --
    skips if columns already exist.
    """
    catalog = _get_catalog()
    tbl = catalog.load_table(_QUARTERLY_RESULTS_TABLE)
    existing = {f.name for f in tbl.schema().fields}
    new_fields = [
        NestedField(
            field_id=22,
            name="current_assets",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=23,
            name="current_liabilities",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=24,
            name="shares_outstanding",
            field_type=DoubleType(),
            required=False,
        ),
    ]
    to_add = [f for f in new_fields if f.name not in existing]
    if not to_add:
        _logger.info(
            "quarterly_results already has Piotroski "
            "columns — skipping evolution."
        )
        return
    with tbl.update_schema() as update:
        for field in to_add:
            update.add_column(
                path=field.name,
                field_type=field.field_type,
            )
    _logger.info(
        "Evolved quarterly_results schema: added %s",
        [f.name for f in to_add],
    )


if __name__ == "__main__":
    import os

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    create_tables()
