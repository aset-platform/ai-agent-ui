"""Quality factor tests — f_score lookup from piotroski_scores."""
from __future__ import annotations

import math
from datetime import date

from backend.algo.factors.quality import compute_quality


def test_quality_returns_f_score(monkeypatch) -> None:
    rows = [
        {"score_date": date(2026, 5, 1), "total_score": 7},
        {"score_date": date(2026, 5, 8), "total_score": 8},
    ]
    monkeypatch.setattr(
        "backend.algo.factors.quality._load_piotroski",
        lambda ticker, start, end: rows,
    )
    out = compute_quality(
        "RELIANCE.NS", date(2026, 5, 1), date(2026, 5, 8),
    )
    assert out[date(2026, 5, 1)]["f_score"] == 7
    assert out[date(2026, 5, 8)]["f_score"] == 8


def test_quality_forward_fills(monkeypatch) -> None:
    rows = [{"score_date": date(2026, 5, 1), "total_score": 6}]
    monkeypatch.setattr(
        "backend.algo.factors.quality._load_piotroski",
        lambda ticker, start, end: rows,
    )
    out = compute_quality(
        "RELIANCE.NS", date(2026, 5, 1), date(2026, 5, 5),
    )
    assert out[date(2026, 5, 5)]["f_score"] == 6


def test_quality_missing_returns_nan(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.algo.factors.quality._load_piotroski",
        lambda ticker, start, end: [],
    )
    out = compute_quality("FOO", date(2026, 5, 1), date(2026, 5, 1))
    assert math.isnan(out[date(2026, 5, 1)]["f_score"])
