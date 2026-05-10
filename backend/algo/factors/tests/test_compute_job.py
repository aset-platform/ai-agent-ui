"""Orchestrator integration test — mock data layer, verify
end-to-end flow."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors import compute_job


def _ohlcv(
    close: list[float], volume: list[int] | None = None,
) -> pd.DataFrame:
    n = len(close)
    if volume is None:
        volume = [1000] * n
    return pd.DataFrame({
        "bar_date": [
            date(2024, 1, 1) + timedelta(days=i) for i in range(n)
        ],
        "open": close, "high": close, "low": close,
        "close": close, "volume": volume,
    })


def test_run_compute_writes_rows(monkeypatch) -> None:
    closes = list(np.linspace(100, 130, 280))
    monkeypatch.setattr(
        compute_job, "_get_universe", lambda: ["TEST.NS"],
    )
    monkeypatch.setattr(
        compute_job, "_load_ohlcv_for_ticker",
        lambda t, s, e: _ohlcv(closes),
    )
    monkeypatch.setattr(
        compute_job, "_load_nifty_history",
        lambda s, e: _ohlcv(closes),
    )
    monkeypatch.setattr(
        compute_job, "_load_sector_indices_history",
        lambda s, e: {},
    )
    monkeypatch.setattr(
        compute_job, "_lookup_sector",
        lambda tickers: {"TEST.NS": "IT"},
    )
    monkeypatch.setattr(
        compute_job, "_compute_breadth_for_date",
        lambda d: {
            "pct_above_50sma": 0.6,
            "pct_above_200sma": 0.5,
            "midcap_largecap_ratio": 1.4,
        },
    )

    captured: list = []
    monkeypatch.setattr(
        compute_job, "upsert_factors",
        lambda rows: captured.extend(rows) or len(rows),
    )

    n_written = compute_job.run_compute_job(
        as_of=date(2024, 1, 1) + timedelta(days=279),
        days=2,
    )
    assert n_written > 0
    assert all(r.ticker == "TEST.NS" for r in captured)
    last = captured[-1]
    assert "mom_12_1" in last.values
    assert last.values.get("pct_above_50sma") == 0.6
