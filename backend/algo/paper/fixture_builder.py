"""Build a replay JSONL fixture from a user's holdings + watchlist,
targeting dates where the strategy entry AST fires — so a paper
*replay* run produces fills on the user's own universe.

Drift-free: the trigger scan evaluates the SAME strategy AST
(``Evaluator.eval_node``) against features assembled by the SAME
``assemble_per_bar_features`` the paper runtime uses at
``_on_bar_close``. A date this scan accepts reproduces in replay.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import HTTPException

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
    # NOTE: rsi_2 from Iceberg is steady-state; a cold replay may
    # miss the first 1-2 dates while RSI(2) warms (3 bars).
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
    for bar_ts_ns in sorted(by_ts.keys()):
        # Reverse bar_open_ts_ns → calendar date (UTC midnight).
        bar_date_obj = datetime.fromtimestamp(
            bar_ts_ns / 1_000_000_000, tz=timezone.utc,
        ).date()
        bar_feats = by_ts[bar_ts_ns]
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


# ── Fixture I/O helpers ───────────────────────────────────────────

_NS = 1_000_000_000
# 09:15 IST = 03:45 UTC  (offset from midnight UTC)
_SESSION_OPEN_SECS = 3 * 3600 + 45 * 60


def _user_fixtures_dir() -> Path:
    """Return (and create) the per-user fixtures directory."""
    from backend.paths import APP_HOME

    d = APP_HOME / "fixtures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _synth_ticks(
    ticker: str,
    dt: date,
    close: float,
    volume: int,
) -> list[dict]:
    """Synthesise 3 ticks that form a complete 1-min bar on ``dt``.

    All ticks carry ``ltp=close`` so the resampled bar's close equals
    the Iceberg OHLCV close for ``dt``.  The last-first span is 90s
    (> 60s) so a 1-min bucketer always closes the bar.  Timestamps are
    in the IST cash-session window (09:15 IST == 03:45 UTC) so the
    resampled bar's date equals ``dt`` under any UTC-aligned bucketer.
    """
    base = _utc_midnight_ns(dt) + _SESSION_OPEN_SECS * _NS
    offsets = (0, 30, 90)   # last-first = 90 s > 60 s → 1-min bar closes
    vol = max(1, int(volume) // len(offsets))
    return [
        {
            "ticker": ticker,
            "ts_ns": base + o * _NS,
            "ltp": float(close),
            "volume": vol,
        }
        for o in offsets
    ]


# ── Universe + strategy helpers ───────────────────────────────────

def _resolve_universe(user_id: str) -> list[str]:
    """Return holdings (qty > 0) ∪ watchlist, India only, deduped, sorted.

    Runs synchronously via the NullPool bridge
    (``stocks.repository._run_pg``) — safe to call from
    ``asyncio.to_thread`` or scheduler threads.
    """
    from backend.db.models.user_ticker import UserTicker
    from sqlalchemy import select
    from stocks.repository import _pg_session, _run_pg

    # ── watchlist (auth.user_tickers) ────────────────────────────
    def _watchlist_call():
        async def _q():
            async with _pg_session() as s:
                result = await s.execute(
                    select(UserTicker.ticker).where(
                        UserTicker.user_id == user_id,
                    )
                )
                return [row[0] for row in result.all()]
        return _q

    try:
        watchlist: list[str] = _run_pg(_watchlist_call())
    except Exception:
        _logger.warning(
            "[fixture_builder] watchlist load failed for user=%s",
            user_id,
            exc_info=True,
        )
        watchlist = []

    # ── holdings (stocks.portfolio_transactions) ──────────────────
    holdings: list[str] = []
    try:
        from tools._stock_shared import _require_repo

        repo = _require_repo()
        holdings_df = repo.get_portfolio_holdings(user_id)
        if not holdings_df.empty:
            mask = holdings_df["quantity"].astype(float) > 0
            holdings = holdings_df.loc[mask, "ticker"].tolist()
    except Exception:
        _logger.warning(
            "[fixture_builder] holdings load failed for user=%s",
            user_id,
            exc_info=True,
        )

    # ── merge, filter India only, dedup, sort ────────────────────
    combined = set(watchlist) | set(holdings)
    india = sorted(
        t for t in combined
        if t.endswith((".NS", ".BO"))
    )
    return india


def _entry_cond_for_v3() -> dict:
    """Return the entry-condition AST node for the v3 template.

    Loads ``rsi2_connors_daily_v3.json`` via the template loader so
    the fixture scan uses exactly the same condition the live strategy
    evaluates.
    """
    from backend.algo.strategy.templates.loader import load_template

    strategy = load_template("rsi2_connors_daily_v3")
    # strategy.root is the IfNode; .cond is the entry condition
    # type: ignore[union-attr]
    return strategy.root.cond.model_dump(mode="json")


def _close_for(
    ticker: str,
    dt: date,
) -> tuple[float | None, int]:
    """Return ``(close, volume)`` for ``ticker`` on ``dt`` from Iceberg.

    Returns ``(None, 0)`` when no row is present.
    """
    from backend.db.duckdb_engine import query_iceberg_df

    _OHLCV = "stocks.ohlcv"
    try:
        df = query_iceberg_df(
            _OHLCV,
            "SELECT close, volume FROM ohlcv"
            " WHERE ticker = ? AND date = ?",
            [ticker, dt],
        )
        if df.empty:
            return (None, 0)
        row = df.dropna(subset=["close"])
        if row.empty:
            return (None, 0)
        return (float(row.iloc[-1]["close"]), int(row.iloc[-1]["volume"] or 0))
    except Exception:
        _logger.warning(
            "[fixture_builder] OHLCV lookup failed "
            "ticker=%s dt=%s",
            ticker,
            dt,
            exc_info=True,
        )
        return (None, 0)


# ── Public entrypoint ─────────────────────────────────────────────

def build_universe_fixture(
    user_id: str,
    *,
    lookback_days: int = 60,
    max_dates_per_ticker: int = 2,
) -> FixtureBuildResult:
    """Build a replay JSONL fixture for *user_id*'s universe.

    Scans the last ``lookback_days`` for dates where the v3 entry
    condition fires against assembled features, synthesises 3 ticks
    per trigger date, writes them to
    ``<APP_HOME>/fixtures/<user_id>.jsonl``, and returns a summary.

    Raises:
        HTTPException 400: when the user has no India tickers.
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    tickers = _resolve_universe(user_id)
    if not tickers:
        raise HTTPException(
            status_code=400,
            detail=(
                "Add tickers to your watchlist or holdings first."
            ),
        )
    cond = _entry_cond_for_v3()
    all_ticks: list[dict] = []
    trigger_tickers: list[str] = []
    n_dates = 0
    for tk in tickers:
        dates = _scan_trigger_dates(
            tk,
            cond,
            start,
            end,
            max_dates=max_dates_per_ticker,
        )
        if not dates:
            continue
        trigger_tickers.append(tk)
        for dt in dates:
            close, volume = _close_for(tk, dt)
            if close is None or close <= 0:
                continue
            n_dates += 1
            all_ticks.extend(_synth_ticks(tk, dt, close, volume))

    all_ticks.sort(key=lambda t: t["ts_ns"])
    out = _user_fixtures_dir() / f"{user_id}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        fh.write(
            f"# fixture_builder user={user_id}"
            f" tickers={len(trigger_tickers)}"
            f" dates={n_dates}\n"
        )
        for t in all_ticks:
            fh.write(json.dumps(t) + "\n")

    return FixtureBuildResult(
        filename=out.name,
        n_tickers=len(tickers),
        n_trigger_dates=n_dates,
        n_ticks=len(all_ticks),
        trigger_tickers=trigger_tickers,
    )
