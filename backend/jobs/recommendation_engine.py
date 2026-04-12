"""Smart Funnel — DuckDB pre-filter, gap analysis, LLM reasoning.

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

import json
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
        PARTITION BY ticker
        ORDER BY analysis_date DESC
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
           close   AS current_price,
           volume,
           ROW_NUMBER() OVER (
               PARTITION BY ticker ORDER BY date DESC
           ) AS rn
    FROM ohlcv
    WHERE close IS NOT NULL
)
SELECT
    p.ticker,
    p.total_score        AS piotroski,
    p.sector,
    p.industry,
    p.market_cap,
    p.avg_volume,
    p.company_name,
    a.sharpe_ratio,
    a.annualized_return_pct,
    a.sma_50_signal,
    a.sma_200_signal,
    a.rsi_signal,
    a.macd_signal_text,
    f.target_3m_pct_change,
    f.target_3m_price,
    f.mape,
    f.mae,
    f.rmse,
    f.run_date           AS forecast_run_date,
    s.avg_score          AS sentiment,
    s.headline_count,
    o.current_price,
    o.volume             AS ohlcv_volume
FROM latest_piotroski p
JOIN latest_analysis  a
    ON a.ticker = p.ticker AND a.rn = 1
JOIN latest_sentiment s
    ON s.ticker = p.ticker AND s.rn = 1
JOIN latest_forecast  f
    ON f.ticker = p.ticker AND f.rn = 1
JOIN latest_ohlcv     o
    ON o.ticker = p.ticker AND o.rn = 1
WHERE p.rn = 1
  AND p.total_score >= 4
  AND COALESCE(p.avg_volume, 0) >= 10000
  AND f.run_date >= CURRENT_DATE - INTERVAL '30' DAY
  AND s.score_date >= CURRENT_DATE - INTERVAL '7' DAY
  AND COALESCE(f.mape, 0) < 80
"""


def stage1_prefilter(
    duckdb_engine=None,
    scope: str = "all",
) -> pd.DataFrame:
    """Run Stage 1 DuckDB pre-filter over the universe.

    Returns a DataFrame with one row per ticker that
    passes the hard gates, plus a ``composite_score``
    column (0-100).

    Results are cached for 1 h (per scope).

    Parameters
    ----------
    duckdb_engine :
        Optional override for testing.  When *None*,
        uses :func:`backend.db.duckdb_engine`.
    scope :
        Market scope: ``"india"``, ``"us"``, or
        ``"all"`` (default).
    """
    # ── Check cache ───────────────────────────────
    cache_key = f"stage1:{scope}"
    cached = _PREFILTER_CACHE.get(cache_key)
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

    # ── Filter by market scope ────────────────────
    if scope != "all" and not df.empty:
        from market_utils import is_indian_market

        if scope == "india":
            df = df[
                df["ticker"].apply(is_indian_market)
            ]
        else:
            df = df[
                ~df["ticker"].apply(is_indian_market)
            ]
        df = df.reset_index(drop=True)

    _logger.info(
        "stage1_prefilter: %d candidates "
        "(scope=%s)",
        len(df),
        scope,
    )

    if df.empty:
        df["composite_score"] = pd.Series(dtype=float)
        _PREFILTER_CACHE[cache_key] = (
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

    _PREFILTER_CACHE[cache_key] = (df, _time.time())
    _logger.info(
        "stage1_prefilter: top score %.1f, "
        "bottom score %.1f",
        df["composite_score"].iloc[0],
        df["composite_score"].iloc[-1],
    )
    return df


# ─────────────────────────────────────────────────────
# Stage 2 — Per-user portfolio gap analysis
# ─────────────────────────────────────────────────────

CAP_BENCHMARK = {
    "largecap": 60,
    "midcap": 25,
    "smallcap": 15,
}
LARGE_CAP_FLOOR = 200_000_000_000  # 200 B INR
MID_CAP_FLOOR = 50_000_000_000     # 50 B INR

_CORRELATION_THRESHOLD = 0.85
_TOP_CANDIDATES = 40


def _classify_cap(market_cap: float | None) -> str:
    """Classify market cap into largecap/midcap/smallcap.

    Parameters
    ----------
    market_cap : float | None
        Market capitalisation in INR.

    Returns
    -------
    str
        ``"largecap"``, ``"midcap"``, or ``"smallcap"``.
    """
    if market_cap is None or market_cap <= 0:
        return "smallcap"
    if market_cap >= LARGE_CAP_FLOOR:
        return "largecap"
    if market_cap >= MID_CAP_FLOOR:
        return "midcap"
    return "smallcap"


def _compute_sector_gaps(
    user_sectors: dict[str, float],
    universe_sectors: dict[str, float],
) -> dict[str, float]:
    """Compute per-sector weight gap (user - universe).

    Positive = overweight, negative = underweight.
    Sectors present only in the universe appear as
    negative gaps; sectors only in user appear as positive.

    Parameters
    ----------
    user_sectors : dict[str, float]
        Sector -> weight % in user portfolio.
    universe_sectors : dict[str, float]
        Sector -> weight % in the candidate universe.

    Returns
    -------
    dict[str, float]
        Sector -> gap percentage.
    """
    all_sectors = set(user_sectors) | set(universe_sectors)
    gaps: dict[str, float] = {}
    for sector in all_sectors:
        user_w = user_sectors.get(sector, 0.0)
        univ_w = universe_sectors.get(sector, 0.0)
        gaps[sector] = round(user_w - univ_w, 2)
    return gaps


def _compute_gap_bonus(
    sector_gap_pct: float,
    index_gap: bool,
    cap_gap_pct: float,
) -> float:
    """Compute 0-20 bonus points for filling gaps.

    Parameters
    ----------
    sector_gap_pct : float
        User's sector gap for this candidate's sector.
        Negative means underweight (candidate fills gap).
    index_gap : bool
        True if candidate is in Nifty 50 but not in user
        portfolio.
    cap_gap_pct : float
        User's cap-category gap. Negative means underweight.

    Returns
    -------
    float
        Bonus score, capped at 20.
    """
    bonus = 0.0
    # Sector gap: underweight < -5 -> up to +10
    if sector_gap_pct < -5:
        bonus += min(10.0, abs(sector_gap_pct) * 0.5)
    # Index gap: missing Nifty 50 stock -> +5
    if index_gap:
        bonus += 5.0
    # Cap gap: underweight < -5 -> up to +5
    if cap_gap_pct < -5:
        bonus += min(5.0, abs(cap_gap_pct) * 0.3)
    return min(20.0, round(bonus, 2))


def _assign_tier(
    ticker: str,
    holdings_tickers: set[str],
    watchlist_tickers: set[str],
) -> str:
    """Assign recommendation tier for a ticker.

    Parameters
    ----------
    ticker : str
        The ticker symbol.
    holdings_tickers : set[str]
        Tickers currently in user portfolio.
    watchlist_tickers : set[str]
        Tickers on user's watchlist.

    Returns
    -------
    str
        ``"portfolio"``, ``"watchlist"``, or
        ``"discovery"``.
    """
    if ticker in holdings_tickers:
        return "portfolio"
    if ticker in watchlist_tickers:
        return "watchlist"
    return "discovery"


def _categorize_holding(
    composite_score: float,
    forecast_3m_pct: float,
    weight_pct: float,
    sentiment: float,
) -> str:
    """Categorize an existing holding for action.

    Parameters
    ----------
    composite_score : float
        Stage 1 composite score (0-100).
    forecast_3m_pct : float
        3-month forecast percentage change.
    weight_pct : float
        Current weight in portfolio (0-100).
    sentiment : float
        Sentiment score (-1 to +1).

    Returns
    -------
    str
        One of ``"exit_reduce"``, ``"risk_alert"``,
        ``"rebalance"``, or ``"hold_accumulate"``.
    """
    if composite_score < 30 and forecast_3m_pct < 0:
        return "exit_reduce"
    if composite_score < 40 and sentiment < -0.3:
        return "risk_alert"
    if weight_pct > 20:
        return "rebalance"
    return "hold_accumulate"


# ── PG helpers (deferred imports, safe fallback) ─────


def _async_nullpool_session():
    """Create a fresh async NullPool session factory.

    Safe in thread pool workers — each call creates a
    new engine + session, no loop conflicts.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool
    from config import get_settings

    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession,
    )
    return engine, factory


def _get_nifty50_tickers() -> set[str]:
    """Load Nifty 50 tickers from stock_tags PG table.

    Uses async NullPool + asyncio.run() (safe in
    thread pool workers). Returns empty set on failure.
    """
    try:
        import asyncio
        from sqlalchemy import select as sa_select

        from backend.db.models.stock_master import (
            StockMaster,
        )
        from backend.db.models.stock_tag import (
            StockTag,
        )

        async def _fetch() -> set[str]:
            eng, factory = _async_nullpool_session()
            async with factory() as s:
                stmt = (
                    sa_select(StockMaster.yf_ticker)
                    .join(
                        StockTag,
                        StockTag.stock_id
                        == StockMaster.id,
                    )
                    .where(StockTag.tag == "nifty50")
                    .where(
                        StockTag.removed_at.is_(None)
                    )
                )
                result = await s.execute(stmt)
                tickers = {
                    r[0] for r in result.all()
                }
            await eng.dispose()
            return tickers

        return asyncio.run(_fetch())
    except Exception:
        _logger.warning(
            "Failed to load nifty50 tickers from PG",
            exc_info=True,
        )
        return set()


def _get_user_watchlist(user_id: str) -> set[str]:
    """Load user's watchlist tickers from PG.

    Uses async NullPool + asyncio.run() (safe in
    thread pool workers). Returns empty set on failure.
    """
    try:
        import asyncio
        from sqlalchemy import select as sa_select

        from backend.db.models.user_ticker import (
            UserTicker,
        )

        async def _fetch() -> set[str]:
            eng, factory = _async_nullpool_session()
            async with factory() as s:
                stmt = (
                    sa_select(UserTicker.ticker)
                    .where(
                        UserTicker.user_id == user_id,
                    )
                )
                result = await s.execute(stmt)
                tickers = {
                    r[0] for r in result.all()
                }
            await eng.dispose()
            return tickers

        return asyncio.run(_fetch())
    except Exception:
        _logger.warning(
            "Failed to load watchlist for user %s",
            user_id,
            exc_info=True,
        )
        return set()


def _compute_correlation_alerts(
    holdings_tickers: list[str],
    repo=None,
) -> list[dict]:
    """Flag highly correlated holdings (>0.85).

    Uses 1Y daily close returns from OHLCV.

    Parameters
    ----------
    holdings_tickers : list[str]
        Tickers in the user's portfolio.
    repo :
        Optional StockRepository override.

    Returns
    -------
    list[dict]
        Each dict has ``ticker_a``, ``ticker_b``,
        ``correlation``.
    """
    if len(holdings_tickers) < 2:
        return []

    try:
        from datetime import date, timedelta

        if repo is None:
            from stocks.repository import (
                StockRepository,
            )

            repo = StockRepository()

        start = date.today() - timedelta(days=365)
        returns: dict[str, pd.Series] = {}
        for tkr in holdings_tickers:
            try:
                ohlcv = repo.get_ohlcv(tkr, start=start)
                if ohlcv is not None and len(ohlcv) > 20:
                    close = ohlcv.set_index("date")[
                        "close"
                    ].sort_index()
                    returns[tkr] = close.pct_change().dropna()
            except Exception:
                continue

        if len(returns) < 2:
            return []

        ret_df = pd.DataFrame(returns).dropna()
        if len(ret_df) < 20:
            return []

        corr = ret_df.corr()
        alerts: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for i, t1 in enumerate(corr.columns):
            for t2 in corr.columns[i + 1:]:
                pair = (t1, t2)
                if pair in seen:
                    continue
                seen.add(pair)
                val = corr.loc[t1, t2]
                if abs(val) > _CORRELATION_THRESHOLD:
                    alerts.append({
                        "ticker_a": t1,
                        "ticker_b": t2,
                        "correlation": round(val, 3),
                    })
        return alerts
    except Exception:
        _logger.warning(
            "Correlation analysis failed",
            exc_info=True,
        )
        return []


# ── Stage 2 main function ───────────────────────────


def stage2_gap_analysis(
    user_id: str,
    candidates_df: pd.DataFrame,
    repo=None,
    scope: str = "all",
) -> dict:
    """Per-user portfolio gap analysis (Stage 2).

    Enriches Stage 1 candidates with gap-fill bonuses,
    tiers, and portfolio action tags.

    Parameters
    ----------
    user_id : str
        UUID of the user.
    candidates_df : pd.DataFrame
        Stage 1 output with ``ticker``,
        ``composite_score``, ``sentiment``,
        ``target_3m_pct_change``, and optionally
        ``sector``, ``market_cap``.
    repo :
        Optional StockRepository override for testing.
    scope :
        Market scope: ``"india"``, ``"us"``, or
        ``"all"`` (default).

    Returns
    -------
    dict
        Keys: ``portfolio_summary``,
        ``portfolio_actions``, ``candidates``,
        ``gap_analysis``.
    """
    if repo is None:
        try:
            from stocks.repository import (
                StockRepository,
            )

            repo = StockRepository()
        except Exception:
            _logger.warning(
                "StockRepository unavailable; "
                "using empty holdings",
            )
            repo = None

    # 1. Load holdings
    holdings_df = pd.DataFrame()
    if repo is not None:
        try:
            holdings_df = repo.get_portfolio_holdings(
                user_id,
            )
        except Exception:
            _logger.warning(
                "get_portfolio_holdings failed "
                "for user %s",
                user_id,
                exc_info=True,
            )

    # Filter holdings by market scope
    if (
        scope != "all"
        and not holdings_df.empty
        and "market" in holdings_df.columns
    ):
        holdings_df = holdings_df[
            holdings_df["market"] == scope
        ].reset_index(drop=True)

    holdings_tickers: set[str] = set()
    if not holdings_df.empty:
        holdings_tickers = set(
            holdings_df["ticker"].tolist()
        )

    # 1b. Enrich holdings with sector, market_cap,
    # current_price from Stage 1 candidates.
    if (
        not holdings_df.empty
        and not candidates_df.empty
        and "sector" not in holdings_df.columns
    ):
        enrich_cols = [
            "ticker", "sector", "industry",
            "market_cap", "company_name",
            "current_price",
        ]
        avail = [
            c for c in enrich_cols
            if c in candidates_df.columns
        ]
        if "ticker" in avail:
            enrich = candidates_df[avail].drop_duplicates(
                subset=["ticker"],
            )
            holdings_df = holdings_df.merge(
                enrich, on="ticker", how="left",
            )
            # Fallback: fill remaining NaN sectors
            # from company_info for tickers that didn't
            # pass Stage 1 gates (e.g. low Piotroski).
            missing = holdings_df[
                holdings_df["sector"].isna()
            ]["ticker"].tolist()
            if missing:
                try:
                    from db.duckdb_engine import (
                        get_connection as _gc2,
                        _create_view as _cv2,
                    )

                    conn2 = _gc2()
                    _cv2(conn2, "stocks.company_info")
                    _cv2(conn2, "stocks.ohlcv")
                    placeholders = ", ".join(
                        f"'{t}'" for t in missing
                    )
                    ci = conn2.execute(
                        "SELECT ticker, sector, "
                        "industry, market_cap "
                        "FROM company_info "
                        "WHERE ticker IN "
                        f"({placeholders})"
                    ).fetchdf()
                    pr = conn2.execute(
                        "SELECT ticker, close "
                        "AS current_price FROM ("
                        "SELECT ticker, close, "
                        "ROW_NUMBER() OVER ("
                        "PARTITION BY ticker "
                        "ORDER BY date DESC) rn "
                        "FROM ohlcv WHERE close "
                        "IS NOT NULL AND ticker "
                        f"IN ({placeholders})"
                        ") WHERE rn = 1"
                    ).fetchdf()
                    conn2.close()
                    for _, ci_row in ci.iterrows():
                        t = ci_row["ticker"]
                        mask = (
                            holdings_df["ticker"] == t
                        )
                        holdings_df.loc[
                            mask, "sector"
                        ] = ci_row.get("sector")
                        holdings_df.loc[
                            mask, "market_cap"
                        ] = ci_row.get("market_cap")
                    for _, pr_row in pr.iterrows():
                        t = pr_row["ticker"]
                        mask = (
                            holdings_df["ticker"] == t
                        ) & holdings_df[
                            "current_price"
                        ].isna()
                        holdings_df.loc[
                            mask, "current_price"
                        ] = pr_row.get(
                            "current_price",
                        )
                except Exception:
                    _logger.debug(
                        "Fallback company_info "
                        "lookup failed",
                        exc_info=True,
                    )

            # Compute current_value if we got price
            if (
                "current_price" in holdings_df.columns
                and "quantity" in holdings_df.columns
            ):
                holdings_df["current_value"] = (
                    holdings_df["quantity"].astype(float)
                    * holdings_df["current_price"]
                    .fillna(0)
                    .astype(float)
                )

    # 2. Build sector weights from holdings
    user_sectors: dict[str, float] = {}
    cap_dist: dict[str, float] = {
        "largecap": 0.0,
        "midcap": 0.0,
        "smallcap": 0.0,
    }
    total_value = 0.0
    if (
        not holdings_df.empty
        and "sector" in holdings_df.columns
    ):
        if "current_value" in holdings_df.columns:
            val_col = "current_value"
        elif "invested" in holdings_df.columns:
            val_col = "invested"
        else:
            val_col = None

        if val_col:
            total_value = float(
                holdings_df[val_col].sum()
            )
            if total_value > 0:
                for _, row in holdings_df.iterrows():
                    sector = row.get("sector") or "Other"
                    val = float(row.get(val_col) or 0)
                    w = val / total_value * 100.0
                    user_sectors[sector] = (
                        user_sectors.get(sector, 0.0) + w
                    )
                    # Cap distribution
                    mcap = row.get("market_cap")
                    cap_cat = _classify_cap(mcap)
                    cap_dist[cap_cat] += w

    # 3. Universe sector distribution
    universe_sectors: dict[str, float] = {}
    if (
        not candidates_df.empty
        and "sector" in candidates_df.columns
    ):
        sec_counts = (
            candidates_df["sector"]
            .fillna("Other")
            .value_counts(normalize=True)
            * 100.0
        )
        universe_sectors = sec_counts.to_dict()

    # 4. Sector gaps
    sector_gaps = _compute_sector_gaps(
        user_sectors, universe_sectors,
    )

    # 5. Nifty 50 index gap
    nifty50 = _get_nifty50_tickers()
    missing_nifty = nifty50 - holdings_tickers

    # 6. Cap distribution gaps vs benchmark
    cap_gaps: dict[str, float] = {}
    for cat, bench in CAP_BENCHMARK.items():
        cap_gaps[cat] = round(
            cap_dist.get(cat, 0.0) - bench, 2,
        )

    # 7. User watchlist for tier assignment
    watchlist = _get_user_watchlist(user_id)

    # 8. Correlation analysis
    corr_alerts = _compute_correlation_alerts(
        list(holdings_tickers), repo=repo,
    )

    # 9. Score existing holdings
    portfolio_actions: list[dict] = []
    if not holdings_df.empty:
        score_map = {}
        if not candidates_df.empty:
            score_map = dict(
                zip(
                    candidates_df["ticker"],
                    candidates_df["composite_score"],
                )
            )
        for _, h in holdings_df.iterrows():
            tkr = h["ticker"]
            comp = score_map.get(tkr, 50.0)
            fcast = 0.0
            if (
                not candidates_df.empty
                and "target_3m_pct_change"
                in candidates_df.columns
            ):
                match = candidates_df.loc[
                    candidates_df["ticker"] == tkr,
                    "target_3m_pct_change",
                ]
                if not match.empty:
                    fcast = float(match.iloc[0])
            sent = 0.0
            if (
                not candidates_df.empty
                and "sentiment" in candidates_df.columns
            ):
                match = candidates_df.loc[
                    candidates_df["ticker"] == tkr,
                    "sentiment",
                ]
                if not match.empty:
                    sent = float(match.iloc[0])
            w_pct = 0.0
            if total_value > 0:
                val_col = (
                    "current_value"
                    if "current_value"
                    in holdings_df.columns
                    else "invested"
                )
                if val_col in holdings_df.columns:
                    w_pct = (
                        float(h.get(val_col) or 0)
                        / total_value
                        * 100.0
                    )
            category = _categorize_holding(
                comp, fcast, w_pct, sent,
            )
            portfolio_actions.append({
                "ticker": tkr,
                "composite_score": round(comp, 2),
                "forecast_3m_pct": round(fcast, 2),
                "weight_pct": round(w_pct, 2),
                "sentiment": round(sent, 2),
                "category": category,
            })

    # 10. Tag candidates with gap bonus, tier, fills_gaps
    enriched: list[dict] = []
    for _, c in candidates_df.iterrows():
        tkr = c["ticker"]
        comp = float(c.get("composite_score") or 0)
        sector = c.get("sector") or "Other"
        mcap = c.get("market_cap")
        cap_cat = _classify_cap(mcap)

        # Sector gap for this candidate
        s_gap = sector_gaps.get(sector, 0.0)
        # Index gap
        i_gap = tkr in missing_nifty
        # Cap gap
        c_gap = cap_gaps.get(cap_cat, 0.0)

        bonus = _compute_gap_bonus(s_gap, i_gap, c_gap)
        tier = _assign_tier(
            tkr, holdings_tickers, watchlist,
        )

        fills: list[str] = []
        if s_gap < -5:
            fills.append(f"sector:{sector}")
        if i_gap:
            fills.append("nifty50")
        if c_gap < -5:
            fills.append(f"cap:{cap_cat}")

        enriched.append({
            "ticker": tkr,
            "composite_score": comp,
            "gap_bonus": bonus,
            "gap_adjusted_score": round(
                comp + bonus, 2,
            ),
            "tier": tier,
            "fills_gaps": fills,
            "sector": sector,
            "cap_category": cap_cat,
        })

    # 11. Sort by gap-adjusted score, top 40
    enriched.sort(
        key=lambda x: x["gap_adjusted_score"],
        reverse=True,
    )
    top_candidates = enriched[:_TOP_CANDIDATES]

    # 12. Portfolio summary
    concentration_risks: list[str] = []
    if user_sectors:
        for sec, w in user_sectors.items():
            if w > 30:
                concentration_risks.append(
                    f"{sec}: {w:.1f}% (>30%)"
                )
    if cap_dist.get("largecap", 0) > 80:
        concentration_risks.append(
            "Large-cap heavy: "
            f"{cap_dist['largecap']:.1f}%"
        )

    portfolio_summary = {
        "total_holdings": len(holdings_tickers),
        "total_value": round(total_value, 2),
        "sector_weights": user_sectors,
        "cap_distribution": {
            k: round(v, 2) for k, v in cap_dist.items()
        },
        "concentration_risks": concentration_risks,
        "correlation_alerts": corr_alerts,
    }

    gap_analysis = {
        "sector_gaps": sector_gaps,
        "cap_gaps": cap_gaps,
        "missing_nifty50_count": len(missing_nifty),
        "missing_nifty50_sample": sorted(
            list(missing_nifty)
        )[:10],
    }

    _logger.info(
        "stage2_gap_analysis: user=%s, "
        "holdings=%d, candidates=%d, "
        "actions=%d, top=%d",
        user_id,
        len(holdings_tickers),
        len(candidates_df),
        len(portfolio_actions),
        len(top_candidates),
    )

    return {
        "portfolio_summary": portfolio_summary,
        "portfolio_actions": portfolio_actions,
        "candidates": top_candidates,
        "gap_analysis": gap_analysis,
    }


# ─────────────────────────────────────────────────────
# Stage 3 — LLM reasoning pass
# ─────────────────────────────────────────────────────

_REQUIRED_REC_FIELDS = {
    "tier", "category", "action", "severity", "rationale",
}


def _validate_llm_output(
    output: dict,
    valid_tickers: set,
) -> list[str]:
    """Validate LLM JSON response.

    Returns a list of error strings.  An empty list means
    the output is valid.

    Parameters
    ----------
    output : dict
        Parsed JSON from the LLM.
    valid_tickers : set
        Tickers known to the candidate universe.

    Returns
    -------
    list[str]
        Validation errors (empty = valid).
    """
    errors: list[str] = []
    if "recommendations" not in output:
        errors.append("Missing 'recommendations' key")
        return errors

    recs = output["recommendations"]
    if not isinstance(recs, list):
        errors.append("'recommendations' is not a list")
        return errors

    for i, rec in enumerate(recs):
        missing = _REQUIRED_REC_FIELDS - set(rec.keys())
        if missing:
            errors.append(
                f"rec[{i}]: missing fields "
                f"{sorted(missing)}"
            )
        ticker = rec.get("ticker")
        if ticker and ticker not in valid_tickers:
            errors.append(
                f"rec[{i}]: hallucinated ticker "
                f"'{ticker}'"
            )

    if "health_score" not in output:
        errors.append("Missing 'health_score'")
    if "health_label" not in output:
        errors.append("Missing 'health_label'")

    return errors


def _deterministic_fallback(
    candidates: list[dict],
    portfolio_actions: list[dict],
    health_score: float,
    health_label: str,
) -> dict:
    """Fallback when LLM reasoning fails.

    Returns top 5 recommendations with template rationale.
    Includes up to 2 portfolio_actions first, then fills
    with top candidates by gap_adjusted_score.

    Parameters
    ----------
    candidates : list[dict]
        Enriched candidates from Stage 2.
    portfolio_actions : list[dict]
        Holding actions from Stage 2.
    health_score : float
        Computed portfolio health score.
    health_label : str
        Health label string.

    Returns
    -------
    dict
        Stage 3 result dict.
    """
    recs: list[dict] = []

    # Up to 2 portfolio actions (exit/risk first)
    action_map = {
        "exit_reduce": "sell",
        "risk_alert": "alert",
        "rebalance": "reduce",
        "hold_accumulate": "accumulate",
    }
    priority = [
        "exit_reduce", "risk_alert",
        "rebalance", "hold_accumulate",
    ]
    sorted_actions = sorted(
        portfolio_actions,
        key=lambda a: priority.index(
            a.get("category", "hold_accumulate"),
        ),
    )
    for pa in sorted_actions[:2]:
        cat = pa.get("category", "hold_accumulate")
        recs.append({
            "ticker": pa["ticker"],
            "tier": "portfolio",
            "category": cat,
            "action": action_map.get(cat, "hold"),
            "severity": (
                "high" if cat == "exit_reduce"
                else "medium"
            ),
            "rationale": (
                f"Deterministic fallback: {cat} "
                f"signal based on composite score "
                f"{pa.get('composite_score', 0):.1f} "
                f"and 3m forecast "
                f"{pa.get('forecast_3m_pct', 0):.1f}%."
            ),
        })

    # Fill remaining slots from candidates
    remaining = 5 - len(recs)
    seen = {r["ticker"] for r in recs}
    for c in candidates[:remaining + 5]:
        if c["ticker"] in seen:
            continue
        recs.append({
            "ticker": c["ticker"],
            "tier": c.get("tier", "discovery"),
            "category": "value",
            "action": "buy",
            "severity": "medium",
            "rationale": (
                f"Deterministic fallback: "
                f"gap-adjusted score "
                f"{c.get('gap_adjusted_score', 0):.1f}"
                f", sector {c.get('sector', 'N/A')}."
            ),
        })
        seen.add(c["ticker"])
        if len(recs) >= 5:
            break

    return {
        "recommendations": recs,
        "portfolio_health_assessment": (
            f"Score {health_score:.0f}/100 "
            f"({health_label}). "
            "Generated via deterministic fallback."
        ),
        "health_score": health_score,
        "health_label": health_label,
        "llm_model": "deterministic_fallback",
        "llm_tokens_used": 0,
    }


def compute_outcome_label(
    action: str, return_pct: float,
) -> str:
    """Label recommendation outcome for tracking.

    Parameters
    ----------
    action : str
        Original action (buy, sell, hold, etc.).
    return_pct : float
        Actual percentage return since recommendation.

    Returns
    -------
    str
        ``"correct"``, ``"incorrect"``, or ``"neutral"``.
    """
    action_lower = action.lower()

    if action_lower in ("buy", "accumulate"):
        if return_pct > 2.0:
            return "correct"
        if return_pct < -2.0:
            return "incorrect"
        return "neutral"

    if action_lower in ("sell", "reduce"):
        if return_pct < -2.0:
            return "correct"
        if return_pct > 2.0:
            return "incorrect"
        return "neutral"

    if action_lower == "hold":
        if abs(return_pct) < 10.0:
            return "correct"
        return "incorrect"

    # alert, rotate, etc.
    return "neutral"


def _compute_health_score(
    portfolio_summary: dict,
) -> tuple[float, str]:
    """Compute 0-100 portfolio health score.

    Parameters
    ----------
    portfolio_summary : dict
        From Stage 2 output.

    Returns
    -------
    tuple[float, str]
        (score, label) where label is one of
        ``"critical"``, ``"needs_attention"``,
        ``"healthy"``, ``"excellent"``.
    """
    score = 70.0

    # Penalise concentration risks
    risks = portfolio_summary.get(
        "concentration_risks", [],
    )
    for risk in risks:
        if "sector" in risk.lower():
            score -= 10.0
        elif "cap" in risk.lower():
            score -= 8.0
        else:
            score -= 10.0

    # Penalise correlation alerts
    corr_alerts = portfolio_summary.get(
        "correlation_alerts", [],
    )
    score -= 5.0 * len(corr_alerts)

    # Low diversification penalty
    total_holdings = portfolio_summary.get(
        "total_holdings", 0,
    )
    if total_holdings < 5:
        score -= 15.0

    # Nifty 50 overlap bonus (+2 per stock, max +10)
    overlap = portfolio_summary.get(
        "nifty50_overlap", 0,
    )
    score += min(overlap * 2, 10)

    score = max(0.0, min(100.0, score))

    if score < 30:
        label = "critical"
    elif score < 60:
        label = "needs_attention"
    elif score < 80:
        label = "healthy"
    else:
        label = "excellent"

    return round(score, 1), label


_STAGE3_SYSTEM_PROMPT = """\
You are a portfolio recommendation engine. Given the \
user's portfolio state, candidate stocks, and gap \
analysis, select 5-8 actionable recommendations.

Rules:
1. Include at least 1 recommendation from each tier \
(portfolio, watchlist, discovery) IF candidates exist \
in that tier.
2. Include at least 1 defensive recommendation \
(sell/reduce/alert) if the portfolio has concentration \
risks, correlation alerts, or low diversification.
3. Balance offensive (buy/accumulate) and defensive \
(sell/reduce/alert) actions.
4. For each recommendation explain its portfolio impact.
5. Assign severity: high, medium, or low.
6. Reference specific data signals (composite score, \
forecast, sentiment, sector gap).
7. Output STRICT JSON only. No markdown fences, \
no commentary outside the JSON object.

Required JSON schema:
{
  "recommendations": [
    {
      "ticker": "SYMBOL.NS",
      "tier": "portfolio|watchlist|discovery",
      "category": "string",
      "action": "buy|sell|hold|accumulate|reduce|alert|\
rotate",
      "severity": "high|medium|low",
      "rationale": "string (2-3 sentences)",
      "expected_impact": "string",
      "data_signals": {}
    }
  ],
  "portfolio_health_assessment": "string (2-3 sentences)",
  "health_score": 0-100,
  "health_label": "critical|needs_attention|healthy|\
excellent"
}
"""


def stage3_llm_reasoning(
    stage2_output: dict,
) -> dict:
    """LLM reasoning pass over Stage 2 output.

    Calls FallbackLLM with structured context, parses
    and validates the JSON response.  Falls back to
    deterministic recommendations on any failure.

    Parameters
    ----------
    stage2_output : dict
        Output from :func:`stage2_gap_analysis`.

    Returns
    -------
    dict
        Keys: ``recommendations``,
        ``portfolio_health_assessment``,
        ``health_score``, ``health_label``,
        ``llm_model``, ``llm_tokens_used``.
    """
    portfolio_summary = stage2_output.get(
        "portfolio_summary", {},
    )
    portfolio_actions = stage2_output.get(
        "portfolio_actions", [],
    )
    candidates = stage2_output.get("candidates", [])
    gap_analysis = stage2_output.get("gap_analysis", {})

    # Compute health score
    health_score, health_label = _compute_health_score(
        portfolio_summary,
    )

    # Empty portfolio early return
    if (
        not candidates
        and not portfolio_actions
    ):
        return {
            "recommendations": [],
            "portfolio_health_assessment": (
                "Empty portfolio — no recommendations."
            ),
            "health_score": health_score,
            "health_label": health_label,
            "llm_model": "none",
            "llm_tokens_used": 0,
        }

    # Build valid ticker set for validation
    valid_tickers = {c["ticker"] for c in candidates}
    for pa in portfolio_actions:
        valid_tickers.add(pa["ticker"])

    # Build structured context for LLM
    context = {
        "portfolio_summary": portfolio_summary,
        "portfolio_actions": portfolio_actions[:10],
        "candidates": candidates[:20],
        "sector_gaps": gap_analysis.get(
            "sector_gaps", {},
        ),
        "cap_gaps": gap_analysis.get("cap_gaps", {}),
    }
    user_message = json.dumps(
        context, default=str, indent=2,
    )

    try:
        from langchain_core.messages import (
            HumanMessage,
            SystemMessage,
        )

        from config import get_settings
        from llm_fallback import FallbackLLM
        from token_budget import get_token_budget
        from message_compressor import (
            MessageCompressor,
        )

        settings = get_settings()
        tiers = [
            t.strip()
            for t in settings.groq_model_tiers.split(",")
            if t.strip()
        ]
        llm = FallbackLLM(
            groq_models=tiers,
            anthropic_model="claude-sonnet-4-6",
            temperature=0.3,
            agent_id="recommendation_engine",
            token_budget=get_token_budget(),
            compressor=MessageCompressor(),
            ollama_first=False,
        )
        response = llm.invoke([
            SystemMessage(
                content=_STAGE3_SYSTEM_PROMPT,
            ),
            HumanMessage(content=user_message),
        ])
        raw = response.content

        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            first_nl = text.index("\n")
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

        parsed = json.loads(text)

        # Validate
        errors = _validate_llm_output(
            parsed, valid_tickers,
        )

        # Remove hallucinated tickers (soft fix)
        if "recommendations" in parsed:
            cleaned = []
            for rec in parsed["recommendations"]:
                tkr = rec.get("ticker")
                if tkr and tkr not in valid_tickers:
                    _logger.warning(
                        "Removing hallucinated ticker "
                        "%s from LLM output",
                        tkr,
                    )
                    continue
                cleaned.append(rec)
            parsed["recommendations"] = cleaned

        # Check for structural errors (not just
        # hallucinated tickers which we already removed)
        structural = [
            e for e in errors
            if "hallucinated" not in e
        ]
        if structural:
            _logger.warning(
                "LLM output validation errors: %s",
                structural,
            )
            return _deterministic_fallback(
                candidates,
                portfolio_actions,
                health_score,
                health_label,
            )

        # Override health score/label with computed
        parsed["health_score"] = health_score
        parsed["health_label"] = health_label

        # Add LLM metadata
        model = getattr(
            llm, "last_provider", "unknown",
        )
        parsed["llm_model"] = model
        parsed["llm_tokens_used"] = len(
            user_message,
        ) + len(raw)

        return parsed

    except Exception:
        _logger.warning(
            "Stage 3 LLM reasoning failed, "
            "using deterministic fallback",
            exc_info=True,
        )
        return _deterministic_fallback(
            candidates,
            portfolio_actions,
            health_score,
            health_label,
        )
