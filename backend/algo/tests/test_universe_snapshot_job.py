"""Snapshot rebuild orchestrator unit tests (REGIME-7).

The candidate loader + Iceberg upsert are mocked out; only the
filtering / ordering / cap logic is exercised.
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.universe import snapshot_job


def test_rebuild_filters_and_caps_at_200(monkeypatch) -> None:
    # 250 candidates with descending ADTV; expect top 200 included.
    candidates = [
        {
            "ticker": f"T{i}.NS",
            "adtv_inr_60d": float(250 - i) * 1e8,
            "market_cap_inr": 1e10,
            "sector": "IT",
        }
        for i in range(250)
    ]
    monkeypatch.setattr(
        snapshot_job, "_load_candidates", lambda d: candidates,
    )
    captured: list = []
    monkeypatch.setattr(
        snapshot_job,
        "_upsert_snapshot",
        lambda d, rows: captured.extend(rows),
    )
    out = snapshot_job.rebuild_universe_snapshot(date(2026, 5, 1))
    included = [r for r in captured if r["included_in_top_200"]]
    excluded = [r for r in captured if not r["included_in_top_200"]]
    assert len(included) == 200
    assert len(excluded) == 50
    assert out == {"included": 200, "excluded": 50}


def test_rebuild_filters_low_mcap(monkeypatch) -> None:
    candidates = [
        {
            "ticker": "OK.NS",
            "adtv_inr_60d": 5e8,
            "market_cap_inr": 1e10,
            "sector": "IT",
        },
        {
            "ticker": "SMALL.NS",
            "adtv_inr_60d": 5e8,
            "market_cap_inr": 1e8,   # 10cr, below 500cr floor
            "sector": "IT",
        },
    ]
    monkeypatch.setattr(
        snapshot_job, "_load_candidates", lambda d: candidates,
    )
    captured: list = []
    monkeypatch.setattr(
        snapshot_job,
        "_upsert_snapshot",
        lambda d, rows: captured.extend(rows),
    )
    snapshot_job.rebuild_universe_snapshot(date(2026, 5, 1))
    tickers = {r["ticker"] for r in captured}
    assert "SMALL.NS" not in tickers
    assert "OK.NS" in tickers


def test_rebuild_filters_low_adtv(monkeypatch) -> None:
    candidates = [
        {
            "ticker": "LIQUID.NS",
            "adtv_inr_60d": 5e8,         # 50cr — passes
            "market_cap_inr": 1e10,
            "sector": "IT",
        },
        {
            "ticker": "ILLIQUID.NS",
            "adtv_inr_60d": 1e6,         # 10L — below 10cr floor
            "market_cap_inr": 1e10,
            "sector": "IT",
        },
    ]
    monkeypatch.setattr(
        snapshot_job, "_load_candidates", lambda d: candidates,
    )
    captured: list = []
    monkeypatch.setattr(
        snapshot_job,
        "_upsert_snapshot",
        lambda d, rows: captured.extend(rows),
    )
    snapshot_job.rebuild_universe_snapshot(date(2026, 5, 1))
    tickers = {r["ticker"] for r in captured}
    assert "ILLIQUID.NS" not in tickers
    assert "LIQUID.NS" in tickers


def test_rebuild_empty_candidates_no_op(monkeypatch) -> None:
    monkeypatch.setattr(
        snapshot_job, "_load_candidates", lambda d: [],
    )
    monkeypatch.setattr(
        snapshot_job,
        "_upsert_snapshot",
        lambda d, rows: pytest.fail("Should not upsert empty"),
    )
    out = snapshot_job.rebuild_universe_snapshot(date(2026, 5, 1))
    assert out == {"included": 0, "excluded": 0}
