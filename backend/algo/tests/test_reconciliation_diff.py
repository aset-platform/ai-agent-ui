"""Tests for compute_drift — pure diff function (V2-3).

No I/O, no DB, no Kite — just the pure function.
"""
from __future__ import annotations

import pytest

from backend.algo.live.reconciliation import (
    DriftItem,
    compute_drift,
)


def test_empty_both_sides_no_drift():
    assert compute_drift({}, {}) == []


def test_identical_positions_no_drift():
    our = {"RELIANCE.NS": 100, "INFY.NS": 50}
    broker = {"RELIANCE.NS": 100, "INFY.NS": 50}
    assert compute_drift(our, broker) == []


def test_missing_on_broker_side():
    our = {"RELIANCE.NS": 100}
    broker: dict[str, int] = {}
    result = compute_drift(our, broker)
    assert len(result) == 1
    item = result[0]
    assert item.symbol == "RELIANCE.NS"
    assert item.our_qty == 100
    assert item.broker_qty == 0
    assert item.diff == -100


def test_missing_on_our_side():
    our: dict[str, int] = {}
    broker = {"INFY.NS": 50}
    result = compute_drift(our, broker)
    assert len(result) == 1
    item = result[0]
    assert item.symbol == "INFY.NS"
    assert item.our_qty == 0
    assert item.broker_qty == 50
    assert item.diff == 50


def test_qty_mismatch():
    our = {"RELIANCE.NS": 50}
    broker = {"RELIANCE.NS": 100}
    result = compute_drift(our, broker)
    assert len(result) == 1
    assert result[0] == DriftItem(
        symbol="RELIANCE.NS",
        our_qty=50,
        broker_qty=100,
        diff=50,
    )


def test_threshold_zero_any_diff_counts():
    our = {"RELIANCE.NS": 100, "INFY.NS": 50}
    broker = {"RELIANCE.NS": 101, "INFY.NS": 50}
    result = compute_drift(our, broker, threshold=0)
    assert len(result) == 1
    assert result[0].symbol == "RELIANCE.NS"


def test_threshold_respects_margin():
    # diff = 1, threshold = 1 → |1| > 1 is False → no drift
    our = {"RELIANCE.NS": 100}
    broker = {"RELIANCE.NS": 101}
    assert compute_drift(our, broker, threshold=1) == []


def test_threshold_just_above():
    # diff = 2, threshold = 1 → |2| > 1 → drift
    our = {"RELIANCE.NS": 100}
    broker = {"RELIANCE.NS": 102}
    result = compute_drift(our, broker, threshold=1)
    assert len(result) == 1
    assert result[0].diff == 2


def test_multiple_symbols_sorted():
    our = {"Z.NS": 10, "A.NS": 5}
    broker = {"Z.NS": 20, "A.NS": 15}
    result = compute_drift(our, broker)
    assert [r.symbol for r in result] == ["A.NS", "Z.NS"]


def test_negative_diff_threshold():
    # broker has fewer shares than we think → diff is negative
    our = {"RELIANCE.NS": 100}
    broker = {"RELIANCE.NS": 95}
    result = compute_drift(our, broker, threshold=4)
    assert len(result) == 1
    assert result[0].diff == -5
