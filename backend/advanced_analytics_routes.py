"""Advanced Analytics API endpoints (AA-7).

Mounted at ``/v1/advanced-analytics/``. Pro + superuser only
(``pro_or_superuser`` guard, §5.7). Powers the 7-tab
``/advanced-analytics`` page (Sprint 9 AA-Epic).

Reports
-------
- ``/current-day-upmove``      — today's up-movers
- ``/previous-day-breakout``   — sorted by today_x_vol
- ``/mom-volume-delivery``     — month-over-month vol/dv
- ``/wow-volume-delivery``     — week-over-week vol/dv
- ``/two-day-scan``            — 2-day persistence
- ``/three-day-scan``          — 3-day persistence
- ``/top-50-delivery-by-qty``  — top 50 by today_dv × x_dv_20d

Pattern (per CLAUDE.md §5.4 + §5.13)
------------------------------------
Each endpoint is a thin wrapper over :func:`_compute_report`:

1. ``pro_or_superuser`` guard.
2. ``await _scoped_tickers(user, "discovery")`` — per-user
   universe (per-user cache key avoids cross-user leak,
   §5.9).
3. Cache key
   ``cache:advanced_analytics:<report>:{user_id}:p{page}:s{sort_key}:{sort_dir}:ps{page_size}``
   read first, ``ttl=TTL_STABLE`` write on miss
   (§5.13 — kwarg ``ttl`` not ``ex``).
4. Single batched DuckDB read per Iceberg table joined
   on ticker (§4.1 #1).
5. Per-tab filter + default sort.
6. ``stale_tickers`` for any ticker missing required input
   (§5.5 transparency chip).
7. Paginate → :class:`AdvancedReportResponse`.

The response is the same superset shape across all 7 reports
(plan §6) so the shared frontend ``<AdvancedAnalyticsTable />``
+ CSV export can stay DRY.
"""

from __future__ import annotations

import csv
import logging
import math
from datetime import date
from io import StringIO
from typing import Literal

import pandas as pd
from advanced_analytics_filters import (
    FUND_KEYS,
    TECH_KEYS,
    parse_filter_csv,
    passes_bundle_filters,
)
from advanced_analytics_models import (
    AdvancedReportResponse,
    AdvancedRow,
    StaleTicker,
)
from cache import TTL_STABLE, get_cache
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from insights_routes import _get_stock_repo, _scoped_tickers
from market_utils import detect_market

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)

ReportName = Literal[
    "current-day-upmove",
    "previous-day-breakout",
    "mom-volume-delivery",
    "wow-volume-delivery",
    "two-day-scan",
    "three-day-scan",
    "top-50-delivery-by-qty",
]

REPORTS: tuple[ReportName, ...] = (
    "current-day-upmove",
    "previous-day-breakout",
    "mom-volume-delivery",
    "wow-volume-delivery",
    "two-day-scan",
    "three-day-scan",
    "top-50-delivery-by-qty",
)

# Filter literals — keep narrow to dodge cardinality
# explosion in the cache key (4 markets × 4 types × 7
# reports = 112 base keys before pagination/sort).
MarketFilter = Literal["all", "india", "us"]
TickerTypeFilter = Literal["all", "stock", "etf"]


def _filter_tickers(
    tickers: list[str],
    market: MarketFilter,
    ticker_type: TickerTypeFilter,
) -> list[str]:
    """Apply market + ticker_type filters to the scoped ticker list.

    ``market="india"`` keeps only tickers ``detect_market``
    flags as Indian (``.NS`` / ``.BO`` suffix or known
    Indian-index ticker). ``ticker_type="etf"`` consults the
    stock-master registry; tickers without a registry entry
    are conservatively kept (they pass through and surface
    as ``stale`` if downstream data is missing).
    """
    if market == "all" and ticker_type == "all":
        return tickers

    registry: dict = {}
    if ticker_type != "all":
        try:
            registry = _get_stock_repo().get_all_registry()
        except Exception as exc:  # pragma: no cover — defensive
            _logger.warning(
                "advanced_analytics registry lookup failed: %s",
                exc,
            )

    out: list[str] = []
    for t in tickers:
        if market != "all":
            mkt = detect_market(t)
            if market == "india" and mkt != "india":
                continue
            if market == "us" and mkt != "us":
                continue
        if ticker_type != "all":
            meta = registry.get(t) or registry.get(t.upper()) or {}
            kind = str(meta.get("ticker_type", "stock")).lower()
            if ticker_type == "stock" and kind != "stock":
                continue
            if ticker_type == "etf" and kind != "etf":
                continue
        out.append(t)
    return out


# ---------------------------------------------------------------
# NaN-safe coercion helpers
# ---------------------------------------------------------------


def _f(val) -> float | None:
    """Coerce *val* to float; return None for NaN / non-numeric."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 4)


def _i(val) -> int | None:
    """Coerce *val* to int; return None for NaN / non-numeric."""
    f = _f(val)
    return None if f is None else int(f)


def _s(val) -> str | None:
    """Coerce *val* to str; reject NaN sentinels (§6.1)."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "null", "n/a", "na", "nat"}:
        return None
    return s


def _iso_utc(val) -> str | None:
    """Format a date / datetime as ISO 8601 UTC ``Z`` (§5.1)."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    try:
        ts = pd.to_datetime(val, utc=True, errors="coerce")
    except (TypeError, ValueError):
        return None
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------
# Iceberg loaders — one batched DuckDB call per table
# ---------------------------------------------------------------


def _ph(tickers: list[str]) -> str:
    """SQL placeholder for ``WHERE ticker IN (...)``."""
    return ",".join(f"'{t}'" for t in tickers)


def _safe_query(table: str, sql: str) -> pd.DataFrame:
    """Run a DuckDB query against *table*; return empty on error."""
    try:
        from backend.db.duckdb_engine import query_iceberg_df

        return query_iceberg_df(table, sql)
    except Exception as exc:
        _logger.warning("advanced_analytics read %s: %s", table, exc)
        return pd.DataFrame()


_AS_OF_CACHE_KEY = "cache:aa:as_of"
_AS_OF_TTL_S = 60


def _effective_trading_date() -> date:
    """Return the most-recent date with NSE bhavcopy data.

    Anchors every AA report to the same "current day" so
    ``today_x_vol`` (volume) and ``current_dpc`` (delivery)
    consistently describe the same trading session — even
    when the daily pipeline runs on a holiday / weekend
    morning where today's bhavcopy isn't published yet,
    or when OHLCV's bulk download is one day ahead of the
    delivery feed.

    Cached for 60 s in Redis — the underlying ``MAX(date)``
    DuckDB scan against ``stocks.nse_delivery`` (~50 k rows)
    runs in seconds, so caching makes the cache-key check
    cheap on every request. The 60 s TTL is short enough
    that a fresh bhavcopy ingest is reflected on the next
    page load.

    Falls back to ``date.today()`` when the delivery table
    is empty (cold start, dev with BSE-blocked sources,
    etc.) — behaves identically to the prior unanchored
    code in that case.
    """
    cache = get_cache()
    raw_cached = cache.get(_AS_OF_CACHE_KEY)
    if raw_cached is not None:
        try:
            value = (
                raw_cached.decode()
                if isinstance(raw_cached, (bytes, bytearray))
                else raw_cached
            )
            return date.fromisoformat(str(value))
        except Exception:  # pragma: no cover — defensive
            pass

    df = _safe_query(
        "stocks.nse_delivery",
        "SELECT MAX(date) AS d FROM nse_delivery",
    )
    out: date
    if df.empty:
        out = date.today()
    else:
        raw = df["d"].iloc[0]
        if raw is None or pd.isna(raw):
            out = date.today()
        elif hasattr(raw, "date"):
            out = raw.date()
        elif isinstance(raw, date):
            out = raw
        else:
            out = date.today()

    try:
        cache.set(_AS_OF_CACHE_KEY, out.isoformat(), ttl=_AS_OF_TTL_S)
    except Exception:  # pragma: no cover — defensive
        pass
    return out


def _load_ohlcv_25d(
    tickers: list[str],
    as_of: date,
) -> pd.DataFrame:
    """Last 25 trading days of OHLCV per ticker, ending
    on or before *as_of*.

    25 days covers the longest window any AA report needs
    (20-day rolling avg + a 5-day buffer for non-trading
    gaps). The ``date <= as_of`` cap aligns OHLCV to the
    same effective "current day" used by the delivery
    feed (see :func:`_effective_trading_date`).
    """
    if not tickers:
        return pd.DataFrame()
    return _safe_query(
        "stocks.ohlcv",
        "SELECT ticker, date, open, high, low, close, volume "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY date DESC"
        "  ) AS rn FROM ohlcv "
        f"  WHERE ticker IN ({_ph(tickers)}) "
        "    AND date >= '1980-01-01'"
        f"    AND date <= '{as_of.isoformat()}'"
        ") WHERE rn <= 25",
    )


def _load_delivery_25d(
    tickers: list[str],
    as_of: date,
) -> pd.DataFrame:
    """Last 25 trading days of NSE delivery per ticker,
    ending on or before *as_of*."""
    if not tickers:
        return pd.DataFrame()
    return _safe_query(
        "stocks.nse_delivery",
        "SELECT ticker, date, deliverable_qty, delivery_pct, "
        "traded_qty, traded_value "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY date DESC"
        "  ) AS rn FROM nse_delivery "
        f"  WHERE ticker IN ({_ph(tickers)}) "
        f"    AND date <= '{as_of.isoformat()}'"
        ") WHERE rn <= 25",
    )


def _golden_cross_days_ago(ind: pd.DataFrame) -> int | None:
    """Trading days since SMA 50 last crossed above SMA 200.

    Returns:
        None — SMA 50 ≤ SMA 200 today (no golden cross).
        0–N  — cross happened N trading rows back; 0 = today.
        999  — SMA 50 has been above SMA 200 for the entire
               215-row window (established bullish, no cross
               visible in available history).
    """
    s50 = ind["SMA_50"] if "SMA_50" in ind.columns else None
    s200 = ind["SMA_200"] if "SMA_200" in ind.columns else None
    if s50 is None or s200 is None:
        return None

    last50 = s50.iloc[-1]
    last200 = s200.iloc[-1]
    if pd.isna(last50) or pd.isna(last200) or last50 <= last200:
        return None

    n = len(ind)
    for i in range(n - 1, 0, -1):
        v50, v200 = s50.iloc[i], s200.iloc[i]
        p50, p200 = s50.iloc[i - 1], s200.iloc[i - 1]
        if pd.isna(v50) or pd.isna(v200) or pd.isna(p50) or pd.isna(p200):
            return 999
        if v50 > v200 and p50 <= p200:
            return (n - 1) - i

    return 999


def _death_cross_days_ago(ind: pd.DataFrame) -> int | None:
    """Trading days since SMA 50 last crossed BELOW SMA 200.

    Mirror of :func:`_golden_cross_days_ago` with inverted
    comparators.

    Returns:
        None — SMA 50 ≥ SMA 200 today (no death cross active).
        0–N  — cross happened N trading rows back; 0 = today.
        999  — SMA 50 has been below SMA 200 for the entire
               window (established bearish, no cross visible).
    """
    s50 = ind["SMA_50"] if "SMA_50" in ind.columns else None
    s200 = ind["SMA_200"] if "SMA_200" in ind.columns else None
    if s50 is None or s200 is None:
        return None

    last50 = s50.iloc[-1]
    last200 = s200.iloc[-1]
    if pd.isna(last50) or pd.isna(last200) or last50 >= last200:
        return None

    n = len(ind)
    for i in range(n - 1, 0, -1):
        v50, v200 = s50.iloc[i], s200.iloc[i]
        p50, p200 = s50.iloc[i - 1], s200.iloc[i - 1]
        if pd.isna(v50) or pd.isna(v200) or pd.isna(p50) or pd.isna(p200):
            return 999
        if v50 < v200 and p50 >= p200:
            return (n - 1) - i

    return 999


def _rolling_band_20d_prev(
    ohlcv: pd.DataFrame,
) -> tuple[float | None, float | None]:
    """20-day rolling (low, high) EXCLUDING the last row (today).

    Returns (None, None) when fewer than 21 rows of history are
    available — caller cannot use the band for breakout detection
    without a clean prior window.
    """
    if "low" not in ohlcv.columns or "high" not in ohlcv.columns:
        return (None, None)
    if len(ohlcv) < 21:
        return (None, None)
    prev_window = ohlcv.iloc[-21:-1]
    low = prev_window["low"].min(skipna=True)
    high = prev_window["high"].max(skipna=True)
    return (
        None if pd.isna(low) else float(low),
        None if pd.isna(high) else float(high),
    )


def _load_indicators_latest(tickers: list[str]) -> pd.DataFrame:
    """Compute latest RSI-14, SMA-50, SMA-200 per ticker.

    Single bulk OHLCV scan (215 rows per ticker) — the retired
    ``stocks.technical_indicators`` Iceberg table is no longer
    populated (see DEAD_TABLES in iceberg_maintenance.py).
    215 rows covers SMA-200 plus a holiday/weekend buffer.
    One DuckDB read for all tickers (§4.1 #1).
    """
    if not tickers:
        return pd.DataFrame()

    raw = _safe_query(
        "stocks.ohlcv",
        "SELECT ticker, date, open, high, low, close "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY date DESC"
        "  ) AS rn FROM ohlcv "
        f"  WHERE ticker IN ({_ph(tickers)}) "
        "    AND date >= '1980-01-01'"
        ") WHERE rn <= 215",
    )
    if raw.empty:
        return pd.DataFrame()

    from backend.tools._analysis_indicators import (
        _calculate_technical_indicators,
    )

    result_rows: list[dict] = []
    for tkr, grp in raw.groupby("ticker"):
        grp = grp.sort_values("date")
        ohlcv = grp.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
            }
        ).set_index("date")
        try:
            ind = _calculate_technical_indicators(ohlcv)
            last = ind.iloc[-1]
            result_rows.append(
                {
                    "ticker": str(tkr),
                    "rsi_14": last.get("RSI_14"),
                    "sma_50": last.get("SMA_50"),
                    "sma_200": last.get("SMA_200"),
                    "golden_cross_days_ago": _golden_cross_days_ago(ind),
                }
            )
        except Exception as exc:
            _logger.debug("indicator compute skipped for %s: %s", tkr, exc)

    if not result_rows:
        return pd.DataFrame()
    return pd.DataFrame(result_rows)


def _load_fundamentals(tickers: list[str]) -> pd.DataFrame:
    """Latest ``fundamentals_snapshot`` row per ticker."""
    if not tickers:
        return pd.DataFrame()
    return _safe_query(
        "stocks.fundamentals_snapshot",
        "SELECT ticker, sales_3y_cagr, prft_3y_cagr, "
        "sales_5y_cagr, prft_5y_cagr, yoy_qtr_prft, "
        "yoy_qtr_sales, debt_to_eq, roce "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY snapshot_date DESC"
        "  ) AS rn FROM fundamentals_snapshot "
        f"  WHERE ticker IN ({_ph(tickers)})"
        ") WHERE rn = 1",
    )


def _load_promoter(tickers: list[str]) -> pd.DataFrame:
    """Latest quarter of promoter holdings per ticker."""
    if not tickers:
        return pd.DataFrame()
    return _safe_query(
        "stocks.promoter_holdings",
        "SELECT ticker, prom_hld_pct, pledged_pct, chng_qoq "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY quarter_end DESC"
        "  ) AS rn FROM promoter_holdings "
        f"  WHERE ticker IN ({_ph(tickers)})"
        ") WHERE rn = 1",
    )


def _load_events(tickers: list[str]) -> pd.DataFrame:
    """Latest corporate event per ticker."""
    if not tickers:
        return pd.DataFrame()
    return _safe_query(
        "stocks.corporate_events",
        "SELECT ticker, event_date, event_type, event_label "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY event_date DESC"
        "  ) AS rn FROM corporate_events "
        f"  WHERE ticker IN ({_ph(tickers)})"
        ") WHERE rn = 1",
    )


def _load_pscore(tickers: list[str]) -> pd.DataFrame:
    """Latest Piotroski score per ticker."""
    if not tickers:
        return pd.DataFrame()
    return _safe_query(
        "stocks.piotroski_scores",
        "SELECT ticker, total_score "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY score_date DESC"
        "  ) AS rn FROM piotroski_scores "
        f"  WHERE ticker IN ({_ph(tickers)})"
        ") WHERE rn = 1",
    )


def _load_company(tickers: list[str]) -> pd.DataFrame:
    """Latest ``company_info`` row per ticker."""
    if not tickers:
        return pd.DataFrame()
    return _safe_query(
        "stocks.company_info",
        "SELECT ticker, company_name, sector, industry, "
        "week_52_high, week_52_low "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY fetched_at DESC"
        "  ) AS rn FROM company_info "
        f"  WHERE ticker IN ({_ph(tickers)})"
        ") WHERE rn = 1",
    )


# ---------------------------------------------------------------
# Per-ticker derivation
# ---------------------------------------------------------------


def _ohlcv_groups(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Partition OHLCV by ticker, sorted oldest→newest."""
    if df.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for tkr, sub in df.groupby("ticker"):
        out[str(tkr)] = sub.sort_values("date").reset_index(drop=True)
    return out


def _delivery_groups(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Partition delivery by ticker, sorted oldest→newest."""
    return _ohlcv_groups(df)


def _emv_14_for(
    ticker_ohlcv: pd.DataFrame,
) -> tuple[float | None, float | None]:
    """Return (latest_emv, latest_emv) — both columns map to one series.

    The plan surfaces ``avg_emv_score`` and ``avg_14d_emv``
    in the row but they are equivalent values from
    :func:`compute_emv_14` (SMA-14 of Ease-of-Movement). The
    Sprint 9 deviation (per memory line 64) keeps EMV
    compute-only — no Iceberg column.
    """
    if ticker_ohlcv.empty or len(ticker_ohlcv) < 15:
        return (None, None)
    try:
        from backend.tools._analysis_indicators import compute_emv_14

        renamed = ticker_ohlcv.rename(
            columns={"high": "High", "low": "Low", "volume": "Volume"},
        )
        series = compute_emv_14(renamed)
        if series.empty:
            return (None, None)
        latest = _f(series.iloc[-1])
        return (latest, latest)
    except Exception as exc:
        _logger.debug(
            "emv_14 compute %s: %s", ticker_ohlcv.iloc[0]["ticker"], exc
        )
        return (None, None)


def _ppc_stats(closes: list[float]) -> dict[str, float | None]:
    """Per-period close stats: today, prev, prev-2, 10d/20d avg."""
    n = len(closes)
    return {
        "today_ltp": _f(closes[-1]) if n >= 1 else None,
        "prev_day_ltp": _f(closes[-2]) if n >= 2 else None,
        "prev_2_prev_day_ltp": _f(closes[-3]) if n >= 3 else None,
        "current_ppc": _f(closes[-1]) if n >= 1 else None,
        "avg_10d_ppc": _f(sum(closes[-10:]) / min(n, 10)) if n >= 1 else None,
        "avg_20d_ppc": _f(sum(closes[-20:]) / min(n, 20)) if n >= 1 else None,
    }


def _vol_stats(vols: list[float]) -> dict[str, float | None]:
    """Per-period volume stats + multipliers vs 10d/20d average."""
    n = len(vols)
    today = vols[-1] if n >= 1 else None
    prev = vols[-2] if n >= 2 else None
    avg10 = sum(vols[-10:]) / min(n, 10) if n >= 1 else None
    avg20 = sum(vols[-20:]) / min(n, 20) if n >= 1 else None
    return {
        "today_vol": _f(today),
        "prev_day_vol": _f(prev),
        "avg_10d_vol": _f(avg10),
        "avg_20d_vol": _f(avg20),
        "today_x_vol": _f(today / avg20) if today and avg20 else None,
        "prev_day_x_vol": _f(prev / avg20) if prev and avg20 else None,
        "x_vol_10d": _f(today / avg10) if today and avg10 else None,
        "x_vol_20d": _f(today / avg20) if today and avg20 else None,
    }


def _delivery_stats(
    dv: list[float], dpc: list[float]
) -> dict[str, float | None]:
    """Per-period delivery (qty + %) stats + multipliers."""
    n = len(dv)
    today_dv = dv[-1] if n >= 1 else None
    prev_dv = dv[-2] if n >= 2 else None
    avg10_dv = sum(dv[-10:]) / min(n, 10) if n >= 1 else None
    avg20_dv = sum(dv[-20:]) / min(n, 20) if n >= 1 else None

    nd = len(dpc)
    today_dpc = dpc[-1] if nd >= 1 else None
    prev_dpc = dpc[-2] if nd >= 2 else None
    avg10_dpc = sum(dpc[-10:]) / min(nd, 10) if nd >= 1 else None
    avg20_dpc = sum(dpc[-20:]) / min(nd, 20) if nd >= 1 else None

    return {
        "today_dv": _f(today_dv),
        "prev_day_dv": _f(prev_dv),
        "avg_10d_dv": _f(avg10_dv),
        "avg_20d_dv": _f(avg20_dv),
        "today_dpc": _f(today_dpc),
        "prev_day_dpc": _f(prev_dpc),
        "avg_10d_dpc": _f(avg10_dpc),
        "avg_20d_dpc": _f(avg20_dpc),
        "current_dpc": _f(today_dpc),
        "today_x_dv": (
            _f(today_dv / avg20_dv) if today_dv and avg20_dv else None
        ),
        "prev_day_x_dv": (
            _f(prev_dv / avg20_dv) if prev_dv and avg20_dv else None
        ),
        "x_dv_10d": _f(today_dv / avg10_dv) if today_dv and avg10_dv else None,
        "x_dv_20d": _f(today_dv / avg20_dv) if today_dv and avg20_dv else None,
    }


def _build_row(
    ticker: str,
    ohlcv_g: pd.DataFrame | None,
    delivery_g: pd.DataFrame | None,
    indicators: dict | None,
    funds: dict | None,
    prom: dict | None,
    event: dict | None,
    pscore: dict | None,
    company: dict | None,
) -> AdvancedRow:
    """Compose one :class:`AdvancedRow` from joined inputs."""
    closes: list[float] = []
    vols: list[float] = []
    if ohlcv_g is not None and not ohlcv_g.empty:
        closes = [
            c
            for c in ohlcv_g["close"].tolist()
            if c is not None and not (isinstance(c, float) and math.isnan(c))
        ]
        vols = [
            v
            for v in ohlcv_g["volume"].tolist()
            if v is not None and not (isinstance(v, float) and math.isnan(v))
        ]

    dv_qty: list[float] = []
    dpc: list[float] = []
    if delivery_g is not None and not delivery_g.empty:
        dv_qty = [
            v
            for v in delivery_g["deliverable_qty"].tolist()
            if v is not None and not (isinstance(v, float) and math.isnan(v))
        ]
        dpc = [
            v
            for v in delivery_g["delivery_pct"].tolist()
            if v is not None and not (isinstance(v, float) and math.isnan(v))
        ]

    ppc = _ppc_stats(closes)
    vs = _vol_stats(vols)
    ds = _delivery_stats(dv_qty, dpc)

    avg_emv, avg_14d_emv = (None, None)
    if ohlcv_g is not None and not ohlcv_g.empty:
        avg_emv, avg_14d_emv = _emv_14_for(ohlcv_g)

    today_ltp = ppc["today_ltp"]
    today_vol = vs["today_vol"]
    avg10_vol = vs["avg_10d_vol"]
    avg20_vol = vs["avg_20d_vol"]
    today_not = _f(today_vol * today_ltp) if today_vol and today_ltp else None
    avg10_not = _f(avg10_vol * today_ltp) if avg10_vol and today_ltp else None
    avg20_not = _f(avg20_vol * today_ltp) if avg20_vol and today_ltp else None

    week_52_high = _f((company or {}).get("week_52_high"))
    week_52_low = _f((company or {}).get("week_52_low"))
    away = None
    if today_ltp and week_52_high:
        away = _f((week_52_high - today_ltp) / week_52_high * 100.0)

    return AdvancedRow(
        ticker=ticker,
        company_name=_s((company or {}).get("company_name")),
        sector=_s((company or {}).get("sector")),
        sub_sector=_s((company or {}).get("industry")),
        pscore=_i((pscore or {}).get("total_score")),
        rsi=_f((indicators or {}).get("rsi_14")),
        avg_emv_score=avg_emv,
        avg_14d_emv=avg_14d_emv,
        sma_50=_f((indicators or {}).get("sma_50")),
        sma_200=_f((indicators or {}).get("sma_200")),
        golden_cross_days_ago=_i(
            (indicators or {}).get("golden_cross_days_ago")
        ),
        week_52_high=week_52_high,
        week_52_low=week_52_low,
        away_from_52week_high=away,
        today_not=today_not,
        avg_10d_not=avg10_not,
        avg_20d_not=avg20_not,
        debt_to_eq=_f((funds or {}).get("debt_to_eq")),
        yoy_qtr_prft=_f((funds or {}).get("yoy_qtr_prft")),
        yoy_qtr_sales=_f((funds or {}).get("yoy_qtr_sales")),
        sales_growth_3yrs=_f((funds or {}).get("sales_3y_cagr")),
        prft_growth_3yrs=_f((funds or {}).get("prft_3y_cagr")),
        sales_growth_5yrs=_f((funds or {}).get("sales_5y_cagr")),
        prft_growth_5yrs=_f((funds or {}).get("prft_5y_cagr")),
        roce=_f((funds or {}).get("roce")),
        chng_in_prom_hld=_f((prom or {}).get("chng_qoq")),
        pledged=_f((prom or {}).get("pledged_pct")),
        prom_hld=_f((prom or {}).get("prom_hld_pct")),
        event=_s((event or {}).get("event_label"))
        or _s((event or {}).get("event_type")),
        event_date=_iso_utc((event or {}).get("event_date")),
        **ppc,
        **vs,
        **ds,
    )


# ---------------------------------------------------------------
# Stale-ticker detection (§5.5)
# ---------------------------------------------------------------


def _stale_for_row(row: AdvancedRow) -> StaleTicker | None:
    """Map a row's missing-input pattern to a single chip reason."""
    if row.today_ltp is None:
        return StaleTicker(ticker=row.ticker, reason="nan_close")
    if row.today_dv is None and row.today_dpc is None:
        return StaleTicker(ticker=row.ticker, reason="missing_delivery")
    if row.debt_to_eq is None and row.roce is None:
        return StaleTicker(ticker=row.ticker, reason="missing_quarterly")
    if row.prom_hld is None and row.pledged is None:
        return StaleTicker(ticker=row.ticker, reason="missing_promoter")
    return None


# ---------------------------------------------------------------
# Per-tab filter + default sort
# ---------------------------------------------------------------


def _passes_filter(row: AdvancedRow, report: ReportName) -> bool:
    """Per-report inclusion filter (column-availability matrix)."""
    if report == "current-day-upmove":
        return (row.today_x_vol or 0) > 1 and (row.current_dpc or 0) > (
            row.avg_20d_dpc or 0
        )
    if report == "previous-day-breakout":
        return (row.today_x_vol or 0) > 1
    if report == "mom-volume-delivery":
        return (row.x_vol_20d or 0) > 1 or (row.x_dv_20d or 0) > 1
    if report == "wow-volume-delivery":
        return (row.x_vol_10d or 0) > 1 or (row.x_dv_10d or 0) > 1
    if report == "two-day-scan":
        return (row.today_x_vol or 0) > 1 and (row.prev_day_x_vol or 0) > 1
    if report == "three-day-scan":
        return (row.today_x_vol or 0) > 1 and (row.prev_day_x_vol or 0) > 1
    if report == "top-50-delivery-by-qty":
        return (row.today_dv or 0) > 0
    return True


_DEFAULT_SORT: dict[ReportName, tuple[str, str]] = {
    "current-day-upmove": ("today_x_vol", "desc"),
    "previous-day-breakout": ("today_x_vol", "desc"),
    "mom-volume-delivery": ("x_dv_20d", "desc"),
    "wow-volume-delivery": ("x_dv_10d", "desc"),
    "two-day-scan": ("today_x_vol", "desc"),
    "three-day-scan": ("today_x_vol", "desc"),
    "top-50-delivery-by-qty": ("today_dv", "desc"),
}


def _sort_key(row: AdvancedRow, key: str):
    """Pull sort key from a row; None values sort last."""
    val = getattr(row, key, None)
    if val is None:
        # Sentinel sorts last in both directions.
        return (1, 0)
    return (0, val)


def _apply_sort_paginate(
    rows: list[AdvancedRow],
    report: ReportName,
    sort_key: str | None,
    sort_dir: str,
    page: int,
    page_size: int,
) -> tuple[list[AdvancedRow], int]:
    """Sort + paginate; return (page_rows, total)."""
    key, default_dir = _DEFAULT_SORT.get(report, ("ticker", "asc"))
    use_key = sort_key or key
    use_dir = sort_dir or default_dir
    if use_key not in AdvancedRow.model_fields:
        use_key = key
    reverse = use_dir == "desc"
    rows = sorted(rows, key=lambda r: _sort_key(r, use_key), reverse=reverse)

    if report == "top-50-delivery-by-qty":
        rows = rows[:50]

    total = len(rows)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    return (rows[start:end], total)


# ---------------------------------------------------------------
# Joined-dataset orchestrator
# ---------------------------------------------------------------


def _df_to_dict(df: pd.DataFrame) -> dict[str, dict]:
    """``ticker → row.to_dict()`` for an indexed-by-ticker DataFrame."""
    if df.empty:
        return {}
    return {
        str(r["ticker"]): {k: r[k] for k in df.columns if k != "ticker"}
        for _, r in df.iterrows()
    }


def _build_all_rows(
    tickers: list[str],
    as_of: date,
) -> list[AdvancedRow]:
    """Run all 8 batched DuckDB reads, then compose rows.

    One SQL round-trip per Iceberg table — never a per-ticker
    loop into Iceberg (§4.1 #1, #2).

    *as_of* is the effective trading date — caps OHLCV +
    delivery loads so both halves of every row describe the
    same trading session.
    """
    if not tickers:
        return []

    ohlcv_df = _load_ohlcv_25d(tickers, as_of)
    delivery_df = _load_delivery_25d(tickers, as_of)
    ind_df = _load_indicators_latest(tickers)
    funds_df = _load_fundamentals(tickers)
    prom_df = _load_promoter(tickers)
    events_df = _load_events(tickers)
    pscore_df = _load_pscore(tickers)
    company_df = _load_company(tickers)

    ohlcv_groups = _ohlcv_groups(ohlcv_df)
    delivery_groups = _delivery_groups(delivery_df)
    ind_map = _df_to_dict(ind_df)
    funds_map = _df_to_dict(funds_df)
    prom_map = _df_to_dict(prom_df)
    events_map = _df_to_dict(events_df)
    pscore_map = _df_to_dict(pscore_df)
    company_map = _df_to_dict(company_df)

    rows: list[AdvancedRow] = []
    for tkr in tickers:
        rows.append(
            _build_row(
                ticker=tkr,
                ohlcv_g=ohlcv_groups.get(tkr),
                delivery_g=delivery_groups.get(tkr),
                indicators=ind_map.get(tkr),
                funds=funds_map.get(tkr),
                prom=prom_map.get(tkr),
                event=events_map.get(tkr),
                pscore=pscore_map.get(tkr),
                company=company_map.get(tkr),
            )
        )
    return rows


# ---------------------------------------------------------------
# Report compute (cache + scope + sort + pagination)
# ---------------------------------------------------------------


async def _cached_full_rows(
    user: UserContext,
    as_of: date,
) -> list[AdvancedRow]:
    """Return the full row list for *user* on *as_of*, cached.

    The expensive part (8 batched DuckDB reads + Python row
    composition over the user's scope) runs once per
    ``(user_id, as_of)`` pair. Subsequent filter / sort /
    page changes reuse the cached row list and apply all
    transforms in-memory — no DuckDB round-trip.

    Cache TTL = ``TTL_STABLE``; key embeds *as_of* so the
    next bhavcopy day naturally invalidates the prior day's
    cache entry.
    """
    import json

    cache = get_cache()
    ck = f"cache:aa:rows:{user.user_id}" f":dt{as_of.isoformat()}"
    blob = cache.get(ck)
    if blob is not None:
        try:
            return [AdvancedRow(**d) for d in json.loads(blob)]
        except Exception:  # pragma: no cover — defensive
            _logger.warning(
                "advanced_analytics row-cache parse failed",
                exc_info=True,
            )

    tickers = await _scoped_tickers(user, "discovery")
    rows = _build_all_rows(tickers, as_of)
    try:
        cache.set(
            ck,
            json.dumps([r.model_dump() for r in rows]),
            ttl=TTL_STABLE,
        )
    except Exception:  # pragma: no cover — defensive
        _logger.warning(
            "advanced_analytics row-cache set failed",
            exc_info=True,
        )
    return rows


async def _compute_report(
    user: UserContext,
    report: ReportName,
    page: int,
    page_size: int,
    sort_key: str | None,
    sort_dir: str,
    market: MarketFilter = "all",
    ticker_type: TickerTypeFilter = "all",
    search: str = "",
    tech: str = "",
    fund: str = "",
) -> Response:
    """Shared cache / scope / compute pipeline for all 7 endpoints.

    Two cache layers:
      1. Outer (unchanged) — full row list keyed on
         ``(user, as_of)``.
      2. Inner — full response keyed on every parameter
         including the (sorted, deduped) bundle filters.
    """
    cache = get_cache()
    needle = search.strip().upper()
    tech_keys = parse_filter_csv(tech, TECH_KEYS, "tech")
    fund_keys = parse_filter_csv(fund, FUND_KEYS, "fund")
    # Anchor the report to the most-recent NSE bhavcopy day
    # so every ticker's "today" describes the same trading
    # session (handles weekends / public holidays / long
    # weekends without per-report date logic).
    as_of = _effective_trading_date()
    inner_ck = (
        f"cache:advanced_analytics:{report}:{user.user_id}"
        f":m{market}:t{ticker_type}:q{needle}"
        f":ftech{','.join(tech_keys)}"
        f":ffund{','.join(fund_keys)}"
        f":dt{as_of.isoformat()}"
        f":p{page}:s{sort_key or 'default'}:{sort_dir}"
        f":ps{page_size}"
    )
    hit = cache.get(inner_ck)
    if hit is not None:
        return Response(content=hit, media_type="application/json")

    # Outer cache hit returns instantly; miss runs the
    # 8 DuckDB queries + row composition once.
    full_rows = await _cached_full_rows(user, as_of)

    # Apply market / ticker_type filter via the same helper
    # that backs the legacy path (registry-aware, .NS-aware).
    keep = set(
        _filter_tickers(
            [r.ticker for r in full_rows],
            market,
            ticker_type,
        )
    )
    rows = [r for r in full_rows if r.ticker in keep]
    if needle:
        rows = [r for r in rows if needle in r.ticker.upper()]
    if tech_keys or fund_keys:
        rows = [
            r for r in rows if passes_bundle_filters(r, tech_keys, fund_keys)
        ]

    filtered = [r for r in rows if _passes_filter(r, report)]
    page_rows, total = _apply_sort_paginate(
        filtered,
        report,
        sort_key,
        sort_dir,
        page,
        page_size,
    )

    stale: list[StaleTicker] = []
    seen: set[str] = set()
    # Stale chips scoped to post-bundle-filter rows: a stale
    # ticker that doesn't match the user's filter is irrelevant
    # noise, not a transparency obligation.
    for r in rows:
        if r.ticker in seen:
            continue
        chip = _stale_for_row(r)
        if chip is not None:
            stale.append(chip)
            seen.add(r.ticker)

    body = AdvancedReportResponse(
        rows=page_rows,
        total=total,
        page=page,
        page_size=page_size,
        stale_tickers=stale,
    )
    payload = body.model_dump_json()
    cache.set(inner_ck, payload, ttl=TTL_STABLE)
    return Response(content=payload, media_type="application/json")


# ---------------------------------------------------------------
# CSV export helpers
# ---------------------------------------------------------------

# Hard cap — patched in tests; protects backend memory + browser
# CSV parser. ~10k rows × ~50 columns ≈ 5 MB CSV.
_MAX_EXPORT_ROWS = 10_000


# CSV column header labels — mirrors columnCatalogs.ts UI labels
# but lives here so the backend can format header rows without
# pulling the frontend allowlist into Python. Source of truth for
# CSV header text.
_CSV_COLUMN_LABELS: dict[str, str] = {
    "ticker": "Ticker",
    "company_name": "Company",
    "sector": "Sector",
    "sub_sector": "Sub-sector",
    "avg_emv_score": "Avg EMV Score",
    "avg_14d_emv": "Avg 14d EMV",
    "pscore": "P-Score",
    "rsi": "RSI",
    "sma_50": "SMA 50",
    "sma_200": "SMA 200",
    "golden_cross_days_ago": "Golden Cross (d ago)",
    "today_ltp": "Today LTP",
    "prev_day_ltp": "Prev LTP",
    "prev_2_prev_day_ltp": "Prev-2 LTP",
    "current_ppc": "Current PPC %",
    "avg_10d_ppc": "Avg 10d PPC %",
    "avg_20d_ppc": "Avg 20d PPC %",
    "week_52_high": "52w High",
    "week_52_low": "52w Low",
    "away_from_52week_high": "Away from 52w High %",
    "today_vol": "Today Vol",
    "prev_day_vol": "Prev Vol",
    "avg_10d_vol": "Avg 10d Vol",
    "avg_20d_vol": "Avg 20d Vol",
    "today_x_vol": "Today × Vol",
    "prev_day_x_vol": "Prev × Vol",
    "x_vol_10d": "× Vol 10d",
    "x_vol_20d": "× Vol 20d",
    "today_dv": "Today Deliv Qty",
    "prev_day_dv": "Prev Deliv Qty",
    "avg_10d_dv": "Avg 10d Deliv Qty",
    "avg_20d_dv": "Avg 20d Deliv Qty",
    "today_dpc": "Today Deliv %",
    "prev_day_dpc": "Prev Deliv %",
    "avg_10d_dpc": "Avg 10d Deliv %",
    "avg_20d_dpc": "Avg 20d Deliv %",
    "today_x_dv": "Today × Deliv",
    "prev_day_x_dv": "Prev × Deliv",
    "x_dv_10d": "× Deliv 10d",
    "x_dv_20d": "× Deliv 20d",
    "current_dpc": "Current Deliv %",
    "today_not": "Today Notional",
    "avg_10d_not": "Avg 10d Notional",
    "avg_20d_not": "Avg 20d Notional",
    "debt_to_eq": "Debt/Eq",
    "yoy_qtr_prft": "YoY Qtr Profit %",
    "yoy_qtr_sales": "YoY Qtr Sales %",
    "sales_growth_3yrs": "Sales 3y %",
    "prft_growth_3yrs": "Profit 3y %",
    "sales_growth_5yrs": "Sales 5y %",
    "prft_growth_5yrs": "Profit 5y %",
    "roce": "ROCE %",
    "chng_in_prom_hld": "Δ Promoter %",
    "pledged": "Pledged %",
    "prom_hld": "Promoter %",
    "event": "Event",
    "event_date": "Event Date",
}


def _validate_columns(raw: str) -> list[str]:
    """Validate ``columns=`` param. Empty → safe defaults."""
    if not raw.strip():
        return ["ticker", "today_ltp", "sma_50", "sma_200", "rsi"]
    cols: list[str] = []
    seen: set[str] = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok not in _CSV_COLUMN_LABELS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown column: {tok}",
            )
        if tok in seen:
            continue
        seen.add(tok)
        cols.append(tok)
    if "ticker" not in seen:
        cols.insert(0, "ticker")
    return cols


def _format_csv_cell(value) -> str:  # type: ignore[no-untyped-def]
    """Stable CSV cell rendering: None/NaN → empty, floats → 4dp."""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _csv_response(
    payload: str,
    report: ReportName,
    as_of: date,
) -> StreamingResponse:
    fname = f"advanced-analytics-{report}-{as_of.strftime('%Y%m%d')}.csv"

    def _gen():
        # Stream in 64 KB chunks so very large CSVs don't
        # block the event loop while serialising.
        chunk = 64 * 1024
        for i in range(0, len(payload), chunk):
            yield payload[i : i + chunk]

    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (f'attachment; filename="{fname}"'),
        },
    )


async def _stream_export(
    user: UserContext,
    report: ReportName,
    sort_key: str | None,
    sort_dir: str,
    market: MarketFilter,
    ticker_type: TickerTypeFilter,
    search: str,
    tech: str,
    fund: str,
    columns: str,
) -> StreamingResponse:
    """Build the filtered, sorted full-set list and stream as CSV."""
    cache = get_cache()
    needle = search.strip().upper()
    tech_keys = parse_filter_csv(tech, TECH_KEYS, "tech")
    fund_keys = parse_filter_csv(fund, FUND_KEYS, "fund")
    cols = _validate_columns(columns)
    as_of = _effective_trading_date()
    # Cache key includes raw sort_key (before validation) so
    # requests differing only in sort_key / sort_dir get distinct
    # cache slots. Mirrors _compute_report key structure (§5.13).
    ck = (
        f"cache:advanced_analytics:{report}:{user.user_id}"
        f":m{market}:t{ticker_type}:q{needle}"
        f":ftech{','.join(tech_keys)}"
        f":ffund{','.join(fund_keys)}"
        f":s{sort_key or 'default'}:{sort_dir}"
        f":dt{as_of.isoformat()}:export:{','.join(cols)}"
    )
    hit = cache.get(ck)
    if hit is not None:
        return _csv_response(hit, report, as_of)

    full_rows = await _cached_full_rows(user, as_of)
    keep = set(
        _filter_tickers(
            [r.ticker for r in full_rows],
            market,
            ticker_type,
        )
    )
    rows = [r for r in full_rows if r.ticker in keep]
    if needle:
        rows = [r for r in rows if needle in r.ticker.upper()]
    if tech_keys or fund_keys:
        rows = [
            r for r in rows if passes_bundle_filters(r, tech_keys, fund_keys)
        ]
    rows = [r for r in rows if _passes_filter(r, report)]

    # Validate sort_key against the model; fall back to the
    # report's default if missing. Mirrors _apply_sort_paginate
    # so the export and paginated views share the same sort.
    if sort_key and sort_key not in AdvancedRow.model_fields:
        sort_key = None
    use_key, use_dir = _DEFAULT_SORT[report]
    if sort_key:
        use_key = sort_key
        use_dir = sort_dir
    reverse = use_dir == "desc"
    rows.sort(
        key=lambda r: (
            getattr(r, use_key) is None,
            getattr(r, use_key) or 0,
        ),
        reverse=reverse,
    )

    if report == "top-50-delivery-by-qty":
        rows = rows[:50]

    if len(rows) > _MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Export exceeds {_MAX_EXPORT_ROWS:,} rows; "
                "tighten filters."
            ),
        )

    buf = StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([_CSV_COLUMN_LABELS[c] for c in cols])
    for row in rows:
        writer.writerow([_format_csv_cell(getattr(row, c)) for c in cols])
    payload = buf.getvalue()
    try:
        cache.set(ck, payload, ttl=TTL_STABLE)
    except Exception:  # pragma: no cover — defensive
        _logger.warning(
            "advanced_analytics export-cache set failed",
            exc_info=True,
        )
    return _csv_response(payload, report, as_of)


# ---------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------


def create_advanced_analytics_router() -> APIRouter:
    """Build the ``/advanced-analytics`` router (pro+superuser)."""
    router = APIRouter(
        prefix="/advanced-analytics",
        tags=["advanced-analytics"],
    )

    def _make_endpoint(report: ReportName):
        async def _handler(
            user: UserContext = Depends(pro_or_superuser),
            page: int = Query(1, ge=1),
            page_size: int = Query(25, ge=1, le=200),
            sort_key: str | None = Query(None),
            sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
            market: str = Query("all", pattern="^(all|india|us)$"),
            ticker_type: str = Query("all", pattern="^(all|stock|etf)$"),
            search: str = Query("", max_length=20),
            tech: str = Query(
                "",
                max_length=200,
                pattern="^[a-z0-9_,]*$",
            ),
            fund: str = Query(
                "",
                max_length=200,
                pattern="^[a-z0-9_,]*$",
            ),
        ) -> Response:
            try:
                return await _compute_report(
                    user,
                    report,
                    page,
                    page_size,
                    sort_key,
                    sort_dir,
                    market,  # type: ignore[arg-type]
                    ticker_type,  # type: ignore[arg-type]
                    search,
                    tech,
                    fund,
                )
            except HTTPException:
                raise
            except Exception as exc:
                _logger.exception(
                    "advanced_analytics %s failed: %s",
                    report,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"advanced_analytics {report} failed",
                )

        _handler.__name__ = f"get_{report.replace('-', '_')}"
        return _handler

    for report in REPORTS:
        router.add_api_route(
            path=f"/{report}",
            endpoint=_make_endpoint(report),
            methods=["GET"],
            response_model=AdvancedReportResponse,
            name=f"advanced_analytics_{report.replace('-', '_')}",
        )

    def _make_export_endpoint(report: ReportName):
        async def _handler(
            user: UserContext = Depends(pro_or_superuser),
            sort_key: str | None = Query(None),
            sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
            market: str = Query("all", pattern="^(all|india|us)$"),
            ticker_type: str = Query("all", pattern="^(all|stock|etf)$"),
            search: str = Query("", max_length=20),
            tech: str = Query(
                "",
                max_length=200,
                pattern="^[a-z0-9_,]*$",
            ),
            fund: str = Query(
                "",
                max_length=200,
                pattern="^[a-z0-9_,]*$",
            ),
            columns: str = Query(
                "",
                max_length=2000,
                pattern="^[a-z0-9_,]*$",
            ),
            # Reserved for future json/xlsx export formats; today only csv.
            fmt: str = Query("csv", pattern="^(csv)$"),
        ) -> StreamingResponse:
            try:
                return await _stream_export(
                    user,
                    report,
                    sort_key,
                    sort_dir,
                    market,  # type: ignore[arg-type]
                    ticker_type,  # type: ignore[arg-type]
                    search,
                    tech,
                    fund,
                    columns,
                )
            except HTTPException:
                raise
            except Exception as exc:
                _logger.exception(
                    "advanced_analytics %s export failed: %s",
                    report,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail=(f"advanced_analytics {report} export failed"),
                )

        _handler.__name__ = f"export_{report.replace('-', '_')}"
        return _handler

    for report in REPORTS:
        router.add_api_route(
            path=f"/{report}/export",
            endpoint=_make_export_endpoint(report),
            methods=["GET"],
            name=(f"advanced_analytics_" f"{report.replace('-', '_')}_export"),
        )

    return router
