"""Breadth factor tests."""
from __future__ import annotations

import math
from datetime import date

from backend.algo.factors.breadth import compute_breadth_for_date


def test_breadth_basic(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.algo.factors.breadth._fetch_breadth_pct",
        lambda d, window: 0.62 if window == 50 else 0.55,
    )
    monkeypatch.setattr(
        "backend.algo.factors.breadth._fetch_midcap_largecap_ratio",
        lambda d: 1.42,
    )
    out = compute_breadth_for_date(date(2026, 5, 8))
    assert out["pct_above_50sma"] == 0.62
    assert out["pct_above_200sma"] == 0.55
    assert out["midcap_largecap_ratio"] == 1.42


def test_breadth_missing_returns_nan(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.algo.factors.breadth._fetch_breadth_pct",
        lambda d, window: float("nan"),
    )
    monkeypatch.setattr(
        "backend.algo.factors.breadth._fetch_midcap_largecap_ratio",
        lambda d: float("nan"),
    )
    out = compute_breadth_for_date(date(2026, 5, 8))
    assert math.isnan(out["pct_above_50sma"])
    assert math.isnan(out["midcap_largecap_ratio"])
