"""Sanity tests for rsi14_trend_pullback_swing_v1.json."""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.indicators import compute_indicators
from backend.algo.backtest.types import BarData
from backend.algo.features.per_bar import assemble_per_bar_features
from backend.algo.strategy.ast import parse_strategy

_TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "templates"
    / "rsi14_trend_pullback_swing_v1.json"
)


@pytest.fixture
def template_dict() -> dict:
    return json.loads(_TEMPLATE_PATH.read_text())


def test_template_parses_cleanly(template_dict):
    s = parse_strategy(template_dict)
    assert s.product == "CNC"
    assert s.schedule.interval == "1d"
    assert s.universe.scope == "discovery"
    assert s.universe.filter.market == "india"
    assert s.universe.filter.ticker_type == ["stock"]
    assert s.rebalance.max_positions == 5


def test_template_entry_thresholds_match_spec(template_dict):
    """Entry: distance_from_sma200>0, sma_50>sma_200,
    sma200_slope>0, rsi_14<50, rsi_14_delta_1bar>0,
    dist_from_prev_day_high_pct>0."""
    entry = template_dict["root"]["cond"]["operands"]
    thresholds = {op["left"]["feature"]: op for op in entry}

    assert thresholds["distance_from_sma200"]["op"] == ">"
    assert thresholds["distance_from_sma200"]["right"]["literal"] == 0.0

    assert thresholds["sma_50"]["op"] == ">"
    assert thresholds["sma_50"]["right"]["feature"] == "sma_200"

    assert thresholds["sma200_slope"]["op"] == ">"
    assert thresholds["sma200_slope"]["right"]["literal"] == 0.0

    assert thresholds["rsi_14"]["op"] == "<"
    assert thresholds["rsi_14"]["right"]["literal"] == 50

    assert thresholds["rsi_14_delta_1bar"]["op"] == ">"
    assert thresholds["rsi_14_delta_1bar"]["right"]["literal"] == 0.0

    assert thresholds["dist_from_prev_day_high_pct"]["op"] == ">"
    assert thresholds["dist_from_prev_day_high_pct"]["right"]["literal"] == 0.0


def test_template_exit_branch_structure(template_dict):
    """Exit: distance_from_sma200<0 OR
    (bars_below_sma50>=2 AND rsi_14<45)."""
    exit_branch = template_dict["root"]["else"]
    assert exit_branch["type"] == "if"
    cond = exit_branch["cond"]
    assert cond["type"] == "or"

    trend_fail, soft_weak = cond["operands"]
    assert trend_fail["left"]["feature"] == "distance_from_sma200"
    assert trend_fail["op"] == "<"
    assert trend_fail["right"]["literal"] == 0.0

    assert soft_weak["type"] == "and"
    bars_op, rsi_op = soft_weak["operands"]
    assert bars_op["left"]["feature"] == "bars_below_sma50"
    assert bars_op["op"] == ">="
    assert bars_op["right"]["literal"] == 2
    assert rsi_op["left"]["feature"] == "rsi_14"
    assert rsi_op["op"] == "<"
    assert rsi_op["right"]["literal"] == 45

    assert exit_branch["then"]["type"] == "exit"
    assert exit_branch["then"]["scope"] == "this_symbol"
    assert exit_branch["else"]["type"] == "hold"


def test_template_risk_caps(template_dict):
    s = parse_strategy(template_dict)
    assert s.risk.per_trade.stop_loss_pct == 8.0
    assert s.risk.portfolio.max_exposure_pct == 100.0
    assert s.risk.portfolio.max_concentration_pct == 25.0
    assert s.risk.daily.max_loss_pct == 5.0
    assert s.risk.daily.max_open_positions == 5


def test_template_uses_only_expected_features(template_dict):
    expected = {
        "distance_from_sma200",
        "sma_50",
        "sma_200",
        "sma200_slope",
        "rsi_14",
        "rsi_14_delta_1bar",
        "dist_from_prev_day_high_pct",
        "bars_below_sma50",
    }
    used: set[str] = set()

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "feature" and isinstance(v, str):
                    used.add(v)
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(template_dict["root"])
    extra = used - expected
    assert not extra, f"AST references unexpected features: {extra}"
    missing = expected - used
    assert not missing, f"AST missing expected features: {missing}"


def _ctx(**features: float) -> EvalContext:
    return EvalContext(
        ticker="TEST.NS",
        bar_date=date(2026, 9, 5),
        features={k: Decimal(str(v)) for k, v in features.items()},
        open_qty=0,
    )


def test_evaluator_enters_when_all_entry_conditions_true(template_dict):
    ctx = _ctx(
        distance_from_sma200=0.05,
        sma_50=110,
        sma_200=100,
        sma200_slope=0.01,
        rsi_14=45,
        rsi_14_delta_1bar=2,
        dist_from_prev_day_high_pct=0.5,
        bars_below_sma50=0,
    )
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "set_target_weight", "weight": 0.2}


def test_evaluator_holds_when_pullback_too_deep(template_dict):
    """rsi_14 >= 50 fails the pullback condition, and no exit
    condition is true either (position still in a healthy
    uptrend) -> hold."""
    ctx = _ctx(
        distance_from_sma200=0.05,
        sma_50=110,
        sma_200=100,
        sma200_slope=0.01,
        rsi_14=55,
        rsi_14_delta_1bar=2,
        dist_from_prev_day_high_pct=0.5,
        bars_below_sma50=0,
    )
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "hold"}


def test_evaluator_exits_on_major_trend_failure(template_dict):
    ctx = _ctx(
        distance_from_sma200=-0.02,
        sma_50=95,
        sma_200=100,
        sma200_slope=-0.01,
        rsi_14=60,
        rsi_14_delta_1bar=-1,
        dist_from_prev_day_high_pct=-0.5,
        bars_below_sma50=0,
    )
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "exit", "scope": "this_symbol"}


def test_evaluator_exits_on_soft_trend_weakness(template_dict):
    ctx = _ctx(
        distance_from_sma200=0.02,  # still above SMA200 -> not major
        sma_50=101,
        sma_200=100,
        sma200_slope=0.005,
        rsi_14=40,  # < 45
        rsi_14_delta_1bar=-1,
        dist_from_prev_day_high_pct=-0.2,
        bars_below_sma50=3,  # >= 2
    )
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "exit", "scope": "this_symbol"}


def test_end_to_end_wires_real_computed_features_without_missing_feature_error(
    template_dict,
):
    """Wires the ACTUAL runtime feature-assembly path — the same
    one backtest/paper/live use (assemble_per_bar_features,
    backend/algo/features/per_bar.py) — for a well-warmed
    synthetic uptrend series. bar_feats comes from
    compute_indicators (the PRIMARY-cadence daily source every
    1d-schedule strategy actually reads; NOT compute_daily_features,
    which only feeds the _1d cross-cadence overlay for INTRADAY-
    primary strategies — the original version of this test got
    this distinction wrong; see
    docs/superpowers/plans/2026-09-05-rsi14-trend-pullback-swing.md
    for the correction history). factor_row supplies
    distance_from_sma200/sma200_slope, mirroring what the real
    runner reads from stocks.daily_factors. No daily_overlay is
    passed, matching how the real backtest/paper/live runners call
    assemble_per_bar_features for a 1d-primary-cadence strategy.
    Guards the class of bug where a template references a feature
    key no runtime ever actually populates for its own cadence
    (KeyError: Feature not in context)."""
    bars = []
    price = 100.0
    for i in range(260):
        close_p = price * 1.003
        bars.append(BarData(
            ticker="TEST.NS",
            date=date(2024, 1, 1) + timedelta(days=i),
            open=Decimal(str(round(price, 4))),
            high=Decimal(str(round(max(price, close_p) * 1.005, 4))),
            low=Decimal(str(round(min(price, close_p) * 0.995, 4))),
            close=Decimal(str(round(close_p, 4))),
            volume=10000,
            bar_open_ts_ns=i * 86400 * 10**9,
        ))
        price = close_p

    panel = compute_indicators(bars)
    last_date = bars[-1].date
    bar_feats = panel[last_date]

    sma200_last = bar_feats["sma_200"]
    sma200_21_ago = panel[bars[-1 - 21].date]["sma_200"]
    close_last = bars[-1].close
    factor_row = {
        "distance_from_sma200": (
            (close_last - sma200_last) / sma200_last
        ),
        "sma200_slope": (
            (sma200_last - sma200_21_ago) / sma200_21_ago
        ),
    }

    feats = assemble_per_bar_features(
        bar_feats=bar_feats,
        factor_row=factor_row,
    )

    # Explicit presence check for every feature the template
    # references — the AST's short-circuiting and/or means simply
    # evaluating it does NOT guarantee every operand was looked
    # up (entry's rsi_14<50 is false here, so rsi_14_delta_1bar
    # and dist_from_prev_day_high_pct are never reached by
    # eval_node in THIS scenario). Assert directly instead.
    required_features = {
        "distance_from_sma200",
        "sma_50",
        "sma_200",
        "sma200_slope",
        "rsi_14",
        "rsi_14_delta_1bar",
        "dist_from_prev_day_high_pct",
        "bars_below_sma50",
    }
    missing = required_features - feats.keys()
    assert not missing, f"features missing from real panel: {missing}"

    ctx = EvalContext(
        ticker="TEST.NS",
        bar_date=last_date,
        features=feats,
        open_qty=0,
    )
    # A monotonic uptrend never has a down day, so rsi_14 stays
    # well above 50 for the whole warmed period -> the pullback
    # condition (rsi_14 < 50) never fires -> entry fails; and the
    # exit OR is also false throughout (distance_from_sma200 stays
    # positive, bars_below_sma50 stays 0 since close never dips
    # below sma_50) -> the only reachable outcome is hold. This is
    # a concrete, provable assertion, not a tautology.
    result = Evaluator().eval_node(template_dict["root"], ctx)
    assert result == {"type": "hold"}
