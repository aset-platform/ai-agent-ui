"""Smart Funnel Stage 1 — DuckDB pre-filter + composite scoring.

Scores the full ticker universe using a 6-factor composite
signal from Iceberg tables:

1. Fundamental  (Piotroski F-Score)
2. Risk-adjusted (Sharpe ratio)
3. Momentum     (annualised return %)
4. Forecast     (accuracy-adjusted 3m target)
5. Sentiment    (LLM sentiment score)
6. Technical    (bullish signal count)

Hard gates eliminate low-quality candidates before
expensive LLM reasoning in Stage 3.
"""

import logging
import time as _time

import pandas as pd

_logger = logging.getLogger(__name__)

# ── Composite score weights (sum = 1.0) ───────────

W_PIOTROSKI = 0.25
W_SHARPE = 0.20
W_MOMENTUM = 0.15
W_FORECAST = 0.20
W_SENTIMENT = 0.10
W_TECHNICAL = 0.10

# ── Normalisation clamp ranges ─────────────────────

SHARPE_MIN, SHARPE_MAX = -2.0, 4.0
RETURN_MIN, RETURN_MAX = -50.0, 100.0
FORECAST_MIN, FORECAST_MAX = -30.0, 50.0

# ── Cache (1 h TTL, same pattern as _MARKET_CACHE) ───

_PREFILTER_CACHE: dict[
    str, tuple[pd.DataFrame, float]
] = {}
_PREFILTER_TTL = 3600  # seconds


# ── Helpers ────────────────────────────────────────


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _norm(
    value: float, lo: float, hi: float,
) -> float:
    """Normalise *value* from [lo, hi] to 0-100."""
    clamped = _clamp(value, lo, hi)
    if hi == lo:
        return 0.0
    return (clamped - lo) / (hi - lo) * 100.0


def _compute_accuracy_factor(
    mape: float | None,
    mae: float | None,
    rmse: float | None,
    current_price: float | None,
) -> float:
    """Composite forecast accuracy factor (0-1).

    Blends MAPE (50 %), MAE-relative (30 %),
    RMSE-relative (20 %).  Falls back to MAPE-only
    when *current_price* is zero or missing.
    """
    mape_f = max(0.0, 1.0 - (mape or 0.0) / 100.0)
    if current_price and current_price > 0:
        mae_f = max(
            0.0, 1.0 - (mae or 0.0) / current_price,
        )
        rmse_f = max(
            0.0, 1.0 - (rmse or 0.0) / current_price,
        )
    else:
        mae_f = rmse_f = mape_f  # fallback
    return 0.5 * mape_f + 0.3 * mae_f + 0.2 * rmse_f


def _compute_composite_score(row: dict) -> float:
    """6-factor composite score (0-100).

    Parameters
    ----------
    row : dict
        Must contain: piotroski, sharpe_ratio,
        annualized_return_pct, target_3m_pct_change,
        mape, mae, rmse, current_price, sentiment,
        sma_50_signal, sma_200_signal, rsi_signal,
        macd_signal_text.
    """
    # 1. Fundamental — Piotroski / 9 * 100
    piotroski = row.get("piotroski") or 0
    fundamental = (piotroski / 9.0) * 100.0

    # 2. Risk-adjusted — normalised Sharpe
    sharpe = row.get("sharpe_ratio") or 0.0
    risk_adj = _norm(sharpe, SHARPE_MIN, SHARPE_MAX)

    # 3. Momentum — annualised return %
    ann_ret = row.get("annualized_return_pct") or 0.0
    momentum = _norm(ann_ret, RETURN_MIN, RETURN_MAX)

    # 4. Forecast — accuracy-adjusted 3 m target
    target_pct = row.get("target_3m_pct_change") or 0.0
    accuracy = _compute_accuracy_factor(
        row.get("mape"),
        row.get("mae"),
        row.get("rmse"),
        row.get("current_price"),
    )
    forecast = _norm(
        target_pct * accuracy,
        FORECAST_MIN,
        FORECAST_MAX,
    )

    # 5. Sentiment — map [-1, +1] → [0, 100]
    sentiment = row.get("sentiment") or 0.0
    sent_score = (sentiment + 1.0) / 2.0 * 100.0

    # 6. Technical — count bullish signals / 4
    bullish = 0
    if (row.get("sma_50_signal") or "").lower() == "buy":
        bullish += 1
    if (row.get("sma_200_signal") or "").lower() == "buy":
        bullish += 1
    if (row.get("rsi_signal") or "").lower() == "buy":
        bullish += 1
    macd_txt = (
        row.get("macd_signal_text") or ""
    ).lower()
    if macd_txt in ("bullish", "buy"):
        bullish += 1
    tech_score = (bullish / 4.0) * 100.0

    composite = (
        W_PIOTROSKI * fundamental
        + W_SHARPE * risk_adj
        + W_MOMENTUM * momentum
        + W_FORECAST * forecast
        + W_SENTIMENT * sent_score
        + W_TECHNICAL * tech_score
    )
    return round(composite, 2)


# ── Stage 1 query ─────────────────────────────────


_STAGE1_SQL = """\
WITH latest_piotroski AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY ticker ORDER BY score_date DESC
    ) AS rn FROM piotroski_scores
),
latest_analysis AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY ticker ORDER BY date DESC
    ) AS rn FROM analysis_summary
),
latest_sentiment AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY ticker ORDER BY score_date DESC
    ) AS rn FROM sentiment_scores
),
latest_forecast AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY ticker ORDER BY run_date DESC
    ) AS rn FROM forecast_runs
),
latest_ohlcv AS (
    SELECT ticker,
           "Close" AS current_price,
           "Volume" AS volume,
           ROW_NUMBER() OVER (
               PARTITION BY ticker ORDER BY date DESC
           ) AS rn
    FROM ohlcv
)
SELECT
    p.ticker,
    p.piotroski_score   AS piotroski,
    a.sharpe_ratio,
    a.annualized_return_pct,
    a.sma_50_signal,
    a.sma_200_signal,
    a.rsi_signal,
    a.macd_signal_text,
    f.target_3m_pct_change,
    f.mape,
    f.mae,
    f.rmse,
    s.avg_score          AS sentiment,
    o.current_price,
    o.volume             AS avg_volume
FROM latest_piotroski p
JOIN latest_analysis  a ON a.ticker = p.ticker AND a.rn = 1
JOIN latest_sentiment s ON s.ticker = p.ticker AND s.rn = 1
JOIN latest_forecast  f ON f.ticker = p.ticker AND f.rn = 1
JOIN latest_ohlcv     o ON o.ticker = p.ticker AND o.rn = 1
WHERE p.rn = 1
  AND p.piotroski_score >= 4
  AND o.volume >= 10000
  AND f.run_date >= CURRENT_DATE - INTERVAL '30' DAY
  AND s.score_date >= CURRENT_DATE - INTERVAL '7' DAY
  AND COALESCE(f.mape, 0) < 80
"""


def stage1_prefilter(
    duckdb_engine=None,
) -> pd.DataFrame:
    """Run Stage 1 DuckDB pre-filter over the universe.

    Returns a DataFrame with one row per ticker that
    passes the hard gates, plus a ``composite_score``
    column (0-100).

    Results are cached for 1 h.

    Parameters
    ----------
    duckdb_engine :
        Optional override for testing.  When *None*,
        uses :func:`backend.db.duckdb_engine`.
    """
    # ── Check cache ───────────────────────────────
    cached = _PREFILTER_CACHE.get("stage1")
    if cached:
        df, ts = cached
        if _time.time() - ts < _PREFILTER_TTL:
            _logger.info(
                "stage1_prefilter: cache hit (%d rows)",
                len(df),
            )
            return df

    # ── Build DuckDB connection with 5 views ──────
    from db.duckdb_engine import (
        _create_view,
        get_connection,
    )

    conn = (
        duckdb_engine
        if duckdb_engine is not None
        else get_connection()
    )

    tables = [
        "stocks.piotroski_scores",
        "stocks.analysis_summary",
        "stocks.sentiment_scores",
        "stocks.forecast_runs",
        "stocks.ohlcv",
    ]

    try:
        if duckdb_engine is None:
            for tbl in tables:
                _create_view(conn, tbl)

        result = conn.execute(_STAGE1_SQL)
        try:
            df = result.fetchdf()
        except Exception:
            columns = [
                desc[0] for desc in result.description
            ]
            rows = result.fetchall()
            df = pd.DataFrame(rows, columns=columns)
    finally:
        if duckdb_engine is None:
            conn.close()

    _logger.info(
        "stage1_prefilter: %d candidates from DuckDB",
        len(df),
    )

    if df.empty:
        df["composite_score"] = pd.Series(dtype=float)
        _PREFILTER_CACHE["stage1"] = (
            df,
            _time.time(),
        )
        return df

    # ── Score each row ────────────────────────────
    df["composite_score"] = df.apply(
        lambda r: _compute_composite_score(r.to_dict()),
        axis=1,
    )
    df = df.sort_values(
        "composite_score", ascending=False,
    ).reset_index(drop=True)

    _PREFILTER_CACHE["stage1"] = (df, _time.time())
    _logger.info(
        "stage1_prefilter: top score %.1f, "
        "bottom score %.1f",
        df["composite_score"].iloc[0],
        df["composite_score"].iloc[-1],
    )
    return df
