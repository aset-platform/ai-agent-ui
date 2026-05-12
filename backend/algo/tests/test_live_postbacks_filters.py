"""Tests for the dry_run + since_date filters on the Live
Postbacks / Submissions endpoints (ASETPLTFRM-382).

These tests bypass the FastAPI route layer (which has pre-existing
breakage around response_model) and exercise the query helpers
directly via a mocked ``query_iceberg_table``. That's the right
target — the helpers are where the new SQL clauses live, and
exercising them in isolation keeps the tests fast + deterministic.
"""
from __future__ import annotations

from unittest.mock import patch

from backend.algo.routes.live import (
    _dry_run_clause,
    _query_order_submission_events,
    _query_postback_events,
)


# ---------------------------------------------------------------
# _dry_run_clause — SQL fragment generation
# ---------------------------------------------------------------


def test_dry_run_clause_none_yields_empty() -> None:
    assert _dry_run_clause(None) == ""


def test_dry_run_clause_true_filters_to_dry_only() -> None:
    out = _dry_run_clause(True)
    assert "'$.dry_run') = 'true'" in out
    assert "IS NULL" not in out


def test_dry_run_clause_false_treats_null_as_real() -> None:
    """ASETPLTFRM-374 omits dry_run from real-money payloads, so
    the False branch MUST accept NULL alongside the literal
    'false' — otherwise post-374 events wouldn't surface."""
    out = _dry_run_clause(False)
    assert "'$.dry_run') = 'false'" in out
    assert "IS NULL" in out


# ---------------------------------------------------------------
# _query_order_submission_events — param composition
# ---------------------------------------------------------------


def _capture_sql():
    """Helper that returns (captured_sql_list, captured_params_list,
    mock) wired to capture whatever SQL the helper assembles."""
    captured = {"sql": [], "params": []}

    def _fake(_table, sql, params):
        captured["sql"].append(sql)
        captured["params"].append(list(params))
        return []

    return captured, _fake


def test_order_submissions_no_filters_emits_minimal_sql() -> None:
    cap, fake = _capture_sql()
    with patch(
        "backend.algo.routes.live.query_iceberg_table",
        side_effect=fake,
    ):
        _query_order_submission_events("user-1", 25)
    sql = cap["sql"][0]
    params = cap["params"][0]
    assert "type = 'order_submitted_live'" in sql
    assert "json_extract_string" not in sql  # no dry_run clause
    assert "ts_date >=" not in sql
    assert params == ["user-1", 25]


def test_order_submissions_dry_run_false_adds_null_branch() -> None:
    cap, fake = _capture_sql()
    with patch(
        "backend.algo.routes.live.query_iceberg_table",
        side_effect=fake,
    ):
        _query_order_submission_events(
            "user-1", 25, dry_run=False,
        )
    sql = cap["sql"][0]
    assert "'$.dry_run') = 'false'" in sql
    assert "IS NULL" in sql


def test_order_submissions_since_date_adds_ts_date_filter() -> None:
    cap, fake = _capture_sql()
    with patch(
        "backend.algo.routes.live.query_iceberg_table",
        side_effect=fake,
    ):
        _query_order_submission_events(
            "user-1", 25, since_date="2026-05-12",
        )
    sql = cap["sql"][0]
    params = cap["params"][0]
    assert "ts_date >= ?" in sql
    assert "2026-05-12" in params


def test_order_submissions_session_id_with_filters() -> None:
    """The Submissions panel may scope to one runtime session AND
    today + real-money. Composability is the contract."""
    cap, fake = _capture_sql()
    with patch(
        "backend.algo.routes.live.query_iceberg_table",
        side_effect=fake,
    ):
        _query_order_submission_events(
            "user-1",
            25,
            session_id="sess-abc",
            dry_run=False,
            since_date="2026-05-12",
        )
    sql = cap["sql"][0]
    params = cap["params"][0]
    assert "session_id = ?" in sql
    assert "ts_date >= ?" in sql
    assert "IS NULL" in sql
    assert "sess-abc" in params
    assert "2026-05-12" in params


# ---------------------------------------------------------------
# _query_postback_events — same matrix
# ---------------------------------------------------------------


def test_postbacks_no_filters_emits_minimal_sql() -> None:
    cap, fake = _capture_sql()
    with patch(
        "backend.algo.routes.live.query_iceberg_table",
        side_effect=fake,
    ):
        _query_postback_events("user-1", 50)
    sql = cap["sql"][0]
    assert "type = 'kite_postback_received'" in sql
    assert "json_extract_string" not in sql
    assert "ts_date >=" not in sql


def test_postbacks_dry_run_false_includes_null_branch() -> None:
    cap, fake = _capture_sql()
    with patch(
        "backend.algo.routes.live.query_iceberg_table",
        side_effect=fake,
    ):
        _query_postback_events("user-1", 50, dry_run=False)
    sql = cap["sql"][0]
    assert "'$.dry_run') = 'false'" in sql
    assert "IS NULL" in sql


def test_postbacks_since_date_appends_ts_date_filter() -> None:
    cap, fake = _capture_sql()
    with patch(
        "backend.algo.routes.live.query_iceberg_table",
        side_effect=fake,
    ):
        _query_postback_events(
            "user-1", 50, since_date="2026-05-12",
        )
    sql = cap["sql"][0]
    params = cap["params"][0]
    assert "ts_date >= ?" in sql
    assert "2026-05-12" in params


def test_postbacks_full_filter_stack() -> None:
    """Live → Postbacks tab calls with both filters active."""
    cap, fake = _capture_sql()
    with patch(
        "backend.algo.routes.live.query_iceberg_table",
        side_effect=fake,
    ):
        _query_postback_events(
            "user-1",
            50,
            dry_run=False,
            since_date="2026-05-12",
        )
    sql = cap["sql"][0]
    params = cap["params"][0]
    assert "ts_date >= ?" in sql
    assert "IS NULL" in sql  # NULL-tolerant real-money
    # Order: user_id, since_date, limit.
    assert params == ["user-1", "2026-05-12", 50]
