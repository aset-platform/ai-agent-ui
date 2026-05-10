"""Attribution job orchestrator skeleton tests (REGIME-6).

These tests focus on the deterministic orchestration logic
(grouping, mock factor seeding, payload aggregation) — they do
NOT hit Postgres or Iceberg. The integration-level wiring is
exercised separately via the routes test once data is seeded.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import numpy as np

from backend.algo.attribution.job import (
    _aggregate_sector_weights_and_returns,
    _generate_mock_factor_returns,
)


def test_aggregate_sector_weights_from_filled_events() -> None:
    """Two BUY fills in IT + one in Banks produce sector weights
    proportional to traded INR."""
    sector_map = {
        "RELIANCE.NS": "IT",
        "TCS.NS": "IT",
        "HDFCBANK.NS": "Banks",
    }
    events = [
        {
            "payload_json": (
                '{"ticker": "RELIANCE.NS", "qty": 10, '
                '"fill_price": 100.0}'
            ),
        },
        {
            "payload_json": (
                '{"ticker": "TCS.NS", "qty": 5, '
                '"fill_price": 200.0}'
            ),
        },
        {
            "payload_json": (
                '{"ticker": "HDFCBANK.NS", "qty": 2, '
                '"fill_price": 500.0}'
            ),
        },
    ]
    weights, returns = _aggregate_sector_weights_and_returns(
        events, sector_map,
    )
    # IT: 10*100 + 5*200 = 2000; Banks: 2*500 = 1000; total 3000
    assert weights == {
        "IT": 2000.0 / 3000.0,
        "Banks": 1000.0 / 3000.0,
    }
    assert returns == {"IT": 0.0, "Banks": 0.0}


def test_aggregate_handles_empty_event_list() -> None:
    weights, returns = _aggregate_sector_weights_and_returns(
        [], {},
    )
    assert weights == {}
    assert returns == {}


def test_aggregate_skips_malformed_payload() -> None:
    """A row with un-parseable payload doesn't crash; valid rows
    still aggregate."""
    sector_map = {"RELIANCE.NS": "IT"}
    events = [
        {"payload_json": "not-json"},
        {"payload_json": '{"ticker": "RELIANCE.NS"}'},  # no qty
        {
            "payload_json": (
                '{"ticker": "RELIANCE.NS", "qty": 1, '
                '"fill_price": 100.0}'
            ),
        },
    ]
    weights, _ = _aggregate_sector_weights_and_returns(
        events, sector_map,
    )
    assert weights == {"IT": 1.0}


def test_unknown_sector_bucket() -> None:
    """A ticker missing from the sector_map lands in 'Unknown'."""
    sector_map: dict[str, str | None] = {"FOO.NS": None}
    events = [
        {
            "payload_json": (
                '{"ticker": "FOO.NS", "qty": 1, '
                '"fill_price": 50.0}'
            ),
        },
    ]
    weights, _ = _aggregate_sector_weights_and_returns(
        events, sector_map,
    )
    assert weights == {"Unknown": 1.0}


def test_mock_factor_returns_deterministic() -> None:
    """Same (user, strategy, period_start) → identical factor
    returns. Different period_start → different draws."""
    uid = UUID("11111111-1111-1111-1111-111111111111")
    sid = UUID("22222222-2222-2222-2222-222222222222")
    a = _generate_mock_factor_returns(
        user_id=uid, strategy_id=sid,
        period_start=date(2026, 5, 1), n=30,
    )
    b = _generate_mock_factor_returns(
        user_id=uid, strategy_id=sid,
        period_start=date(2026, 5, 1), n=30,
    )
    for k in a:
        assert np.array_equal(a[k], b[k])
    c = _generate_mock_factor_returns(
        user_id=uid, strategy_id=sid,
        period_start=date(2026, 4, 1), n=30,
    )
    assert not np.array_equal(a["MKT"], c["MKT"])
    assert set(a.keys()) == {"MKT", "SMB", "HML", "MOM"}
    assert all(len(v) == 30 for v in a.values())
