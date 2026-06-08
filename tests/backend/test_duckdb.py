"""Test DuckDB query layer."""
from unittest import mock

import pytest


def test_duckdb_connection():
    """DuckDB engine returns a connection."""
    from backend.db.duckdb_engine import get_connection

    conn = get_connection()
    assert conn is not None
    result = conn.execute("SELECT 1 AS n").fetchone()
    assert result[0] == 1
    conn.close()


def test_duckdb_parameterized_query():
    """DuckDB parameterized query works."""
    from backend.db.duckdb_engine import get_connection

    conn = get_connection()
    conn.execute(
        "CREATE TABLE test "
        "(ticker VARCHAR, price DOUBLE)"
    )
    conn.execute(
        "INSERT INTO test VALUES "
        "('AAPL', 150.0), ('MSFT', 300.0)"
    )
    result = conn.execute(
        "SELECT price FROM test WHERE ticker = ?",
        ["AAPL"],
    ).fetchone()
    assert result[0] == 150.0
    conn.close()


# --- Stale-metadata-cache self-heal (ASETPLTFRM-429) ---
#
# Regression for: Daily Compute Analytics failing for all
# 802 tickers because the long-lived backend's process-local
# metadata cache pointed at a superseded ``metadata.json``
# whose current snapshot's ``snap-*.avro`` had been deleted by
# the orphan sweep. The metadata.json file still existed, so
# the ``os.path.exists`` guard never fired; the read must
# self-heal by invalidating + re-resolving, then retry once.

_MISSING_SNAP = (
    "IO Error: No files found that match the pattern "
    '"file:////w/stocks/ohlcv/metadata/'
    'snap-3672760242230010905-0-db388cba.avro"'
)
_MISSING_META = (
    "IO Error: Cannot open file "
    '"/w/stocks/ohlcv/metadata/03509-abc.metadata.json"'
)


def test_stale_error_detects_missing_snapshot():
    """Deleted manifest-list avro is a stale-metadata error."""
    from backend.db.duckdb_engine import _is_stale_metadata_error

    assert _is_stale_metadata_error(Exception(_MISSING_SNAP))


def test_stale_error_detects_missing_metadata_json():
    """Deleted metadata.json is a stale-metadata error."""
    from backend.db.duckdb_engine import _is_stale_metadata_error

    assert _is_stale_metadata_error(Exception(_MISSING_META))


def test_stale_error_ignores_unrelated():
    """A query/binder error is not a stale-metadata error."""
    from backend.db.duckdb_engine import _is_stale_metadata_error

    assert not _is_stale_metadata_error(
        Exception("Binder Error: column foo not found"),
    )


def test_run_with_heal_retries_stale_then_succeeds(monkeypatch):
    """First read hits a deleted snapshot; cache is invalidated
    and the retry (cold cache → latest metadata) succeeds."""
    import backend.db.duckdb_engine as eng

    monkeypatch.setattr(
        eng, "get_connection", lambda: mock.MagicMock(),
    )
    monkeypatch.setattr(eng, "_create_view", lambda c, t: None)
    invalidated = []
    monkeypatch.setattr(
        eng, "invalidate_metadata", invalidated.append,
    )

    calls = {"n": 0}

    def runner(_conn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception(_MISSING_SNAP)
        return "OK"

    result = eng._run_with_heal(
        ["stocks.ohlcv"], False, runner,
    )
    assert result == "OK"
    assert calls["n"] == 2  # retried exactly once
    assert invalidated == ["stocks.ohlcv"]


def test_run_with_heal_propagates_after_one_retry(monkeypatch):
    """If the stale error persists, it propagates after a
    single retry (no infinite loop)."""
    import backend.db.duckdb_engine as eng

    monkeypatch.setattr(
        eng, "get_connection", lambda: mock.MagicMock(),
    )
    monkeypatch.setattr(eng, "_create_view", lambda c, t: None)
    monkeypatch.setattr(
        eng, "invalidate_metadata", lambda t: None,
    )

    calls = {"n": 0}

    def runner(_conn):
        calls["n"] += 1
        raise Exception(_MISSING_SNAP)

    with pytest.raises(Exception, match="No files found"):
        eng._run_with_heal(["stocks.ohlcv"], False, runner)
    assert calls["n"] == 2  # initial + one retry only


def test_run_with_heal_non_stale_error_not_retried(monkeypatch):
    """Non-stale errors are raised immediately, not retried,
    and never invalidate the cache."""
    import backend.db.duckdb_engine as eng

    monkeypatch.setattr(
        eng, "get_connection", lambda: mock.MagicMock(),
    )
    monkeypatch.setattr(eng, "_create_view", lambda c, t: None)
    invalidated = []
    monkeypatch.setattr(
        eng, "invalidate_metadata", invalidated.append,
    )

    calls = {"n": 0}

    def runner(_conn):
        calls["n"] += 1
        raise ValueError("Binder Error: nope")

    with pytest.raises(ValueError):
        eng._run_with_heal(["stocks.ohlcv"], False, runner)
    assert calls["n"] == 1  # not retried
    assert invalidated == []
