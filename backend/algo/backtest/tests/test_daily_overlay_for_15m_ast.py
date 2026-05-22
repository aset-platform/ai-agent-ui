"""FE-15b integration: 15m AST resolves daily-cadence features.

Spec §5.3 — ``market_breadth_pct_above_sma200`` and ``stress_prob``
are DAILY-derived features referenced by the MIS Intraday MR v1
15m AST *without* a ``_1d`` suffix.  The feature engine (FE-8 /
FE-9 cohort pass) bakes them directly into the
``stocks.intraday_features`` panel at ``interval_sec=900`` so they
arrive in ``bar_feats`` at evaluation time — no separate daily-
overlay lookup required for these two keys.

In the backtest runner the path is:

    intraday_indicators[ticker][ts_ns]   ← ``bar_feats``
    regime_by_date[bar_date]             ← ``regime_row``
    assemble_per_bar_features(
        bar_feats=bar_feats,
        regime_row=regime_row,
        daily_overlay=lookup_daily_overlay(...)   ← {name}_1d keys
    )
    → EvalContext.features
    → evaluator.eval_node(strategy.root, ctx)

If the feature lookup is broken — e.g. ``stress_prob`` missing from
the panel and not injected via ``regime_row`` — the AST evaluator
raises ``KeyError`` and the runner silently swallows it, emitting
*no* orders on every 15m bar (the "silent-fail" described in the
spec).

These tests are NOT re-implementing FE-15b; they VERIFY the existing
wiring is correct for the MR v1 features.  Downgrade note: the
runner does not expose a public single-bar eval entry point, so we
test the two components that compose it — ``assemble_per_bar_features``
and ``Evaluator.eval_node`` — with the same fixture dict the runner
would build, then run a minimal end-to-end smoke via ``run_backtest``
with all I/O patched.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.features.per_bar import (
    assemble_per_bar_features,
    lookup_daily_overlay,
)
from backend.algo.strategy.ast import parse_strategy

# ── constants ────────────────────────────────────────────────────

_KEY_BREADTH = "market_breadth_pct_above_sma200"
_KEY_STRESS = "stress_prob"
_IST = timezone(timedelta(hours=5, minutes=30))
_TICKER = "RELIANCE.NS"
_BAR_DATE = date(2026, 5, 15)


# ── fixtures ─────────────────────────────────────────────────────


def _load_mr_v1() -> dict:
    """Load the real MIS Intraday MR v1 template from disk."""
    tmpl_path = (
        Path(__file__).parents[3]
        / "algo"
        / "strategy"
        / "templates"
        / "mis_intraday_meanrev_long_v1.json"
    )
    return json.loads(tmpl_path.read_text())


def _ts_ns_for(d: date, hour: int, minute: int) -> int:
    """IST bar-open → UTC ns-since-epoch."""
    dt = datetime(
        d.year, d.month, d.day, hour, minute, tzinfo=_IST
    )
    return int(dt.timestamp() * 1_000_000_000)


def _utc_midnight_ns(d: date) -> int:
    """UTC midnight → ns for daily-panel keys."""
    return int(
        datetime.combine(d, time.min, tzinfo=timezone.utc)
        .timestamp()
        * 1_000_000_000
    )


def _minimal_15m_bar_feats(
    *,
    breadth: Decimal = Decimal("65"),
    stress: Decimal = Decimal("0.18"),
    rsi_5: Decimal = Decimal("22"),
    gap_pct: Decimal = Decimal("0.5"),
    minutes_since_open: Decimal = Decimal("60"),
    today_ltp: Decimal = Decimal("2870"),
) -> dict[str, Decimal]:
    """Build a bar_feats dict matching what the feature engine
    writes into ``stocks.intraday_features`` at interval_sec=900.

    - ``market_breadth_pct_above_sma200`` — FE-8 cohort breadth
    - ``stress_prob`` — FE-9 regime projection (same-day daily value)
    - ``rsi_5``, ``gap_pct``, ``minutes_since_open`` — MR v1 inner
      condition leaves
    """
    return {
        _KEY_BREADTH: breadth,
        _KEY_STRESS: stress,
        "rsi_5": rsi_5,
        "gap_pct": gap_pct,
        "minutes_since_open": minutes_since_open,
        "today_ltp": today_ltp,
        "today_vol": Decimal("2500000"),
    }


# ── unit tests — assemble_per_bar_features ───────────────────────


def test_breadth_and_stress_survive_assemble_from_bar_feats():
    """Primary path: both features arrive in ``bar_feats`` (as the
    FE-8/FE-9 engine writes them) and must pass through
    ``assemble_per_bar_features`` unsuffixed into the output.
    """
    bar_feats = _minimal_15m_bar_feats()
    out = assemble_per_bar_features(bar_feats=bar_feats)
    assert out[_KEY_BREADTH] == Decimal("65"), (
        f"market_breadth_pct_above_sma200 missing from assembled "
        f"features; got keys: {sorted(out.keys())}"
    )
    assert out[_KEY_STRESS] == Decimal("0.18"), (
        f"stress_prob missing from assembled features; "
        f"got keys: {sorted(out.keys())}"
    )


def test_stress_prob_survives_assemble_from_regime_row():
    """Fallback path (backtest runner's ``regime_by_date``): when
    ``stress_prob`` is injected via ``regime_row`` rather than
    ``bar_feats`` it must still appear unsuffixed in the output.
    This is how the *daily* cadence backtest runner supplies it;
    the intraday runner supplies it via bar_feats (FE-9), but the
    assemble helper must handle both.
    """
    bar_feats = {k: v for k, v in _minimal_15m_bar_feats().items()
                 if k != _KEY_STRESS}
    regime_row = {
        "regime_label": "BULL",
        _KEY_STRESS: Decimal("0.22"),
    }
    out = assemble_per_bar_features(
        bar_feats=bar_feats,
        regime_row=regime_row,
    )
    assert out[_KEY_STRESS] == Decimal("0.22"), (
        "stress_prob not merged unsuffixed from regime_row"
    )
    assert "stress_prob_1d" not in out, (
        "regime_row must not be suffixed; regime is primary-cadence data"
    )


def test_daily_overlay_does_not_shadow_primary_breadth():
    """Hypothetical future guard: if someone populates the
    daily-overlay panel with a ``market_breadth_pct_above_sma200``
    key, the ``_1d``-suffixed version must NOT overwrite the
    primary 15m breadth feature.
    """
    bar_feats = _minimal_15m_bar_feats(breadth=Decimal("65"))
    daily_overlay = {_KEY_BREADTH: Decimal("40")}
    out = assemble_per_bar_features(
        bar_feats=bar_feats,
        daily_overlay=daily_overlay,
    )
    # Primary 15m value untouched
    assert out[_KEY_BREADTH] == Decimal("65")
    # Daily version available under suffixed key
    assert out[f"{_KEY_BREADTH}_1d"] == Decimal("40")


# ── unit tests — lookup_daily_overlay ────────────────────────────


def test_lookup_daily_overlay_returns_panel_row_by_date():
    """``lookup_daily_overlay`` must locate the row for a given
    ``(ticker, bar_date)`` and return it — verifying the ns-key
    computation from bar_date matches what the feature engine
    writes.  Concretely: FE-15b's daily-overlay panel keyed by
    UTC-midnight ns must resolve correctly for the MR v1 tickers.
    """
    ts_ns = _utc_midnight_ns(_BAR_DATE)
    panel = {
        _TICKER: {
            ts_ns: {
                _KEY_BREADTH: Decimal("70"),
                _KEY_STRESS: Decimal("0.15"),
            }
        }
    }
    result = lookup_daily_overlay(
        daily_panel=panel,
        ticker=_TICKER,
        bar_date=_BAR_DATE,
    )
    assert result is not None, (
        "lookup_daily_overlay returned None for a present row"
    )
    assert result[_KEY_BREADTH] == Decimal("70")
    assert result[_KEY_STRESS] == Decimal("0.15")


def test_lookup_daily_overlay_returns_none_for_absent_ticker():
    """Missing ticker in panel → None (not empty dict). The runner
    passes None to assemble, which treats it as "no overlay" —
    strategies without _1d refs are unaffected (spec §5.3
    graceful-degradation contract).
    """
    result = lookup_daily_overlay(
        daily_panel={},
        ticker=_TICKER,
        bar_date=_BAR_DATE,
    )
    assert result is None


# ── unit tests — Evaluator.eval_node with MR v1 AST ─────────────


def test_mr_v1_ast_cond_evaluates_true_when_both_features_present():
    """The MR v1 outer gate:

        market_breadth_pct_above_sma200 >= 0.50
        AND stress_prob <= 0.40
        AND 30 <= minutes_since_open <= 270

    All three must resolve from EvalContext.features without
    KeyError.  When all conditions are satisfied the evaluator
    must return an action node (not raise).
    """
    evaluator = Evaluator()
    features = assemble_per_bar_features(
        bar_feats=_minimal_15m_bar_feats(
            breadth=Decimal("65"),       # >= 0.50 → True
            stress=Decimal("0.18"),      # <= 0.40 → True
            rsi_5=Decimal("22"),         # <= 25   → True  (inner)
            gap_pct=Decimal("0.5"),      # >= -1.5 → True  (inner)
            minutes_since_open=Decimal("60"),  # in [30..270] → True
        )
    )
    strategy = parse_strategy(_load_mr_v1())
    ctx = EvalContext(
        ticker=_TICKER,
        bar_date=_BAR_DATE,
        features=features,
        open_qty=0,
    )
    # Must not raise KeyError for any of the feature references
    action = evaluator.eval_node(
        strategy.root.model_dump(by_alias=True),
        ctx,
    )
    # Outer gate passes → inner gate passes → set_target_weight
    assert action.get("type") == "set_target_weight", (
        f"Expected set_target_weight; got: {action}"
    )


def test_mr_v1_ast_cond_evaluates_false_when_breadth_below_threshold():
    """When breadth < 0.50 the outer gate fails → AST returns
    exit action (else branch of the outer if-node).  The feature
    resolution path is still exercised — both features are
    read without KeyError.
    """
    evaluator = Evaluator()
    features = assemble_per_bar_features(
        bar_feats=_minimal_15m_bar_feats(
            breadth=Decimal("0.30"),   # < 0.50 → outer gate fails
            stress=Decimal("0.18"),
            minutes_since_open=Decimal("60"),
        )
    )
    strategy = parse_strategy(_load_mr_v1())
    ctx = EvalContext(
        ticker=_TICKER,
        bar_date=_BAR_DATE,
        features=features,
        open_qty=0,
    )
    action = evaluator.eval_node(
        strategy.root.model_dump(by_alias=True),
        ctx,
    )
    assert action.get("type") == "exit", (
        f"Expected exit when breadth gate fails; got: {action}"
    )


def test_mr_v1_ast_raises_keyerror_when_breadth_absent():
    """Regression guard: if ``market_breadth_pct_above_sma200``
    is absent from the features dict (e.g. FE-8 panel missing),
    the evaluator raises ``KeyError``.  This documents the
    silent-fail risk: the runner catches this error and emits
    no orders rather than crashing.
    """
    evaluator = Evaluator()
    bar_feats = {k: v for k, v in _minimal_15m_bar_feats().items()
                 if k != _KEY_BREADTH}
    features = assemble_per_bar_features(bar_feats=bar_feats)
    strategy = parse_strategy(_load_mr_v1())
    ctx = EvalContext(
        ticker=_TICKER,
        bar_date=_BAR_DATE,
        features=features,
        open_qty=0,
    )
    with pytest.raises(KeyError, match="market_breadth_pct_above_sma200"):
        evaluator.eval_node(
            strategy.root.model_dump(by_alias=True),
            ctx,
        )


def test_mr_v1_ast_raises_keyerror_when_stress_absent():
    """Same silent-fail guard for ``stress_prob``.  If the FE-9
    pass didn't run (no regime history) AND the daily-runner's
    regime_row is absent, the evaluator raises ``KeyError``.
    """
    evaluator = Evaluator()
    bar_feats = {k: v for k, v in _minimal_15m_bar_feats().items()
                 if k != _KEY_STRESS}
    features = assemble_per_bar_features(bar_feats=bar_feats)
    strategy = parse_strategy(_load_mr_v1())
    ctx = EvalContext(
        ticker=_TICKER,
        bar_date=_BAR_DATE,
        features=features,
        open_qty=0,
    )
    with pytest.raises(KeyError, match="stress_prob"):
        evaluator.eval_node(
            strategy.root.model_dump(by_alias=True),
            ctx,
        )


# ── end-to-end smoke — run_backtest with patched I/O ─────────────


def _make_bar(
    *,
    d: date,
    ts_ns: int,
    close: Decimal = Decimal("2870"),
) -> BarData:
    return BarData(
        ticker=_TICKER,
        date=d,
        open=close - Decimal("5"),
        high=close + Decimal("10"),
        low=close - Decimal("10"),
        close=close,
        volume=2_000_000,
        bar_open_ts_ns=ts_ns,
    )


def test_run_backtest_intraday_mr_v1_does_not_keyerror_on_features(
    caplog,
):
    """End-to-end smoke: a 15m backtest run for the MR v1 strategy
    must complete without logging any feature-key-error lines for
    ``market_breadth_pct_above_sma200`` or ``stress_prob`` when
    the intraday feature panel contains both keys.

    Verifies FE-15b wiring end-to-end: the runner assembles
    ticker_features from the panel, the AST evaluator sees the
    correct keys, and no ``_key_err_counts`` warnings appear.

    The runner logs feature KeyErrors at WARNING level via the
    run-summary line with the pattern
    ``"feature-key-errors=... [('Feature not in context: ...',)]"``
    We assert that neither daily-cadence feature name appears in
    that log line.

    We patch all I/O (Iceberg, PG, event flush) and inject a
    synthetic 3-bar panel with both daily-cadence features
    pre-populated in bar_feats (matching what the feature engine
    writes at write time via FE-8 / FE-9).
    """
    import logging

    from backend.algo.backtest.runner import run_backtest

    bar_d = date(2026, 5, 15)
    bar1_ns = _ts_ns_for(bar_d, 9, 30)
    bar2_ns = _ts_ns_for(bar_d, 9, 45)
    bar3_ns = _ts_ns_for(bar_d, 10, 0)

    # Synthetic intraday bars for one ticker, one day
    bars = {
        _TICKER: [
            _make_bar(d=bar_d, ts_ns=bar1_ns),
            _make_bar(d=bar_d, ts_ns=bar2_ns),
            _make_bar(d=bar_d, ts_ns=bar3_ns),
        ]
    }

    # Feature panel: both FE-8 and FE-9 features present at each bar
    intraday_feats: dict[str, dict[int, dict]] = {
        _TICKER: {
            ts: _minimal_15m_bar_feats()
            for ts in (bar1_ns, bar2_ns, bar3_ns)
        }
    }

    strategy = parse_strategy(_load_mr_v1())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=bar_d,
        period_end=bar_d,
        interval_sec=900,
        initial_capital_inr=Decimal("500000"),
    )

    with caplog.at_level(logging.DEBUG, logger="backend.algo.backtest.runner"):
        with (
            patch(
                "backend.algo.backtest.runner.load_intraday_bars_window",
                return_value=bars,
            ),
            patch(
                "backend.algo.backtest.runner.load_intraday_features_window",
                return_value=intraday_feats,
            ),
            patch(
                "backend.algo.backtest.runner.get_factors_window",
                return_value=[],
            ),
            patch(
                "backend.algo.regime.repo.get_regime_history",
                return_value=[],
            ),
            patch(
                "backend.algo.backtest.runner.compute_market_regime",
                return_value={},
            ),
            patch(
                "backend.algo.backtest.runner.compute_market_trend_strength",
                return_value={},
            ),
            patch(
                "backend.algo.backtest.runner.query_iceberg_table",
                return_value=[],
            ),
            patch(
                "backend.algo.backtest.runner.flush_events",
            ),
        ):
            summary = run_backtest(
                strategy=strategy,
                request=request,
                user_id=uuid4(),
                universe=[_TICKER],
            )

    # Summary must complete (no exception)
    assert summary.run_id is not None
    assert summary.status == "completed"

    # The runner logs a summary line containing feature-key-errors=
    # followed by the top-5 missing keys. If either daily-cadence
    # feature was absent at any bar its name appears there. We
    # assert it does NOT appear, confirming FE-15b wiring is intact.
    all_log = " ".join(r.message for r in caplog.records)
    assert _KEY_BREADTH not in all_log, (
        f"market_breadth_pct_above_sma200 appeared in runner log "
        f"as a missing feature — FE-8 panel not reaching EvalContext.\n"
        f"Log:\n{all_log}"
    )
    assert _KEY_STRESS not in all_log, (
        f"stress_prob appeared in runner log as a missing feature — "
        f"FE-9 panel not reaching EvalContext.\n"
        f"Log:\n{all_log}"
    )
