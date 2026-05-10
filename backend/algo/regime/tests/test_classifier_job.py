"""Tests for classifier_job — orchestrator integration with mock
data + repo. End-to-end with synthetic OHLCV input."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from backend.algo.regime import classifier_job


def _make_synthetic_history(end: date, days: int) -> pd.DataFrame:
    """Synthetic NIFTY history — bullish trend with calm vol."""
    rng = np.random.default_rng(7)
    n = days
    dates = pd.date_range(end - timedelta(days=n - 1), end, freq="D")
    # Gentle uptrend with low noise
    prices = 18000 * (
        1 + rng.normal(0.0008, 0.005, n)
    ).cumprod()
    return pd.DataFrame({"bar_date": dates, "close": prices})


def test_compute_inputs_from_history() -> None:
    today = date(2026, 5, 9)
    nifty_df = _make_synthetic_history(today, 252)
    vix_df = pd.DataFrame({
        "bar_date": [today], "close": [13.5],
    })
    breadth_pct = Decimal("0.65")

    inputs = classifier_job._compute_inputs(
        as_of=today,
        nifty_df=nifty_df,
        vix_df=vix_df,
        pct_above_50sma=breadth_pct,
    )
    assert inputs["nifty_close"] > Decimal("0")
    assert inputs["nifty_sma200"] > Decimal("0")
    assert inputs["vix_close"] == Decimal("13.5")
    assert "nifty_ret_30d" in inputs
    assert "nifty_ret_60d" in inputs
    assert inputs["pct_above_50sma"] == Decimal("0.65")


def test_safe_classify_falls_back_to_sideways_on_nan() -> None:
    """When VIX is missing (NaN), the orchestrator must NOT raise
    — it logs degraded mode and writes SIDEWAYS."""
    inputs = {
        "nifty_close": Decimal("20000"),
        "nifty_sma200": Decimal("18000"),
        "vix_close": Decimal("NaN"),
        "nifty_ret_30d": Decimal("0.05"),
        "nifty_ret_60d": Decimal("0.10"),
        "pct_above_50sma": Decimal("0.60"),
    }
    label, degraded = classifier_job._safe_classify(inputs)
    assert label == "SIDEWAYS"
    assert degraded is True


def test_run_classifier_writes_row(monkeypatch) -> None:
    """Patch all I/O — verify the orchestrator builds a RegimeRow
    and calls upsert_regime_history exactly once."""
    today = date(2026, 5, 9)
    nifty_df = _make_synthetic_history(today, 252)
    vix_df = pd.DataFrame({"bar_date": [today], "close": [13.5]})

    monkeypatch.setattr(
        classifier_job,
        "_load_nifty_window",
        lambda *a, **k: nifty_df,
    )
    monkeypatch.setattr(
        classifier_job,
        "_load_vix_latest",
        lambda *a, **k: vix_df,
    )
    monkeypatch.setattr(
        classifier_job,
        "_compute_breadth_pct_50sma",
        lambda *a, **k: Decimal("0.65"),
    )
    monkeypatch.setattr(
        classifier_job,
        "_compute_stress_prob",
        lambda *a, **k: 0.18,
    )

    captured: list = []

    def _fake_upsert(rows):
        captured.extend(rows)
        return len(rows)

    monkeypatch.setattr(
        classifier_job,
        "upsert_regime_history",
        _fake_upsert,
    )

    classifier_job.run_classifier(as_of=today)

    assert len(captured) == 1
    row = captured[0]
    assert row.bar_date == today
    assert row.regime_label in {"BULL", "SIDEWAYS", "BEAR"}
    assert row.stress_prob == 0.18
