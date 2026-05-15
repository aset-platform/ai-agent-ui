"""Tests for the feature-engine quality assertions
(:mod:`backend.algo.features.quality`).

Three assertions, three error scenarios:
- coverage floor < 95% of bars emitting >= 5 features → fires
- a single NaN / inf row in the batch → fires
- a row missing or with wrong ``feature_set_version`` → fires
"""

from __future__ import annotations

import math

from backend.algo.features import FEATURE_SET_VERSION
from backend.algo.features.quality import (
    features_coverage_floor,
    features_no_nan,
    features_version_stamped,
    run_post_compute_assertions,
)


def _row(
    ticker="A.NS",
    ts_ns=1_700_000_000_000_000_000,
    feature_name="rsi",
    feature_value=55.0,
    version=FEATURE_SET_VERSION,
):
    return {
        "ticker": ticker,
        "bar_open_ts_ns": ts_ns,
        "bar_date": "2026-05-13",
        "year_month": "2026-05",
        "interval_sec": 900,
        "feature_name": feature_name,
        "feature_value": feature_value,
        "feature_set_version": version,
    }


# ---------- Coverage floor --------------------------------------


def test_coverage_floor_passes_when_every_bar_has_min_features():
    """Each bar emits exactly 5 features → coverage 100%."""
    ts = 1_700_000_000_000_000_000
    rows = [_row(ts_ns=ts, feature_name=f"f{i}") for i in range(5)]
    expected = {("A.NS", ts)}
    res = features_coverage_floor().evaluate(
        {"arrow_rows": rows, "expected_bar_keys": expected},
    )
    assert res.passed, res.message


def test_coverage_floor_fires_when_below_95_percent():
    """One bar emits 5 features, nineteen bars emit 4 → only
    1/20 = 5% bars meet the floor → fires."""
    rows = []
    expected: set[tuple[str, int]] = set()
    base_ts = 1_700_000_000_000_000_000
    # Bar 0 — 5 features (above floor)
    expected.add(("A.NS", base_ts))
    for i in range(5):
        rows.append(_row(ts_ns=base_ts, feature_name=f"f{i}"))
    # Bars 1..19 — only 4 features each (below floor)
    for j in range(1, 20):
        ts = base_ts + j * 900 * 1_000_000_000
        expected.add(("A.NS", ts))
        for k in range(4):
            rows.append(_row(ts_ns=ts, feature_name=f"f{k}"))
    res = features_coverage_floor().evaluate(
        {"arrow_rows": rows, "expected_bar_keys": expected},
    )
    assert not res.passed
    assert "coverage" in res.message.lower()
    assert res.detail["below_floor_count"] == 19
    assert res.detail["total"] == 20


def test_coverage_floor_fails_on_missing_context():
    """Silent-success guard: the assertion must fail (not pass)
    when the required context keys are absent."""
    res = features_coverage_floor().evaluate({})
    assert not res.passed


def test_coverage_floor_empty_expected_passes():
    """Empty input → vacuously passes."""
    res = features_coverage_floor().evaluate(
        {"arrow_rows": [], "expected_bar_keys": set()},
    )
    assert res.passed


# ---------- NaN / inf detection --------------------------------


def test_no_nan_passes_on_finite_values():
    rows = [_row(feature_value=v) for v in (55.0, -1.5, 0.0, 1e6)]
    res = features_no_nan().evaluate({"arrow_rows": rows})
    assert res.passed, res.message


def test_no_nan_fires_on_single_nan_row():
    rows = [
        _row(feature_value=55.0),
        _row(feature_name="rsi_5", feature_value=float("nan")),
        _row(feature_name="vwap", feature_value=99.5),
    ]
    res = features_no_nan().evaluate({"arrow_rows": rows})
    assert not res.passed
    assert res.detail["bad_count"] == 1


def test_no_nan_fires_on_inf_row():
    rows = [_row(feature_value=math.inf)]
    res = features_no_nan().evaluate({"arrow_rows": rows})
    assert not res.passed
    assert res.detail["bad_count"] == 1


# ---------- Version stamp --------------------------------------


def test_version_stamp_passes_when_every_row_matches():
    rows = [_row() for _ in range(5)]
    res = features_version_stamped().evaluate({"arrow_rows": rows})
    assert res.passed, res.message


def test_version_stamp_fires_on_empty_version():
    rows = [
        _row(),
        _row(version=""),
    ]
    res = features_version_stamped().evaluate({"arrow_rows": rows})
    assert not res.passed
    assert res.detail["bad_count"] == 1


def test_version_stamp_fires_on_wrong_version():
    rows = [_row(version="v0.9-old")]
    res = features_version_stamped().evaluate({"arrow_rows": rows})
    assert not res.passed
    assert res.detail["expected_version"] == FEATURE_SET_VERSION


# ---------- Orchestrator helper --------------------------------


def test_run_post_compute_assertions_aggregates_status():
    """Three pass + an inserted NaN row → ``status='error'`` on
    the report (because the no-nan severity is ``error``)."""
    ts = 1_700_000_000_000_000_000
    good_rows = [_row(ts_ns=ts, feature_name=f"f{i}") for i in range(5)]
    bad_rows = good_rows + [
        _row(ts_ns=ts, feature_name="f6", feature_value=float("nan")),
    ]
    expected = {("A.NS", ts)}
    rep_good = run_post_compute_assertions(
        arrow_rows=good_rows,
        expected_bar_keys=expected,
    )
    assert rep_good.status == "ok"
    rep_bad = run_post_compute_assertions(
        arrow_rows=bad_rows,
        expected_bar_keys=expected,
    )
    assert rep_bad.status == "error"
    assert any(
        r.name == "intraday-features-no-nan" and r.failed
        for r in rep_bad.results
    )
