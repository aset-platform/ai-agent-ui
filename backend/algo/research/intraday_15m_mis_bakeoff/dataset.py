"""Iceberg → pandas research frame for the intraday 15m bake-off.

Pulls from ``stocks.intraday_features`` (EAV) and ``stocks.intraday_bars``,
pivots to wide, applies the spec §4.2 filter chain, joins
``stocks.regime_history`` as a daily overlay. Returns a single
pandas frame ready for the labeler.

Spec §4.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

_logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _load_features_eav(
    *,
    tickers: list[str],
    date_min: date,
    date_max: date,
    interval_sec: int = 900,
) -> pd.DataFrame:
    """EAV rows from ``stocks.intraday_features``."""
    from backend.db.duckdb_engine import query_iceberg_df

    placeholders = ",".join([f"'{t}'" for t in tickers])
    sql = (
        "SELECT ticker, bar_open_ts_ns, bar_date, interval_sec, "
        "feature_name, feature_value, feature_set_version "
        "FROM intraday_features "
        f"WHERE ticker IN ({placeholders}) "
        f"  AND interval_sec = {interval_sec} "
        f"  AND CAST(bar_date AS DATE) BETWEEN DATE '{date_min}'"
        f" AND DATE '{date_max}' "
        "  AND feature_set_version = "
        "    (SELECT MAX(feature_set_version) FROM intraday_features)"
    )
    return query_iceberg_df("stocks.intraday_features", sql)


def _load_bars(
    *,
    tickers: list[str],
    date_min: date,
    date_max: date,
    interval_sec: int = 900,
) -> pd.DataFrame:
    """OHLCV from ``stocks.intraday_bars``."""
    from backend.db.duckdb_engine import query_iceberg_df

    placeholders = ",".join([f"'{t}'" for t in tickers])
    sql = (
        "SELECT ticker, bar_open_ts_ns, bar_date, interval_sec, "
        "open, high, low, close, volume "
        "FROM intraday_bars "
        f"WHERE ticker IN ({placeholders}) "
        f"  AND interval_sec = {interval_sec} "
        f"  AND CAST(bar_date AS DATE) BETWEEN DATE '{date_min}'"
        f" AND DATE '{date_max}' "
    )
    return query_iceberg_df("stocks.intraday_bars", sql)


def _load_regime_overlay(date_min: date, date_max: date) -> pd.DataFrame:
    from backend.db.duckdb_engine import query_iceberg_df

    sql = (
        "SELECT bar_date, regime_label "
        "FROM regime_history "
        f"WHERE CAST(bar_date AS DATE) BETWEEN DATE '{date_min}'"
        f" AND DATE '{date_max}'"
    )
    return query_iceberg_df("stocks.regime_history", sql)


def _is_in_session(ts_ns: int) -> bool:
    """09:15 IST <= bar_open_ts < 15:00 IST."""
    ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(_IST)
    return time(9, 15) <= ts.time() < time(15, 0)


def _session_mask(ts_ns_series: pd.Series) -> pd.Series:
    """Boolean mask: True if bar_open_ts is within 09:15-15:00 IST.

    Vectorized — operates on the whole series at once.
    """
    dt = pd.to_datetime(ts_ns_series, utc=True).dt.tz_convert(_IST)
    t = dt.dt.time
    return (t >= time(9, 15)) & (t < time(15, 0))


def _drop_warmup(df: pd.DataFrame, n_bars: int = 8) -> pd.DataFrame:
    """Drop the first *n_bars* of each (ticker, bar_date).

    Spec §4.2 #5 — VWAP/ORB stability.
    """
    if n_bars <= 0:
        return df
    df = df.sort_values(["ticker", "bar_open_ts_ns"])
    rank = df.groupby(["ticker", "bar_date"]).cumcount()
    return df[rank >= n_bars].copy()


def load_research_frame(
    *,
    tickers: list[str],
    date_min: date,
    date_max: date,
    enforce_session_hours: bool = True,
    drop_warmup_bars: int = 8,
) -> pd.DataFrame:
    """Build the wide research frame for the bake-off.

    Returns a pandas frame with one row per ``(ticker, bar_open_ts_ns)``,
    feature columns from the EAV pivot, OHLCV from ``stocks.intraday_bars``,
    and ``regime_label`` joined from ``stocks.regime_history``.
    """
    eav = _load_features_eav(
        tickers=tickers,
        date_min=date_min,
        date_max=date_max,
    )
    bars = _load_bars(
        tickers=tickers,
        date_min=date_min,
        date_max=date_max,
    )

    if eav.empty or bars.empty:
        return pd.DataFrame()

    wide = eav.pivot_table(
        index=["ticker", "bar_open_ts_ns", "bar_date", "interval_sec"],
        columns="feature_name",
        values="feature_value",
        aggfunc="first",
    ).reset_index()
    wide.columns.name = None

    df = wide.merge(
        bars[
            [
                "ticker",
                "bar_open_ts_ns",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        ],
        on=["ticker", "bar_open_ts_ns"],
        how="inner",
    )

    if enforce_session_hours:
        df = df[_session_mask(df["bar_open_ts_ns"])].copy()
    df = _drop_warmup(df, n_bars=drop_warmup_bars)

    # bar_date comes back as VARCHAR (pandas StringDtype or object) from
    # the Iceberg/DuckDB scan; coerce to Python date so downstream
    # comparisons (e.g. <= date(...)) work without surprises.
    if "bar_date" in df.columns and pd.api.types.is_string_dtype(
        df["bar_date"]
    ):
        df["bar_date"] = pd.to_datetime(
            df["bar_date"], format="%Y-%m-%d"
        ).dt.date

    try:
        regime = _load_regime_overlay(date_min, date_max)
        if not regime.empty:
            if "bar_date" in regime.columns and pd.api.types.is_string_dtype(
                regime["bar_date"]
            ):
                regime["bar_date"] = pd.to_datetime(
                    regime["bar_date"], format="%Y-%m-%d"
                ).dt.date
            df = df.merge(
                regime,
                on="bar_date",
                how="left",
                suffixes=("", "_regime"),
            )
    except Exception:
        _logger.warning(
            "regime overlay load failed — proceeding without",
            exc_info=True,
        )

    return df.reset_index(drop=True)
