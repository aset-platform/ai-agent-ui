"""Tests for the min_adtv_inr universe filter (ASETPLTFRM-430 Exp.1)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from backend.algo.backtest.universe import _load_snapshot_adtv
from backend.algo.strategy.ast import UniverseFilter, parse_strategy


def test_universe_filter_accepts_min_adtv_inr():
    f = UniverseFilter(
        ticker_type=["stock"],
        market="india",
        min_adtv_inr=50_000_000,
    )
    assert f.min_adtv_inr == 50_000_000


def test_universe_filter_defaults_min_adtv_inr_to_none():
    f = UniverseFilter(ticker_type=["stock"])
    assert f.min_adtv_inr is None


def test_universe_filter_rejects_negative_min_adtv_inr():
    with pytest.raises(ValidationError):
        UniverseFilter(
            ticker_type=["stock"],
            min_adtv_inr=-1.0,
        )


def test_v2_template_parses_with_adtv_filter():
    path = (
        Path(__file__).parent.parent.parent
        / "strategy" / "templates" / "rsi2_connors_daily_v2.json"
    )
    d = json.loads(path.read_text())
    strategy = parse_strategy(d)
    assert strategy.universe.filter.min_adtv_inr == 50_000_000


def test_load_snapshot_adtv_returns_latest_rebalance_only():
    """When the snapshot has multiple rebalance dates, the loader
    returns the most recent cohort only."""
    fake_rows = [
        {
            "rebalance_date": date(2025, 1, 1),
            "ticker": "OLD.NS",
            "adtv_inr_60d": 1_000_000_000.0,
        },
        {
            "rebalance_date": date(2026, 5, 1),
            "ticker": "NEW.NS",
            "adtv_inr_60d": 500_000_000.0,
        },
        {
            "rebalance_date": date(2026, 5, 1),
            "ticker": "FRESH.NS",
            "adtv_inr_60d": 75_000_000.0,
        },
    ]
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=fake_rows,
    ):
        adtv = _load_snapshot_adtv()
    assert set(adtv.keys()) == {"NEW.NS", "FRESH.NS"}
    assert adtv["NEW.NS"] == 500_000_000.0
    assert adtv["FRESH.NS"] == 75_000_000.0


def test_load_snapshot_adtv_empty_when_no_rows():
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ):
        assert _load_snapshot_adtv() == {}
