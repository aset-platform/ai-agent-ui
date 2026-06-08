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


# --- Proactive manifest-list guard in _resolve_metadata ---
# (ASETPLTFRM-429)
#
# A cached metadata.json that still EXISTS on disk but whose current
# snapshot's manifest-list avro was deleted by the orphan sweep must
# be treated as stale and re-resolved — not returned. This prevents
# the doomed scan before it happens (the _run_with_heal retry is the
# reactive backstop).

import json  # noqa: E402


def test_uri_to_local_path_collapses_slashes():
    from backend.db.duckdb_engine import _uri_to_local_path

    assert (
        _uri_to_local_path("file:////Users/x/snap-1.avro")
        == "/Users/x/snap-1.avro"
    )
    assert (
        _uri_to_local_path("file:///tmp/snap-2.avro")
        == "/tmp/snap-2.avro"
    )
    assert _uri_to_local_path("/already/local") == "/already/local"


def _write_table(tmp_path, version, snap_id, snap_filename):
    """Build a metadata.json (+ its snap avro) for stocks.ohlcv
    under *tmp_path* and return (metadata_path, snap_path)."""
    meta_dir = tmp_path / "stocks" / "ohlcv" / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    snap_path = meta_dir / snap_filename
    snap_path.write_text("avro-bytes")
    meta_path = meta_dir / f"{version}-uuid.metadata.json"
    meta_path.write_text(
        json.dumps(
            {
                "current-snapshot-id": snap_id,
                "snapshots": [
                    {
                        "snapshot-id": snap_id,
                        "manifest-list": f"file://{snap_path}",
                    }
                ],
            }
        )
    )
    return str(meta_path), snap_path


def test_resolve_reresolves_when_manifest_list_deleted(
    tmp_path, monkeypatch,
):
    """Cache hit whose metadata.json exists but manifest-list was
    deleted -> drop stale entry, re-glob to the current metadata."""
    import backend.db.duckdb_engine as eng

    old_meta, old_snap = _write_table(
        tmp_path, "03000", 111, "snap-111.avro",
    )
    new_meta, _new_snap = _write_table(
        tmp_path, "03001", 222, "snap-222.avro",
    )
    monkeypatch.setattr(eng, "ICEBERG_WAREHOUSE", tmp_path)
    eng.invalidate_metadata()
    # Poison: cache the OLD (soon-stale) metadata + its manifest.
    eng._meta_cache["stocks.ohlcv"] = (old_meta, str(old_snap))
    # Orphan sweep deletes the old snapshot's manifest-list.
    old_snap.unlink()

    resolved = eng._resolve_metadata("stocks.ohlcv")
    assert resolved == new_meta  # re-resolved to current
    eng.invalidate_metadata()


def test_resolve_cache_hit_short_circuits_glob(
    tmp_path, monkeypatch,
):
    """A healthy cached entry is returned without globbing — even
    when a newer metadata.json exists on disk."""
    import backend.db.duckdb_engine as eng

    good_meta, good_snap = _write_table(
        tmp_path, "03000", 111, "snap-111.avro",
    )
    # A newer file exists; a glob would pick it.
    _write_table(tmp_path, "03001", 222, "snap-222.avro")
    monkeypatch.setattr(eng, "ICEBERG_WAREHOUSE", tmp_path)
    eng.invalidate_metadata()
    eng._meta_cache["stocks.ohlcv"] = (good_meta, str(good_snap))

    resolved = eng._resolve_metadata("stocks.ohlcv")
    assert resolved == good_meta  # cache hit, not the newer file
    eng.invalidate_metadata()


def test_resolve_populates_manifest_list_in_cache(
    tmp_path, monkeypatch,
):
    """A cold resolve records the current snapshot's manifest-list
    path alongside the metadata path."""
    import backend.db.duckdb_engine as eng

    meta, snap = _write_table(
        tmp_path, "03000", 111, "snap-111.avro",
    )
    monkeypatch.setattr(eng, "ICEBERG_WAREHOUSE", tmp_path)
    eng.invalidate_metadata()

    resolved = eng._resolve_metadata("stocks.ohlcv")
    assert resolved == meta
    cached_meta, cached_ml = eng._meta_cache["stocks.ohlcv"]
    assert cached_meta == meta
    assert cached_ml == str(snap)
    eng.invalidate_metadata()
