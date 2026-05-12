"""ASETPLTFRM-380 — assertions wired into the regime classifier
pipeline step. Verifies the bug we shipped against (2026-05-11
stale-VIX silent-success) would surface as a
``data_quality_violation`` post-step."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from backend.algo.regime.pipeline_steps import (
    _run_regime_classifier_assertions,
)


@pytest.fixture
def captured_events():
    """Captures whatever ``flush_events`` would have written."""
    captured: list[dict] = []

    def _fake_flush(rows):
        captured.extend(rows)

    with patch(
        "backend.algo.backtest.event_writer.flush_events",
        side_effect=_fake_flush,
    ):
        yield captured


def test_stale_vix_2026_05_11_replay_emits_violation(
    captured_events,
) -> None:
    """The exact bug from 2026-05-11: pipeline wrote vix_close=16.84
    while ^INDIAVIX close was 18.55. Now flagged as a
    cross-source-close-enough warn violation."""
    out = {
        "rule_inputs": {
            "vix_close": 16.84,
            "pct_above_50sma": 0.82,
        },
        "stress_prob": 0.25,
    }
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[{"close": 18.55}],
    ):
        _run_regime_classifier_assertions(
            out, run_id="run-test", today=date(2026, 5, 11),
        )
    types = [e["type"] for e in captured_events]
    assert types == ["data_quality_violation"]
    import json
    p = json.loads(captured_events[0]["payload_json"])
    assert p["step"] == "regime_classifier_daily"
    assert p["pipeline_id"] == "india_regime_daily"
    assert "vix_close" in p["assertion"]
    assert "16.84" in p["message"]
    assert "18.55" in p["message"]


def test_nan_breadth_emits_error_violation(captured_events) -> None:
    """NaN in pct_above_50sma is a hard error — output unusable
    downstream."""
    out = {
        "rule_inputs": {
            "vix_close": 18.5,
            "pct_above_50sma": float("nan"),
        },
        "stress_prob": 0.3,
    }
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[{"close": 18.55}],
    ):
        _run_regime_classifier_assertions(
            out, run_id="run-test", today=date(2026, 5, 11),
        )
    import json
    severities = [
        json.loads(e["payload_json"])["severity"]
        for e in captured_events
    ]
    # value_is_not_nan(pct_above_50sma) → error
    assert "error" in severities


def test_clean_output_emits_no_violations(captured_events) -> None:
    out = {
        "rule_inputs": {
            "vix_close": 18.55,
            "pct_above_50sma": 0.82,
        },
        "stress_prob": 0.25,
    }
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[{"close": 18.55}],
    ):
        _run_regime_classifier_assertions(
            out, run_id="run-test", today=date(2026, 5, 11),
        )
    assert captured_events == []


def test_iceberg_lookup_failure_doesnt_crash(
    captured_events,
) -> None:
    """If the cross-source VIX read raises, the assertion run
    should still complete (other assertions evaluate on the
    available context)."""
    out = {
        "rule_inputs": {
            "vix_close": 18.55,
            "pct_above_50sma": 0.82,
        },
        "stress_prob": 0.25,
    }
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        side_effect=RuntimeError("duckdb borked"),
    ):
        _run_regime_classifier_assertions(
            out, run_id="run-test", today=date(2026, 5, 11),
        )
    # No cross-source row → that assertion has nothing to
    # compare against; passes silently. The other assertions
    # see clean inputs → no violations.
    assert captured_events == []
