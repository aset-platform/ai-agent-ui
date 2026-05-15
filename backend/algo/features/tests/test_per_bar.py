"""Tests for the shared per-bar feature-assembly helper —
FE-15b (ASETPLTFRM-420).

Covers the cross-cadence suffix rules + non-regression
contract (existing strategies that don't reference suffixed
keys get a byte-identical dict to today's inline assembly).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.algo.features.per_bar import (
    _suffix_keys,
    assemble_per_bar_features,
    lookup_daily_overlay,
)


def test_suffix_keys_renames_every_key():
    assert _suffix_keys(
        {"rsi_14": Decimal("55"), "ema_50": Decimal("100")}, "_1d"
    ) == {
        "rsi_14_1d": Decimal("55"),
        "ema_50_1d": Decimal("100"),
    }


def test_suffix_keys_handles_none_and_empty():
    assert _suffix_keys(None, "_1d") == {}
    assert _suffix_keys({}, "_1d") == {}


def test_assemble_primary_only_matches_legacy_shape():
    """Without any overlay, the helper output must match the
    legacy inline-merge shape used by the 3 runtimes today.
    Non-regression guard.
    """
    bar_feats = {
        "rsi_14": Decimal("55"),
        "ema_50": Decimal("100"),
        "today_ltp": Decimal("99.5"),
    }
    result = assemble_per_bar_features(
        bar_feats=bar_feats,
        market_regime=Decimal("1"),
        market_trend=Decimal("3.2"),
    )
    assert result == {
        "rsi_14": Decimal("55"),
        "ema_50": Decimal("100"),
        "today_ltp": Decimal("99.5"),
        "nifty_above_sma200": Decimal("1"),
        "nifty_30d_return_pct": Decimal("3.2"),
    }


def test_assemble_defaults_market_keys_to_zero_when_missing():
    """If market_regime / market_trend not provided, default to
    Decimal('0') (legacy ``market_regime.get(bar_date, 0)`` shape).
    """
    result = assemble_per_bar_features(bar_feats={})
    assert result["nifty_above_sma200"] == Decimal("0")
    assert result["nifty_30d_return_pct"] == Decimal("0")


def test_assemble_factor_row_merges_unsuffixed():
    bar_feats = {"rsi_14": Decimal("55")}
    factor_row = {
        "mom_12_1": Decimal("0.15"),
        "f_score": Decimal("7"),
    }
    result = assemble_per_bar_features(
        bar_feats=bar_feats,
        factor_row=factor_row,
    )
    assert result["mom_12_1"] == Decimal("0.15")
    assert result["f_score"] == Decimal("7")
    assert result["rsi_14"] == Decimal("55")


def test_assemble_regime_row_merges_unsuffixed():
    result = assemble_per_bar_features(
        bar_feats={},
        regime_row={"regime_label": "BULL", "stress_prob": Decimal("0.21")},
    )
    assert result["regime_label"] == "BULL"
    assert result["stress_prob"] == Decimal("0.21")


def test_assemble_daily_overlay_suffixes_with_1d():
    """Cross-cadence rule: daily overlay (interval_sec=86400)
    must be injected under {name}_1d keys for intraday-primary
    strategies. Primary 15m rsi_14 and daily rsi_14 coexist.
    """
    bar_feats = {"rsi_14": Decimal("55")}  # 15m primary
    daily_overlay = {
        "rsi_14": Decimal("60"),
        "ema_50": Decimal("100"),
        "ema_200": Decimal("95"),
        "golden_cross_bars_ago": Decimal("3"),
    }
    result = assemble_per_bar_features(
        bar_feats=bar_feats,
        daily_overlay=daily_overlay,
    )
    assert result["rsi_14"] == Decimal("55")
    assert result["rsi_14_1d"] == Decimal("60")
    assert result["ema_50_1d"] == Decimal("100")
    assert result["ema_200_1d"] == Decimal("95")
    assert result["golden_cross_bars_ago_1d"] == Decimal("3")


def test_assemble_no_daily_collision_with_primary_15m_rsi_14():
    """The whole point of suffixing: the AST must be able to
    reference BOTH 15m and 1d rsi_14 at the same eval tick.
    """
    result = assemble_per_bar_features(
        bar_feats={"rsi_14": Decimal("25")},
        daily_overlay={"rsi_14": Decimal("70")},
    )
    assert "rsi_14" in result
    assert "rsi_14_1d" in result
    assert result["rsi_14"] != result["rsi_14_1d"]


def test_assemble_hourly_and_fifteen_min_overlays():
    """Future cadences: _1h and _15m suffixes follow the same rule."""
    result = assemble_per_bar_features(
        bar_feats={"rsi_14": Decimal("55")},
        hourly_overlay={"rsi_14": Decimal("50")},
        fifteen_min_overlay={"rsi_14": Decimal("45")},
    )
    assert result["rsi_14"] == Decimal("55")
    assert result["rsi_14_1h"] == Decimal("50")
    assert result["rsi_14_15m"] == Decimal("45")


def test_assemble_empty_overlays_are_no_op():
    result = assemble_per_bar_features(
        bar_feats={"rsi_14": Decimal("55")},
        daily_overlay=None,
        hourly_overlay={},
        fifteen_min_overlay=None,
    )
    for k in result:
        assert not k.endswith("_1d")
        assert not k.endswith("_1h")
        assert not k.endswith("_15m")


def test_lookup_daily_overlay_by_bar_date():
    from datetime import datetime, time, timezone

    bar_d = date(2026, 5, 15)
    ts_ns = int(
        datetime.combine(
            bar_d, time.min, tzinfo=timezone.utc
        ).timestamp()
        * 1_000_000_000
    )
    panel = {
        "RELIANCE.NS": {
            ts_ns: {"ema_50": Decimal("2872.31")},
        },
    }
    result = lookup_daily_overlay(
        daily_panel=panel,
        ticker="RELIANCE.NS",
        bar_date=bar_d,
    )
    assert result == {"ema_50": Decimal("2872.31")}


def test_lookup_daily_overlay_returns_none_when_missing():
    panel = {"RELIANCE.NS": {}}
    assert (
        lookup_daily_overlay(
            daily_panel=panel,
            ticker="RELIANCE.NS",
            bar_date=date(2026, 5, 15),
        )
        is None
    )
    assert (
        lookup_daily_overlay(
            daily_panel={},
            ticker="MISSING.NS",
            bar_date=date(2026, 5, 15),
        )
        is None
    )


def test_assemble_full_stack_round_trip():
    """Realistic case: 15m strategy with daily golden cross +
    daily factor row + regime.
    """
    bar_feats = {
        "rsi_14": Decimal("28"),
        "today_ltp": Decimal("2870"),
        "today_vol": Decimal("1500000"),
    }
    result = assemble_per_bar_features(
        bar_feats=bar_feats,
        market_regime=Decimal("1"),
        market_trend=Decimal("4.2"),
        factor_row={
            "mom_12_1": Decimal("0.18"),
            "f_score": Decimal("8"),
            "realized_vol_60d": Decimal("0.22"),
        },
        regime_row={
            "regime_label": "BULL",
            "stress_prob": Decimal("0.15"),
        },
        daily_overlay={
            "ema_50": Decimal("2865.50"),
            "ema_200": Decimal("2810.00"),
            "golden_cross_bars_ago": Decimal("12"),
        },
    )
    assert result["rsi_14"] == Decimal("28")
    assert result["nifty_above_sma200"] == Decimal("1")
    assert result["nifty_30d_return_pct"] == Decimal("4.2")
    assert result["f_score"] == Decimal("8")
    assert result["realized_vol_60d"] == Decimal("0.22")
    assert result["regime_label"] == "BULL"
    assert result["ema_50_1d"] == Decimal("2865.50")
    assert result["ema_200_1d"] == Decimal("2810.00")
    assert result["golden_cross_bars_ago_1d"] == Decimal("12")
