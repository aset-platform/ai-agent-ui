"""Build a replay JSONL fixture from a user's holdings + watchlist,
targeting dates where the strategy entry AST fires — so a paper
*replay* run produces fills on the user's own universe.

Drift-free: the trigger scan evaluates the SAME strategy AST
(``Evaluator.eval_node``) against features assembled by the SAME
``assemble_per_bar_features`` the paper runtime uses at
``_on_bar_close``. A date this scan accepts reproduces in replay.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.algo.backtest.evaluator import EvalContext, Evaluator

_logger = logging.getLogger(__name__)
_EVALUATOR = Evaluator()

_DUMMY_TICKER = "__fixture__"
_DUMMY_DATE = date(2000, 1, 1)


def _entry_fires(cond: dict, features: dict) -> bool:
    """True iff the entry AST ``cond`` is truthy against ``features``.

    A missing feature or any eval error means 'does not fire'.
    """
    try:
        ctx = EvalContext(
            ticker=_DUMMY_TICKER,
            bar_date=_DUMMY_DATE,
            features=features,
            open_qty=0,
        )
        return bool(_EVALUATOR.eval_node(cond, ctx))
    except (KeyError, ValueError, TypeError):
        return False


def _utc_midnight_ns(d: date) -> int:
    """UTC midnight of ``d`` in nanoseconds — matches
    ``daily_features_daily_compute._utc_midnight_ns``."""
    return int(
        datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
        .timestamp()
        * 1_000_000_000
    )


def _assembled_features_by_date(
    ticker: str,
    start: date,
    end: date,
) -> dict[date, dict[str, Any]]:
    """For each calendar date in ``[start, end]`` that has a daily
    bar in ``stocks.intraday_features``, assemble the SAME
    ``EvalContext.features`` dict that
    ``PaperRuntime._on_bar_close`` would assemble — using the
    identical ``assemble_per_bar_features`` call.

    Feature provenance (mirrors runtime._on_bar_close exactly):
    - ``bar_feats``     — daily panel at ``interval_sec=86400``
                          keyed by ``_utc_midnight_ns(bar_date)``
                          (rsi_2 lives here)
    - ``market_regime`` — ``compute_market_regime`` output
                          keyed by bar_date  (nifty_above_sma200)
    - ``market_trend``  — ``compute_market_trend_strength``
                          keyed by bar_date  (nifty_30d_return_pct)
    - ``factor_row``    — ``get_factors_window`` rows for this
                          ticker; key (ticker, bar_date)
                          (distance_from_sma200 lives here)
    - ``regime_row``    — ``get_regime_history`` rows keyed by
                          bar_date  (stress_prob lives here)
    - ``daily_overlay`` — None (fixture scan is a daily strategy;
                          runtime skips the overlay for 1d cadence)

    Returns an empty dict if any upstream load fails (logged at
    WARNING); the scan will produce zero trigger dates rather
    than raising.
    """
    from backend.algo.backtest.indicators import (
        compute_market_regime,
        compute_market_trend_strength,
    )
    from backend.algo.factors.repo import get_factors_window
    from backend.algo.features import load_intraday_features_window
    from backend.algo.features.per_bar import assemble_per_bar_features
    from backend.algo.regime.repo import get_regime_history

    # Wide warm-up window so SMA-200 and 30d-return are populated
    # at ``start``.  Mirrors the 3-year window the runtime uses.
    wide_start = start - timedelta(days=365 * 3)

    # ── 1. Market regime + trend (NIFTY-derived) ─────────────────
    try:
        market_regime: dict[Any, Decimal] = compute_market_regime(
            wide_start, end,
        )
        market_trend: dict[Any, Decimal] = compute_market_trend_strength(
            wide_start, end,
        )
    except Exception:
        _logger.warning(
            "[fixture_builder] market regime/trend load failed "
            "for ticker=%s — returning empty panel",
            ticker,
            exc_info=True,
        )
        return {}

    # ── 2. Per-ticker factor rows ─────────────────────────────────
    factor_by_date: dict[date, dict[str, Decimal]] = {}
    try:
        factor_rows = get_factors_window(
            [ticker], start - timedelta(days=1), end + timedelta(days=1),
        )
        for r in factor_rows:
            factor_by_date[r.bar_date] = {
                k: Decimal(str(v))
                for k, v in r.values.items()
                if v is not None
            }
    except Exception:
        _logger.warning(
            "[fixture_builder] factor window load failed for "
            "ticker=%s — factor keys absent from features",
            ticker,
            exc_info=True,
        )

    # ── 3. Regime history rows (stress_prob, regime_label) ────────
    regime_by_date: dict[date, dict[str, Any]] = {}
    try:
        rh_rows = get_regime_history(
            start - timedelta(days=1), end + timedelta(days=1),
        )
        for rh in rh_rows:
            entry: dict[str, Any] = {"regime_label": rh.regime_label}
            if rh.stress_prob is not None:
                entry["stress_prob"] = Decimal(str(rh.stress_prob))
            regime_by_date[rh.bar_date] = entry
    except Exception:
        _logger.warning(
            "[fixture_builder] regime_history load failed — "
            "stress_prob absent from features",
            exc_info=True,
        )

    # ── 4. Daily bar features panel (rsi_2 etc.) ──────────────────
    try:
        panel = load_intraday_features_window(
            tickers=[ticker],
            interval_sec=86400,
            period_start=start,
            period_end=end,
            enable_on_demand_backfill=False,
        )
    except Exception:
        _logger.warning(
            "[fixture_builder] daily feature panel load failed "
            "for ticker=%s — returning empty panel",
            ticker,
            exc_info=True,
        )
        return {}

    by_ts = panel.get(ticker, {})

    # ── 5. Assemble one features dict per date ────────────────────
    out: dict[date, dict[str, Any]] = {}
    for bar_date in sorted(by_ts.keys()):
        # Reverse bar_open_ts_ns → calendar date (UTC midnight).
        bar_date_obj = datetime.fromtimestamp(
            bar_date / 1_000_000_000, tz=timezone.utc,
        ).date()
        bar_feats = by_ts[bar_date]
        features = assemble_per_bar_features(
            bar_feats=bar_feats,
            market_regime=market_regime.get(bar_date_obj),
            market_trend=market_trend.get(bar_date_obj),
            factor_row=factor_by_date.get(bar_date_obj),
            regime_row=regime_by_date.get(bar_date_obj),
            daily_overlay=None,  # 1d strategy — no cross-cadence overlay
        )
        out[bar_date_obj] = features

    return out


def _scan_trigger_dates(
    ticker: str,
    entry_cond: dict,
    start: date,
    end: date,
    *,
    max_dates: int,
) -> list[date]:
    """Return the most-recent ``max_dates`` calendar dates in
    ``[start, end]`` where ``entry_cond`` fires against the
    assembled feature dict for ``ticker``.

    Delegates feature assembly to :func:`_assembled_features_by_date`
    (the integration point) so tests can monkeypatch it cleanly.
    """
    by_date = _assembled_features_by_date(ticker, start, end)
    hits = [
        dt for dt, feats in sorted(by_date.items())
        if _entry_fires(entry_cond, feats)
    ]
    return hits[-max_dates:]


@dataclass
class FixtureBuildResult:
    filename: str
    n_tickers: int
    n_trigger_dates: int
    n_ticks: int
    trigger_tickers: list[str]
