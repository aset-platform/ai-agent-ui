"""Daily regime classifier orchestrator — runs at 22:30 IST.

Reads:
  * NIFTY 50 close (^NSEI) — last ~252 trading days from
    ``stocks.ohlcv``
  * India VIX close (^INDIAVIX) — last bar
  * pct_above_50sma — computed from the full-universe daily close

Computes the rule-based label + HMM stress_prob; persists one row
to ``stocks.regime_history``. On NaN/missing inputs it falls back
to ``SIDEWAYS`` and emits a ``regime_classifier_degraded`` warning.

The ``@register_job("regime_classifier_daily")`` wrapper lives in
``backend/jobs/executor.py`` (matching the rest of the algo job
pattern); this module owns only the orchestrator + helpers.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from backend.algo.regime.hmm_overlay import StressHMM
from backend.algo.regime.repo import (
    RegimeRow,
    upsert_regime_history,
)
from backend.algo.regime.rule_based import classify_regime
from db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

CLASSIFIER_VERSION = "v1.0"
NIFTY_TICKER = "^NSEI"
VIX_TICKER = "^INDIAVIX"
BREADTH_UNIVERSE_LOOKBACK_DAYS = 200


def _load_nifty_window(
    as_of: date, lookback_days: int,
) -> pd.DataFrame:
    start = as_of - timedelta(days=lookback_days + 30)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT date AS bar_date, close FROM ohlcv "
        "WHERE ticker = ? AND date BETWEEN ? AND ? "
        "ORDER BY date ASC",
        [NIFTY_TICKER, start, as_of],
    )
    return pd.DataFrame(rows)


def _load_vix_latest(as_of: date) -> pd.DataFrame:
    start = as_of - timedelta(days=10)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT date AS bar_date, close FROM ohlcv "
        "WHERE ticker = ? AND date BETWEEN ? AND ? "
        "ORDER BY date DESC LIMIT 1",
        [VIX_TICKER, start, as_of],
    )
    return pd.DataFrame(rows)


def _compute_breadth_pct_50sma(as_of: date) -> Decimal:
    """% of stocks (full universe) trading above their 50-day SMA.
    Uses the existing ``stocks.ohlcv`` table; one window scan."""
    start = as_of - timedelta(days=BREADTH_UNIVERSE_LOOKBACK_DAYS)
    try:
        rows = query_iceberg_table(
            "stocks.ohlcv",
            "WITH w AS ("
            "  SELECT ticker, date AS bar_date, close, "
            "         AVG(close) OVER ("
            "             PARTITION BY ticker ORDER BY date "
            "             ROWS BETWEEN 49 PRECEDING "
            "             AND CURRENT ROW"
            "         ) AS sma50 "
            "  FROM ohlcv WHERE date BETWEEN ? AND ? "
            ") "
            "SELECT COUNT(*) FILTER (WHERE close > sma50) AS above, "
            "       COUNT(*) AS total "
            "FROM w WHERE bar_date = ?",
            [start, as_of, as_of],
        )
    except Exception as exc:
        _logger.warning(
            "breadth pct_above_50sma scan failed: %s", exc,
        )
        return Decimal("NaN")
    if not rows:
        return Decimal("NaN")
    r = rows[0]
    if not r.get("total"):
        return Decimal("NaN")
    return Decimal(r["above"]) / Decimal(r["total"])


def _compute_inputs(
    as_of: date,
    nifty_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    pct_above_50sma: Decimal,
) -> dict:
    if nifty_df.empty:
        raise RuntimeError(
            f"No NIFTY history available up to {as_of}",
        )
    nifty_df = nifty_df.sort_values(
        "bar_date",
    ).reset_index(drop=True)
    closes = nifty_df["close"].astype(float)
    last_close = closes.iloc[-1]
    sma200 = (
        closes.tail(200).mean()
        if len(closes) >= 200
        else float("nan")
    )
    ret_30d = (
        last_close / closes.iloc[-31] - 1
        if len(closes) >= 31 else float("nan")
    )
    ret_60d = (
        last_close / closes.iloc[-61] - 1
        if len(closes) >= 61 else float("nan")
    )
    vix_close = (
        Decimal(str(float(vix_df["close"].iloc[0])))
        if not vix_df.empty else Decimal("NaN")
    )
    return {
        "nifty_close": Decimal(str(last_close)),
        "nifty_sma200": (
            Decimal(str(sma200)) if not math.isnan(sma200)
            else Decimal("NaN")
        ),
        "vix_close": vix_close,
        "nifty_ret_30d": (
            Decimal(str(ret_30d)) if not math.isnan(ret_30d)
            else Decimal("NaN")
        ),
        "nifty_ret_60d": (
            Decimal(str(ret_60d)) if not math.isnan(ret_60d)
            else Decimal("NaN")
        ),
        "pct_above_50sma": pct_above_50sma,
    }


def _safe_classify(inputs: dict) -> tuple[str, bool]:
    """Returns ``(label, degraded)``. ``degraded=True`` when the
    fallback to ``SIDEWAYS`` was triggered by NaN/missing inputs.
    """
    try:
        label = classify_regime(
            nifty_close=inputs["nifty_close"],
            nifty_sma200=inputs["nifty_sma200"],
            vix_close=inputs["vix_close"],
            nifty_ret_30d=inputs["nifty_ret_30d"],
            nifty_ret_60d=inputs["nifty_ret_60d"],
            pct_above_50sma=inputs["pct_above_50sma"],
        )
        return label, False
    except ValueError as exc:
        _logger.warning(
            "regime_classifier_degraded: %s — "
            "falling back to SIDEWAYS",
            exc,
        )
        return "SIDEWAYS", True


def _compute_stress_prob(nifty_df: pd.DataFrame) -> float | None:
    """Build (log_return, realized_vol_20d) features and ask the
    persisted HMM for the last-bar stress posterior. Cold start:
    fits on all available history, then persists. Returns ``None``
    when there isn't enough history."""
    if len(nifty_df) < 100:
        return None
    closes = nifty_df["close"].astype(float).to_numpy()
    log_ret = np.diff(np.log(closes))
    if log_ret.size < 60:
        return None
    rv = pd.Series(log_ret).rolling(20).std(
        ddof=0,
    ).bfill().to_numpy()
    X = np.column_stack([log_ret, rv])

    hmm = StressHMM.load()
    if hmm is None:
        if X.shape[0] < 200:
            return None
        hmm = StressHMM()
        last_bar = pd.to_datetime(
            nifty_df["bar_date"].iloc[-1],
        ).date()
        hmm.fit(X, trained_through=last_bar)
        hmm.save()
    return hmm.stress_prob(X)


def run_classifier(as_of: date | None = None) -> RegimeRow:
    """Compute today's regime, persist, return the row."""
    if as_of is None:
        as_of = date.today()
    nifty_df = _load_nifty_window(as_of, 252)
    vix_df = _load_vix_latest(as_of)
    breadth = _compute_breadth_pct_50sma(as_of)
    inputs = _compute_inputs(as_of, nifty_df, vix_df, breadth)
    label, degraded = _safe_classify(inputs)
    stress = (
        _compute_stress_prob(nifty_df) if not degraded else None
    )

    inputs_serializable: dict = {}
    for k, v in inputs.items():
        try:
            inputs_serializable[k] = float(v)
        except (ValueError, TypeError):
            inputs_serializable[k] = None
    inputs_serializable["degraded"] = degraded
    row = RegimeRow(
        bar_date=as_of,
        regime_label=label,
        stress_prob=stress,
        rule_inputs=inputs_serializable,
        classifier_version=CLASSIFIER_VERSION,
    )
    upsert_regime_history([row])
    _logger.info(
        "regime_classifier: as_of=%s label=%s "
        "stress=%s degraded=%s",
        as_of, label, stress, degraded,
    )
    return row


def run_classifier_job(payload: dict) -> dict:
    """Scheduler entry-point. Payload optionally carries
    ``as_of`` (ISO date string) for backfill; defaults to today."""
    as_of_raw = payload.get("as_of") if payload else None
    parsed = (
        date.fromisoformat(as_of_raw) if as_of_raw else None
    )
    row = run_classifier(as_of=parsed)
    return {
        "as_of": str(row.bar_date),
        "regime_label": row.regime_label,
        "stress_prob": row.stress_prob,
    }
