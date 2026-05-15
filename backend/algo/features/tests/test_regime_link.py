"""FE-9 tests — regime_label + stress_prob engine wiring.

The engine reads ``regime_by_date[bar_date]`` (an IST trading
date keyed dict matching the daily backtest runner's shape) and
projects ``regime_label`` (string) + ``stress_prob`` (Decimal)
onto every bar of that trading date for every ticker.

Skip-emission contract: ``regime_by_date=None`` OR missing date
→ features absent (NOT NaN / NOT None).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.algo.features import compute_intraday_features_for_universe

IST = timezone(timedelta(minutes=330))


def _ts_ns(day: date, hour: int, minute: int) -> int:
    dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _bar(
    day: date,
    hour: int,
    minute: int,
    close: Decimal,
    *,
    ticker: str = "T.NS",
) -> BarData:
    return BarData(
        ticker=ticker,
        date=day,
        open=close - Decimal("0.5"),
        high=close + Decimal("0.5"),
        low=close - Decimal("1.0"),
        close=close,
        volume=1000,
        bar_open_ts_ns=_ts_ns(day, hour, minute),
    )


def _series(
    ticker: str,
    closes: list[Decimal],
    day: date = date(2026, 4, 1),
) -> list[BarData]:
    out: list[BarData] = []
    for i, c in enumerate(closes):
        total_min = 9 * 60 + 15 + i * 15
        hour, minute = divmod(total_min, 60)
        out.append(_bar(day, hour, minute, c, ticker=ticker))
    return out


# ────────────────────────────────────────────────────────────────
# Engine wiring — regime_label + stress_prob
# ────────────────────────────────────────────────────────────────


def test_engine_emits_regime_label_and_stress_prob_when_present():
    """``regime_by_date`` covering the bar's IST date → both
    features emit on every bar of that date."""
    day = date(2026, 4, 1)
    stock = _series("A.NS", [Decimal(100 + i) for i in range(3)], day=day)

    panel = compute_intraday_features_for_universe(
        {"A.NS": stock},
        regime_by_date={
            day: {
                "regime_label": "BULL",
                "stress_prob": Decimal("0.05"),
            },
        },
    )
    for b in stock:
        feats = panel["A.NS"][b.bar_open_ts_ns]
        assert feats["regime_label"] == "BULL"
        assert feats["stress_prob"] == Decimal("0.05")


def test_engine_absent_when_regime_by_date_none():
    """``regime_by_date=None`` (default) → features absent for
    every bar/ticker; rest of the panel still emits."""
    stock = _series("A.NS", [Decimal(100 + i) for i in range(3)])
    panel = compute_intraday_features_for_universe({"A.NS": stock})
    for b in stock:
        feats = panel["A.NS"][b.bar_open_ts_ns]
        assert "regime_label" not in feats
        assert "stress_prob" not in feats
        # Non-FE-9 features still present.
        assert "today_ltp" in feats


def test_engine_absent_when_bar_date_not_in_regime():
    """Bars on a date NOT in ``regime_by_date`` → features
    absent. Pass dict for one day; bars are on a different day."""
    day_bars = date(2026, 4, 2)
    day_regime = date(2026, 4, 1)
    stock = _series(
        "A.NS",
        [Decimal(100 + i) for i in range(3)],
        day=day_bars,
    )
    panel = compute_intraday_features_for_universe(
        {"A.NS": stock},
        regime_by_date={
            day_regime: {
                "regime_label": "BEAR",
                "stress_prob": Decimal("0.7"),
            },
        },
    )
    for b in stock:
        feats = panel["A.NS"][b.bar_open_ts_ns]
        assert "regime_label" not in feats
        assert "stress_prob" not in feats


def test_decimal_precision_preserved_for_stress_prob():
    """Decimal stress_prob survives round-trip without coercion;
    Decimal value is preserved exactly."""
    day = date(2026, 4, 1)
    stock = _series("A.NS", [Decimal("100"), Decimal("101")], day=day)
    panel = compute_intraday_features_for_universe(
        {"A.NS": stock},
        regime_by_date={
            day: {
                "regime_label": "SIDEWAYS",
                "stress_prob": Decimal("0.123456789"),
            },
        },
    )
    for b in stock:
        sp = panel["A.NS"][b.bar_open_ts_ns]["stress_prob"]
        assert isinstance(sp, Decimal)
        assert sp == Decimal("0.123456789")


def test_float_stress_prob_coerced_to_decimal():
    """The repo returns ``stress_prob: float | None`` — engine
    must coerce float → Decimal (mirrors the daily runner
    pattern at ``backend/algo/backtest/runner.py:230-231``)."""
    day = date(2026, 4, 1)
    stock = _series("A.NS", [Decimal("100"), Decimal("101")], day=day)
    panel = compute_intraday_features_for_universe(
        {"A.NS": stock},
        regime_by_date={
            day: {
                "regime_label": "BULL",
                # Pass a float — what the repo actually returns.
                "stress_prob": 0.25,
            },
        },
    )
    sp = panel["A.NS"][stock[0].bar_open_ts_ns]["stress_prob"]
    assert isinstance(sp, Decimal)
    # Decimal(str(0.25)) is exact — no binary-float drift.
    assert sp == Decimal("0.25")


def test_regime_label_only_when_stress_prob_missing():
    """``stress_prob`` may be None in the repo — only the label
    emits; the stress_prob key stays absent."""
    day = date(2026, 4, 1)
    stock = _series("A.NS", [Decimal("100"), Decimal("101")], day=day)
    panel = compute_intraday_features_for_universe(
        {"A.NS": stock},
        regime_by_date={day: {"regime_label": "SIDEWAYS"}},
    )
    feats = panel["A.NS"][stock[0].bar_open_ts_ns]
    assert feats["regime_label"] == "SIDEWAYS"
    assert "stress_prob" not in feats


def test_phase_a_byte_identical_for_non_regime_keys():
    """Non-regime feature keys must be byte-identical between a
    no-regime call and a with-regime call — FE-9 wiring is
    purely additive."""
    day = date(2026, 4, 1)
    stock = _series("A.NS", [Decimal(100 + i) for i in range(10)], day=day)
    no_regime = compute_intraday_features_for_universe({"A.NS": stock})
    with_regime = compute_intraday_features_for_universe(
        {"A.NS": stock},
        regime_by_date={
            day: {
                "regime_label": "BULL",
                "stress_prob": Decimal("0.05"),
            },
        },
    )
    fe9_keys = {"regime_label", "stress_prob"}
    for ts_ns in no_regime["A.NS"]:
        no = no_regime["A.NS"][ts_ns]
        wi = with_regime["A.NS"][ts_ns]
        non_fe9_no = {k: v for k, v in no.items() if k not in fe9_keys}
        non_fe9_with = {k: v for k, v in wi.items() if k not in fe9_keys}
        assert non_fe9_no == non_fe9_with


def test_empty_cohort_inputs_drop_all_fe8_and_fe9_features():
    """When both ``index_bars_by_symbol=None`` and
    ``regime_by_date=None``, ALL 4 FE-8 + 3 FE-9 features must be
    absent simultaneously; non-cohort features still emit."""
    stock = _series("A.NS", [Decimal(100 + i) for i in range(5)])
    panel = compute_intraday_features_for_universe({"A.NS": stock})
    cohort_keys = {
        # FE-8
        "rs_vs_nifty_15m",
        "rs_vs_sector_15m",
        "market_breadth_pct_above_sma200",
        "advance_decline_ratio",
        # FE-9
        "sector_rotation_score",
        "regime_label",
        "stress_prob",
    }
    for b in stock:
        feats = panel["A.NS"][b.bar_open_ts_ns]
        for k in cohort_keys:
            assert k not in feats, (
                f"cohort key {k!r} should be absent when no "
                f"cohort inputs are supplied"
            )
        # Non-cohort features still present.
        assert "today_ltp" in feats
        assert "today_vol" in feats
