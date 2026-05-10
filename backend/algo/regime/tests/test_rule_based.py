"""Tests for rule_based.classify_regime — table-driven."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.algo.regime.rule_based import classify_regime


@pytest.mark.parametrize(
    "name,nifty_close,nifty_sma200,vix,r30,r60,breadth,expected",
    [
        # BULL: above SMA200 + calm/normal VIX + bullish momentum +
        # healthy breadth.
        (
            "calm-bull",
            "20000", "18000", "13", "0.05", "0.10", "0.65", "BULL",
        ),
        (
            "normal-vix-bull",
            "20000", "18000", "20", "0.03", "0.06", "0.58", "BULL",
        ),
        # BEAR: below SMA200 + stress VIX + bearish momentum
        # (breadth not required in BEAR rule).
        (
            "stress-bear",
            "16000", "18000", "30", "-0.05", "-0.10", "0.30", "BEAR",
        ),
        # SIDEWAYS catch-all
        (
            "above-sma-but-stress-vix",
            "20000", "18000", "30", "0.05", "0.10", "0.65", "SIDEWAYS",
        ),
        (
            "below-sma-but-no-bearish-mom",
            "16000", "18000", "30", "0.00", "0.00", "0.30", "SIDEWAYS",
        ),
        (
            "below-sma-no-stress-vix",
            "16000", "18000", "20", "-0.05", "-0.10", "0.30", "SIDEWAYS",
        ),
        (
            "weak-breadth-blocks-bull",
            "20000", "18000", "13", "0.05", "0.10", "0.50", "SIDEWAYS",
        ),
        (
            "momentum-just-at-threshold",
            "20000", "18000", "13", "0.02", "0.05", "0.65", "SIDEWAYS",
        ),
    ],
)
def test_classify_regime(
    name, nifty_close, nifty_sma200, vix, r30, r60, breadth, expected,
):
    got = classify_regime(
        nifty_close=Decimal(nifty_close),
        nifty_sma200=Decimal(nifty_sma200),
        vix_close=Decimal(vix),
        nifty_ret_30d=Decimal(r30),
        nifty_ret_60d=Decimal(r60),
        pct_above_50sma=Decimal(breadth),
    )
    assert got == expected, name


def test_classify_regime_raises_on_nan() -> None:
    """NaN in any input should raise ValueError. The classifier is
    pure — caller (classifier_job) handles fallback to SIDEWAYS."""
    with pytest.raises(ValueError, match="NaN"):
        classify_regime(
            nifty_close=Decimal("NaN"),
            nifty_sma200=Decimal("18000"),
            vix_close=Decimal("13"),
            nifty_ret_30d=Decimal("0.05"),
            nifty_ret_60d=Decimal("0.10"),
            pct_above_50sma=Decimal("0.65"),
        )
