"""Regression: daily engine emits the 3 features RSI(2) Connors v3
references (ASETPLTFRM-432).

These features were added to the engine in PR #231 but missed the
nightly persistence pipeline's data foundation — the engine code
emits them, the persistence job auto-writes all emitted features,
but the historical Iceberg table covered only ~6 months. Backfill
landed via the operational `run_daily_features_daily_compute_job`
invocation; this test guards against accidental removal of the
emit lines.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.algo.backtest.types import BarData
from backend.algo.features.daily_engine import (
    DEFAULT_DAILY_SMA_WINDOWS,
    compute_daily_features,
)


def _bars(n: int, start_close: float = 100.0) -> list[BarData]:
    """Synthetic monotonic-uptrend bars long enough for SMA200."""
    bars = []
    for i in range(n):
        c = Decimal(str(start_close + i * 0.5))
        bars.append(BarData(
            ticker="TEST.NS",
            date=date(2024, 1, 1) + timedelta(days=i),
            open=c,
            high=c + Decimal("0.5"),
            low=c - Decimal("0.5"),
            close=c,
            volume=10000,
            bar_open_ts_ns=i * 86400 * 10**9,
        ))
    return bars


def test_default_sma_windows_include_5_for_v3_compat():
    # v3 references distance_from_sma5 which derives from sma_5.
    assert 5 in DEFAULT_DAILY_SMA_WINDOWS


def test_engine_emits_v3_features_after_warmup():
    panel = compute_daily_features(_bars(220))
    # Pick a bar past SMA200 warmup.
    last_ts = sorted(panel.keys())[-1]
    feats = panel[last_ts]
    # The three v3-strategy features that ASETPLTFRM-432
    # backfilled into stocks.intraday_features.
    assert "rsi_2" in feats, (
        "rsi_2 missing from daily_engine output — v3 strategy "
        "would silent-skip every entry. See ASETPLTFRM-432."
    )
    assert "sma_5" in feats, (
        "sma_5 missing — distance_from_sma5 depends on it."
    )
    assert "distance_from_sma5" in feats, (
        "distance_from_sma5 missing — v3 strategy's exit "
        "condition (else branch) would silent-skip."
    )


def test_distance_from_sma5_arithmetic():
    panel = compute_daily_features(_bars(220, start_close=100.0))
    last_ts = sorted(panel.keys())[-1]
    feats = panel[last_ts]
    # distance_from_sma5 = (close - sma_5) / sma_5
    close = Decimal(str(220 - 1)) * Decimal("0.5") + Decimal("100")
    sma5 = feats["sma_5"]
    expected = (close - sma5) / sma5
    assert abs(feats["distance_from_sma5"] - expected) < Decimal(
        "0.0001"
    ), f"distance_from_sma5={feats['distance_from_sma5']} expected={expected}"


def test_rsi_2_in_valid_range():
    # RSI is bounded [0, 100]. Verify rsi_2 emits a sensible value
    # rather than something pathological.
    panel = compute_daily_features(_bars(220))
    for ts, feats in panel.items():
        if "rsi_2" in feats:
            rsi = feats["rsi_2"]
            assert Decimal("0") <= rsi <= Decimal("100"), (
                f"rsi_2={rsi} out of valid range at ts={ts}"
            )
