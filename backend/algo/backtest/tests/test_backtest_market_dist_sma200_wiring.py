"""Backtest-runner wiring proof for ``nifty_distance_from_sma200_pct``
(2026-08-10 regime feature, Task 1).

Per the algo.md wiring rule, a feature merely added to the catalog
but never populated by a runtime always hits
``signal_rejected reason=missing_feature`` — silently. These tests
drive ``run_backtest`` end-to-end (mirroring
``test_stop_loss_integration.py``'s minimal-patch pattern) and prove
the feature actually reaches BOTH places ``backend/algo/backtest/
runner.py`` assembles an ``EvalContext``:

1. The entry-call site (``assemble_per_bar_features(..., \
   market_dist_sma200=...)`` at the per-ticker AST-eval loop).
2. The mid-trade regime-exit ``market_feats`` block
   (``check_regime_exit_triggers(market_features=...)``).

Both are proven by making the AST/mid-trade-check condition
reference the new feature directly and observing the resulting
BUY/force-exit behavior flip with the mocked value — a stronger
proof than mere key-presence, since a missing key would raise
inside AST evaluation (caught + logged, never a silent pass-through
to a specific BUY/SELL outcome).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy

_TICKER = "FAKE.NS"
_BASE = date(2026, 4, 1)


def _gen_flat_bars(n: int, close: Decimal = Decimal("100")) -> list[BarData]:
    return [
        BarData(
            ticker=_TICKER,
            date=_BASE + timedelta(days=i),
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=10_000,
        )
        for i in range(n)
    ]


def _gated_entry_strategy() -> dict:
    """Entry fires ONLY when nifty_distance_from_sma200_pct > 5."""
    return {
        "id": str(uuid4()),
        "name": "dist-sma200-gated entry",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "nifty_distance_from_sma200_pct"},
                "op": ">",
                "right": {"literal": 5},
            },
            "then": {"type": "buy", "qty": {"shares": 10}},
            "else": {"type": "hold"},
        },
        "risk": {
            "per_trade": {"stop_loss_pct": 0, "max_qty": 1000},
            "portfolio": {
                "max_exposure_pct": 100,
                "max_concentration_pct": 100,
            },
            "daily": {"max_loss_pct": 50, "max_open_positions": 10},
        },
    }


def _run_gated_entry(dist_map: dict) -> object:
    strategy = parse_strategy(_gated_entry_strategy())
    bars = {_TICKER: _gen_flat_bars(5)}
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_BASE,
        period_end=_BASE + timedelta(days=4),
    )
    with (
        patch(
            "backend.algo.backtest.runner.load_ohlcv_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.compute_market_distance_from_sma200",
            return_value=dist_map,
        ),
        patch("backend.algo.backtest.runner.flush_events"),
    ):
        return run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=[_TICKER],
        )


def test_entry_fires_when_distance_above_threshold():
    """A dict mapping every bar date to +10 (> 5 gate) must let the
    AST evaluate the feature as present and TRUE — proving
    ``market_dist_sma200`` reached the entry-call-site
    ``assemble_per_bar_features`` and the AST evaluator saw a
    real, non-default value (default is Decimal(0), which would
    NOT satisfy ``> 5``).
    """
    dist_map = {
        _BASE + timedelta(days=i): Decimal("10") for i in range(5)
    }
    summary = _run_gated_entry(dist_map)
    assert summary.status == "completed"
    fills = [t for t in summary.trade_list]
    assert len(fills) >= 1 or summary.trade_list, (
        "expected the +10 (> 5) gate to let at least one BUY "
        "signal reach a fill — nifty_distance_from_sma200_pct "
        "did not reach the AST evaluator with the mocked value"
    )


def test_entry_never_fires_when_distance_below_threshold():
    """Same strategy, mocked value -10 (fails the > 5 gate) —
    zero trades. Confirms the earlier pass wasn't accidental
    (e.g. a stale default of 0 happening to satisfy some other
    branch) by flipping the sign and re-running.
    """
    dist_map = {
        _BASE + timedelta(days=i): Decimal("-10") for i in range(5)
    }
    summary = _run_gated_entry(dist_map)
    assert summary.status == "completed"
    assert summary.trade_list == []


def _regime_exit_strategy() -> dict:
    """Unconditional entry; mid-trade regime exit gated on the
    SAME new feature staying > 0."""
    return {
        "id": str(uuid4()),
        "name": "dist-sma200 mid-trade regime exit",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 10}},
        "mid_trade_regime_check": {
            "type": "compare",
            "left": {"feature": "nifty_distance_from_sma200_pct"},
            "op": ">",
            "right": {"literal": 0},
        },
        "risk": {
            "per_trade": {"stop_loss_pct": 0, "max_qty": 1000},
            "portfolio": {
                "max_exposure_pct": 100,
                "max_concentration_pct": 100,
            },
            "daily": {"max_loss_pct": 50, "max_open_positions": 10},
        },
    }


def test_regime_exit_block_sees_the_new_feature():
    """Day 1 opens a position (fills day 2). The mocked distance
    dict flips negative starting day 2 — the mid-trade regime
    check (``> 0``) must fail once it sees the position AND the
    negative value, force-closing with exit_reason='regime_exit'.

    This proves the SECOND wiring site (the inline ``market_feats``
    block ~L1017-1021, distinct from the entry-call site) also
    carries ``nifty_distance_from_sma200_pct`` through to the
    ``EvalContext`` ``check_regime_exit_triggers`` builds
    internally — a missing key there would raise inside AST eval,
    not silently no-op, so a clean force-close proves the key
    reached it.
    """
    dist_map = {
        _BASE: Decimal("10"),
        _BASE + timedelta(days=1): Decimal("-10"),
        _BASE + timedelta(days=2): Decimal("-10"),
        _BASE + timedelta(days=3): Decimal("-10"),
        _BASE + timedelta(days=4): Decimal("-10"),
    }
    strategy = parse_strategy(_regime_exit_strategy())
    bars = {_TICKER: _gen_flat_bars(5)}
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_BASE,
        period_end=_BASE + timedelta(days=4),
    )
    with (
        patch(
            "backend.algo.backtest.runner.load_ohlcv_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.compute_market_distance_from_sma200",
            return_value=dist_map,
        ),
        patch("backend.algo.backtest.runner.flush_events"),
    ):
        summary = run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=[_TICKER],
        )
    assert summary.status == "completed"
    regime_exits = [
        t for t in summary.trade_list if t.exit_reason == "regime_exit"
    ]
    assert regime_exits, (
        "expected at least one exit_reason='regime_exit' trade — "
        "the mid-trade regime-exit market_feats block did not see "
        "nifty_distance_from_sma200_pct with the mocked negative "
        "value"
    )
