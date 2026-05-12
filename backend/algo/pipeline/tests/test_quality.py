"""Tests for the data-quality assertion framework
(ASETPLTFRM-380)."""
from __future__ import annotations

from backend.algo.pipeline.quality import (
    Assertion,
    StepAssertionReport,
    cross_source_close_enough,
    emit_violation_events,
    evaluate_assertions,
    value_in_range,
    value_is_not_nan,
)


# ---------------------------------------------------------------
# value_is_not_nan
# ---------------------------------------------------------------


def test_value_is_not_nan_passes_on_clean_value() -> None:
    a = value_is_not_nan("vix_close")
    r = a.evaluate({"vix_close": 18.55})
    assert r.passed
    assert r.severity == "error"


def test_value_is_not_nan_fails_on_nan_float() -> None:
    a = value_is_not_nan("breadth")
    r = a.evaluate({"breadth": float("nan")})
    assert r.failed
    assert "NaN" in r.message
    assert r.detail["field"] == "breadth"


def test_value_is_not_nan_fails_on_none() -> None:
    a = value_is_not_nan("vix_close")
    r = a.evaluate({"vix_close": None})
    assert r.failed
    assert r.detail["value"] is None


def test_value_is_not_nan_passes_on_missing_key() -> None:
    """Missing key behaves the same as None: fails."""
    a = value_is_not_nan("vix_close")
    r = a.evaluate({})
    assert r.failed


# ---------------------------------------------------------------
# value_in_range
# ---------------------------------------------------------------


def test_value_in_range_passes_in_band() -> None:
    a = value_in_range("breadth", 0.0, 1.0)
    assert a.evaluate({"breadth": 0.5}).passed


def test_value_in_range_fails_out_of_band() -> None:
    a = value_in_range("breadth", 0.0, 1.0)
    r = a.evaluate({"breadth": 1.5})
    assert r.failed
    assert "outside [0.0, 1.0]" in r.message
    assert r.detail["lo"] == 0.0


def test_value_in_range_passes_on_none_or_nan() -> None:
    """value_in_range delegates NaN/None to a separate
    not_nan assertion — keeps each check single-concern."""
    a = value_in_range("breadth", 0.0, 1.0)
    assert a.evaluate({"breadth": None}).passed
    assert a.evaluate({"breadth": float("nan")}).passed


def test_value_in_range_fails_on_non_numeric() -> None:
    a = value_in_range("breadth", 0.0, 1.0)
    r = a.evaluate({"breadth": "garbage"})
    assert r.failed
    assert "not numeric" in r.message


# ---------------------------------------------------------------
# cross_source_close_enough — the today's-bug case.
# ---------------------------------------------------------------


def test_cross_source_within_tolerance_passes() -> None:
    a = cross_source_close_enough(
        "vix_close", "expected_vix_close", tolerance_pct=5.0,
    )
    r = a.evaluate({
        "vix_close": 18.4,
        "expected_vix_close": 18.55,
    })
    assert r.passed


def test_cross_source_outside_tolerance_fails() -> None:
    """The exact 2026-05-11 stale-VIX scenario: pipeline wrote
    16.84 but ^INDIAVIX close was 18.55 — that's a ~9% delta,
    well outside the 5% tolerance band."""
    a = cross_source_close_enough(
        "vix_close", "expected_vix_close", tolerance_pct=5.0,
    )
    r = a.evaluate({
        "vix_close": 16.84,
        "expected_vix_close": 18.55,
    })
    assert r.failed
    assert "16.84" in r.message
    assert "18.55" in r.message
    assert r.detail["delta_pct"] > 5.0


def test_cross_source_zero_expected_passes_safely() -> None:
    """Avoid div-by-zero when expected is exactly 0."""
    a = cross_source_close_enough("v", "e")
    assert a.evaluate({"v": 1, "e": 0}).passed


# ---------------------------------------------------------------
# Assertion crash safety
# ---------------------------------------------------------------


def test_assertion_crash_surfaces_as_error_violation() -> None:
    """A check_fn that raises must NOT crash the executor — it
    surfaces as a failed assertion with severity=error so the
    pipeline can finish honestly."""

    def _boom(_ctx):
        raise RuntimeError("kaboom")

    a = Assertion(name="boom", severity="warn", check_fn=_boom)
    r = a.evaluate({})
    assert r.failed
    assert r.severity == "error"  # upgraded from warn
    assert "kaboom" in r.message
    assert r.detail["exception"] == "kaboom"


# ---------------------------------------------------------------
# evaluate_assertions + StepAssertionReport
# ---------------------------------------------------------------


def test_evaluate_returns_report_with_all_results() -> None:
    rep = evaluate_assertions(
        "regime_classifier_daily",
        [
            value_is_not_nan("vix_close"),
            value_in_range("breadth", 0.0, 1.0),
        ],
        {"vix_close": 18.55, "breadth": 0.7},
    )
    assert rep.step == "regime_classifier_daily"
    assert len(rep.results) == 2
    assert all(r.passed for r in rep.results)
    assert rep.status == "ok"
    assert rep.failed == []


def test_status_warn_when_only_warn_fails() -> None:
    rep = evaluate_assertions(
        "step",
        [
            value_in_range("x", 0.0, 1.0, severity="warn"),
        ],
        {"x": 5.0},
    )
    assert rep.status == "warn"


def test_status_error_when_any_error_fails() -> None:
    rep = evaluate_assertions(
        "step",
        [
            value_is_not_nan("x"),  # error severity
            value_in_range("y", 0.0, 1.0, severity="warn"),
        ],
        {"x": None, "y": 0.5},
    )
    assert rep.status == "error"


# ---------------------------------------------------------------
# emit_violation_events
# ---------------------------------------------------------------


def test_emit_violation_events_sinks_only_failures() -> None:
    rep = evaluate_assertions(
        "regime_classifier_daily",
        [
            value_is_not_nan("vix_close"),
            value_is_not_nan("breadth"),
            value_in_range("breadth", 0.0, 1.0, severity="warn"),
        ],
        {"vix_close": None, "breadth": float("nan")},
    )
    sink: list[dict] = []
    n = emit_violation_events(
        rep,
        pipeline_id="india_regime_daily",
        run_id="run-abc",
        user_id="user-1",
        events_sink=sink.append,
    )
    # 2 failed (both NaN checks; range check passes on NaN).
    assert n == 2
    assert len(sink) == 2
    for ev in sink:
        assert ev["type"] == "data_quality_violation"
        # The runtime writes payload as JSON string under
        # ``payload_json``; the helper goes through event_row
        # which serialises.
        import json
        payload = json.loads(ev["payload_json"])
        assert payload["pipeline_id"] == "india_regime_daily"
        assert payload["run_id"] == "run-abc"
        assert payload["step"] == "regime_classifier_daily"


def test_emit_violation_events_zero_when_all_pass() -> None:
    rep = evaluate_assertions(
        "step", [value_is_not_nan("x")], {"x": 1.0},
    )
    n = emit_violation_events(
        rep, pipeline_id="p", events_sink=lambda _: None,
    )
    assert n == 0
