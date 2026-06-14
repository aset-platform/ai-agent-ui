"""Build a replay JSONL fixture from a user's holdings + watchlist,
targeting dates where the strategy entry AST fires — so a paper
*replay* run produces fills on the user's own universe.

Drift-free by construction. The paper runtime feeds a **daily**
strategy from a bar history it accumulates out of the replayed ticks
(``runtime._on_bar_close`` → ``compute_indicators(self._bars_by_ticker)``),
NOT from the Iceberg daily-feature panel. Our synthetic bars are
*flat* (every tick carries ``ltp = that day's close``), so the
runtime's ``rsi_2`` is purely ``wilder_rsi`` over the daily close
series. This builder therefore selects trigger dates by computing
``rsi_2`` the SAME way — ``compute_indicators`` over the SAME flat
close series — combined with the SAME date-keyed factor / regime /
market context the runtime assembles via
``assemble_per_bar_features``. A date this scan accepts reproduces
the identical entry decision in replay.

For ``rsi_2`` (and ``sma_200``) to be settled at the earliest
trigger date, the fixture emits a **dense** daily-bar series — one
bar per real trading day — across a warm-up window
(``_WARMUP_DAYS``) before the earliest trigger. ``distance_from_sma200``,
``stress_prob`` and the NIFTY gates are date-keyed (factor / regime /
market panels) and need no history.
"""
from __future__ import annotations

import json
import logging
import math
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

# Dense warm-up emitted before the earliest trigger date so the
# runtime's recomputed rsi_2 / sma_200 are settled at trigger time.
# rsi_2 (Wilder RSI(2)) converges within float epsilon in ~40 trading
# days; 420 calendar days is a comfortable SMA200 superset. Tunable.
_WARMUP_DAYS = 420


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


# ── Dense OHLCV read ──────────────────────────────────────────────

def _dense_closes(
    tickers: list[str],
    start: date,
    end: date,
) -> dict[str, list[tuple[date, float, int]]]:
    """Return ``{ticker: [(date, close, volume), ...]}`` ascending by
    date, over ``[start, end]``, from ONE scoped Iceberg read
    (CLAUDE §4.1 — batch reads, no per-(ticker, date) loop, no full
    scan). NaN closes are dropped.

    Returns an empty dict on read failure (logged at WARNING) so the
    build degrades to zero triggers rather than raising.
    """
    from backend.db.duckdb_engine import query_iceberg_df

    out: dict[str, list[tuple[date, float, int]]] = {}
    if not tickers:
        return out
    placeholders = ", ".join("?" for _ in tickers)
    sql = (
        "SELECT ticker, date, close, volume FROM ohlcv"
        f" WHERE ticker IN ({placeholders})"
        " AND date BETWEEN ? AND ?"
        " ORDER BY ticker, date"
    )
    params: list[Any] = [*tickers, start, end]
    try:
        df = query_iceberg_df("stocks.ohlcv", sql, params)
    except Exception:
        _logger.warning(
            "[fixture_builder] dense OHLCV read failed for %d tickers",
            len(tickers),
            exc_info=True,
        )
        return out
    if df.empty:
        return out
    df = df.dropna(subset=["close"])
    for tk, grp in df.groupby("ticker"):
        rows: list[tuple[date, float, int]] = []
        for _, r in grp.iterrows():
            d = r["date"]
            d = d.date() if hasattr(d, "date") else d
            vol = r["volume"]
            volume = (
                int(vol)
                if vol is not None
                and not (isinstance(vol, float) and math.isnan(vol))
                else 0
            )
            rows.append((d, float(r["close"]), volume))
        rows.sort(key=lambda x: x[0])
        out[str(tk)] = rows
    return out


# ── Date-keyed context (factor / regime / market) ─────────────────

def _load_market_panels(
    scan_start: date,
    end: date,
) -> tuple[dict[Any, Decimal], dict[Any, Decimal]]:
    """Return ``(market_regime, market_trend)`` dicts keyed by date.

    Mirrors the wide warm-up window so NIFTY SMA200 / 30d-return are
    populated at ``scan_start``. Loaded once for the whole build.
    """
    from backend.algo.backtest.indicators import (
        compute_market_regime,
        compute_market_trend_strength,
    )

    wide_start = scan_start - timedelta(days=365 * 3)
    try:
        return (
            compute_market_regime(wide_start, end),
            compute_market_trend_strength(wide_start, end),
        )
    except Exception:
        _logger.warning(
            "[fixture_builder] market regime/trend load failed — "
            "NIFTY gate keys will be absent",
            exc_info=True,
        )
        return {}, {}


def _load_factor_by_ticker(
    tickers: list[str],
    scan_start: date,
    end: date,
) -> dict[str, dict[date, dict[str, Decimal]]]:
    """Return ``{ticker: {bar_date: {factor_key: Decimal}}}`` from one
    batched ``get_factors_window`` call (distance_from_sma200 etc.)."""
    from backend.algo.factors.repo import get_factors_window

    out: dict[str, dict[date, dict[str, Decimal]]] = {}
    try:
        rows = get_factors_window(
            tickers,
            scan_start - timedelta(days=1),
            end + timedelta(days=1),
        )
    except Exception:
        _logger.warning(
            "[fixture_builder] factor window load failed — factor "
            "keys absent from features",
            exc_info=True,
        )
        return out
    for r in rows:
        out.setdefault(r.ticker, {})[r.bar_date] = {
            k: Decimal(str(v))
            for k, v in r.values.items()
            if v is not None
        }
    return out


def _load_regime_by_date(
    scan_start: date,
    end: date,
) -> dict[date, dict[str, Any]]:
    """Return ``{bar_date: {regime_label, stress_prob}}`` from
    ``get_regime_history`` (stress_prob gate)."""
    from backend.algo.regime.repo import get_regime_history

    out: dict[date, dict[str, Any]] = {}
    try:
        rows = get_regime_history(
            scan_start - timedelta(days=1),
            end + timedelta(days=1),
        )
    except Exception:
        _logger.warning(
            "[fixture_builder] regime_history load failed — "
            "stress_prob absent from features",
            exc_info=True,
        )
        return out
    for rh in rows:
        entry: dict[str, Any] = {"regime_label": rh.regime_label}
        if rh.stress_prob is not None:
            entry["stress_prob"] = Decimal(str(rh.stress_prob))
        out[rh.bar_date] = entry
    return out


# ── Per-ticker feature assembly (runtime-faithful) ────────────────

def _history_from_closes(
    ticker: str,
    rows: list[tuple[date, float, int]],
) -> list[Any]:
    """Build a flat ``BarData`` history (open=high=low=close) from the
    dense close series — identical in shape to the bars the runtime
    resamples from this fixture, so ``compute_indicators`` yields the
    same rsi_2.
    """
    from backend.algo.backtest.types import BarData

    history: list[Any] = []
    for d, close, volume in rows:
        c = Decimal(str(close))
        history.append(
            BarData(
                ticker=ticker,
                date=d,
                open=c,
                high=c,
                low=c,
                close=c,
                volume=int(volume),
            )
        )
    return history


def _features_by_date(
    history: list[Any],
    *,
    market_regime: dict[Any, Decimal],
    market_trend: dict[Any, Decimal],
    factor_by_date: dict[date, dict[str, Decimal]],
    regime_by_date: dict[date, dict[str, Any]],
) -> dict[date, dict[str, Any]]:
    """Assemble the SAME ``EvalContext.features`` dict per date that
    ``PaperRuntime._on_bar_close`` builds — ``rsi_2`` from
    ``compute_indicators(history)`` (the runtime's path), the rest
    from date-keyed panels, ``daily_overlay=None`` (1d cadence).
    """
    from backend.algo.backtest.indicators import compute_indicators
    from backend.algo.features.per_bar import assemble_per_bar_features

    if not history:
        return {}
    ind_map = compute_indicators(history)
    out: dict[date, dict[str, Any]] = {}
    for bar_date_obj, bar_feats in ind_map.items():
        out[bar_date_obj] = assemble_per_bar_features(
            bar_feats=bar_feats,
            market_regime=market_regime.get(bar_date_obj),
            market_trend=market_trend.get(bar_date_obj),
            factor_row=factor_by_date.get(bar_date_obj),
            regime_row=regime_by_date.get(bar_date_obj),
            daily_overlay=None,  # 1d strategy — no cross-cadence overlay
        )
    return out


def _scan_trigger_dates(
    entry_cond: dict,
    features_by_date: dict[date, dict[str, Any]],
    *,
    max_dates: int,
) -> list[date]:
    """Return the most-recent ``max_dates`` dates whose assembled
    feature dict fires ``entry_cond``."""
    hits = [
        dt
        for dt, feats in sorted(features_by_date.items())
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
# 09:15 IST = 03:45 UTC  (offset from midnight UTC); divisible by 60.
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
    """Synthesise 3 ticks that form exactly ONE 1-min bar on ``dt``.

    All ticks carry ``ltp = close`` (flat bar) so the resampled bar's
    OHLC all equal the Iceberg close for ``dt``. Offsets ``(0, 20, 40)``
    sit inside a SINGLE 60-second bucket (session open 03:45 UTC is a
    multiple of 60), so the date yields exactly one bar — the next
    later bar (next trading day, or shutdown flush) rolls it closed.
    Emitting more than one bar per date would inject zero-delta closes
    and corrupt the RSI series.
    """
    base = _utc_midnight_ns(dt) + _SESSION_OPEN_SECS * _NS
    offsets = (0, 20, 40)  # all within [base, base + 60s) → one bar
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

    Uses the canonical v3 template AST. If a user has edited their
    persisted strategy AST (algo.strategies.ast_json), this will not
    reflect those edits — load via the strategy repo (get_strategy)
    if per-user AST fidelity is needed.
    """
    from backend.algo.strategy.templates.loader import load_template

    strategy = load_template("rsi2_connors_daily_v3")
    # strategy.root is the IfNode; .cond is the entry condition
    # type: ignore[union-attr]
    return strategy.root.cond.model_dump(mode="json")


# ── Public entrypoint ─────────────────────────────────────────────

def build_universe_fixture(
    user_id: str,
    *,
    lookback_days: int = 60,
    max_dates_per_ticker: int = 2,
) -> FixtureBuildResult:
    """Build a replay JSONL fixture for *user_id*'s universe.

    Scans the last ``lookback_days`` for dates where the v3 entry
    condition fires (rsi_2 computed the runtime's way over a dense
    flat-close series), then emits a dense daily-bar series per
    qualifying ticker from ``_WARMUP_DAYS`` before its earliest
    trigger through its latest trigger, writes the ticks to
    ``<APP_HOME>/fixtures/<user_id>.jsonl``, and returns a summary.

    Raises:
        HTTPException 400: when the user has no India tickers.
    """
    end = date.today()
    scan_start = end - timedelta(days=lookback_days)
    tickers = _resolve_universe(user_id)
    if not tickers:
        raise HTTPException(
            status_code=400,
            detail=(
                "Add tickers to your watchlist or holdings first."
            ),
        )
    cond = _entry_cond_for_v3()

    # ── ONE scoped dense read: warm-up + scan window ──────────────
    dense_start = scan_start - timedelta(days=_WARMUP_DAYS)
    closes_by_ticker = _dense_closes(tickers, dense_start, end)

    # ── Date-keyed context, loaded once for the whole build ───────
    market_regime, market_trend = _load_market_panels(scan_start, end)
    factor_by_ticker = _load_factor_by_ticker(tickers, scan_start, end)
    regime_by_date = _load_regime_by_date(scan_start, end)

    all_ticks: list[dict] = []
    trigger_tickers: list[str] = []
    n_dates = 0
    for tk in tickers:
        rows = closes_by_ticker.get(tk, [])
        if not rows:
            continue
        history = _history_from_closes(tk, rows)
        feats_by_date = _features_by_date(
            history,
            market_regime=market_regime,
            market_trend=market_trend,
            factor_by_date=factor_by_ticker.get(tk, {}),
            regime_by_date=regime_by_date,
        )
        # Candidate dates: only within the scan window (warm-up dates
        # exist purely to settle rsi_2 / sma_200 — never entries).
        candidate = {
            d: f
            for d, f in feats_by_date.items()
            if scan_start <= d <= end
        }
        triggers = _scan_trigger_dates(
            cond, candidate, max_dates=max_dates_per_ticker,
        )
        if not triggers:
            continue
        latest = max(triggers)

        # Emit dense flat bars from the start of warm-up through this
        # ticker's latest trigger so the runtime reproduces the same
        # rsi_2 at every trigger date.
        for d, close, volume in rows:
            if d > latest:
                break
            if close is None or close <= 0:
                continue
            all_ticks.extend(_synth_ticks(tk, d, close, volume))
        trigger_tickers.append(tk)
        n_dates += len(triggers)

    all_ticks.sort(key=lambda t: t["ts_ns"])
    out = _user_fixtures_dir() / f"{user_id}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        fh.write(
            f"# fixture_builder user={user_id}"
            f" tickers={len(trigger_tickers)}"
            f" trigger_dates={n_dates}"
            f" dense_ticks={len(all_ticks)}\n"
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
