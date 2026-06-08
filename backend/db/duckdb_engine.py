"""DuckDB in-process query engine for Iceberg tables."""

import json
import logging
import os
import threading

import duckdb

from backend.paths import ICEBERG_WAREHOUSE

log = logging.getLogger(__name__)

_extensions_installed = False

# Metadata cache: table_name → (metadata JSON path, current
# snapshot's manifest-list local path | None). Avoids the
# filesystem glob on every query (~30ms each). The manifest-list
# path is tracked so a cache hit can cheaply detect a snapshot the
# orphan sweep expired out from under us (ASETPLTFRM-429) — see
# :func:`_resolve_metadata`.
_meta_cache: dict[str, tuple[str, str | None]] = {}
_meta_lock = threading.Lock()


def get_connection() -> duckdb.DuckDBPyConnection:
    """Create a new DuckDB connection with Iceberg support.

    Each connection is short-lived — create per query batch,
    close after use. DuckDB handles its own caching.
    ``INSTALL`` runs once per process; ``LOAD`` per connection.
    Avro extension required for Iceberg manifest files.
    """
    global _extensions_installed
    conn = duckdb.connect(":memory:")
    if not _extensions_installed:
        conn.execute("INSTALL iceberg;")
        conn.execute("INSTALL avro;")
        _extensions_installed = True
    conn.execute("LOAD iceberg;")
    conn.execute("LOAD avro;")
    log.debug("DuckDB connection created")
    return conn


def invalidate_metadata(
    table_name: str | None = None,
) -> None:
    """Invalidate cached metadata path.

    Call after Iceberg writes so the next query picks
    up the new metadata snapshot.

    Args:
        table_name: Specific table to invalidate, or
            ``None`` to clear all.
    """
    with _meta_lock:
        if table_name:
            _meta_cache.pop(table_name, None)
        else:
            _meta_cache.clear()


def _uri_to_local_path(uri: str) -> str:
    """Convert an Iceberg ``file://`` URI to a local path.

    PyIceberg/DuckDB emit ``file:////Users/...`` (empty authority
    + absolute path). Collapse any number of leading slashes to a
    single one so :func:`os.path.exists` works.
    """
    path = uri
    if path.startswith("file:"):
        path = path[len("file:"):]
    return "/" + path.lstrip("/")


def _current_manifest_list(metadata_path: str) -> str | None:
    """Local path of the current snapshot's manifest-list avro.

    Returns ``None`` if the table has no current snapshot or the
    metadata.json can't be parsed (the caller then falls back to a
    metadata.json-only existence check).
    """
    try:
        with open(metadata_path) as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    csid = meta.get("current-snapshot-id")
    if csid is None:
        return None
    for snap in meta.get("snapshots", []):
        if snap.get("snapshot-id") == csid:
            ml = snap.get("manifest-list")
            return _uri_to_local_path(ml) if ml else None
    return None


def _resolve_metadata(table_name: str) -> str | None:
    """Find the latest Iceberg metadata JSON path.

    Caches the result in-memory. Invalidated by
    :func:`invalidate_metadata` after writes.

    A cached entry is healthy only if BOTH the metadata.json AND
    the current snapshot's manifest-list it points at still exist.
    The orphan sweep can expire a snapshot — deleting its
    manifest-list / manifests — while the superseded metadata.json
    file survives, so an ``os.path.exists`` check on the
    metadata.json alone is insufficient (ASETPLTFRM-429). When the
    manifest-list is gone we drop the stale entry and re-resolve to
    the current (healthy) metadata via the filesystem glob.
    """
    with _meta_lock:
        cached = _meta_cache.get(table_name)
    if cached:
        meta_path, manifest_list = cached
        if os.path.exists(meta_path) and (
            manifest_list is None or os.path.exists(manifest_list)
        ):
            return meta_path
        log.warning(
            "Stale metadata cache for %s (%s); re-resolving",
            table_name,
            meta_path,
        )
        with _meta_lock:
            _meta_cache.pop(table_name, None)

    metadata_path = (
        ICEBERG_WAREHOUSE
        / table_name.replace(".", "/")
        / "metadata"
    )
    metadata_files = sorted(
        metadata_path.glob("*.metadata.json"),
        reverse=True,
    )
    if not metadata_files:
        log.warning("No metadata for %s", table_name)
        return None
    result = str(metadata_files[0])
    with _meta_lock:
        _meta_cache[table_name] = (
            result,
            _current_manifest_list(result),
        )
    return result


def _create_view(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
) -> str:
    """Create a DuckDB view for the Iceberg table.

    Returns:
        The view name (last segment of *table_name*).

    Raises:
        FileNotFoundError: If no metadata exists.
    """
    meta = _resolve_metadata(table_name)
    if meta is None:
        raise FileNotFoundError(f"No Iceberg metadata for {table_name}")
    view_name = table_name.split(".")[-1]
    conn.execute(
        f"CREATE VIEW {view_name} AS " f"SELECT * FROM iceberg_scan('{meta}')"
    )
    return view_name


def _is_stale_metadata_error(exc: Exception) -> bool:
    """True if a read failed because cached Iceberg metadata
    points at files an orphan-sweep / snapshot-expiry removed.

    Two modes, both surfaced by DuckDB as ``IO Error``:

    * Missing snapshot manifest-list / manifest avro
      (``snap-*.avro`` referenced by a *superseded but still
      present* ``metadata.json`` — so the ``os.path.exists``
      guard in :func:`_resolve_metadata` does not fire). This
      is the ASETPLTFRM-429 invalidate-skip race.
    * Missing ``metadata.json`` itself.

    The cache is process-local, so out-of-process writers
    (pipeline CLI) advancing the table never invalidate a
    long-lived reader's cache — hence the read-side self-heal.
    """
    msg = str(exc)
    return (
        "No files found that match the pattern" in msg
        or "Cannot open file" in msg
    )


def _run_with_heal(
    table_names: list[str],
    tolerate_missing: bool,
    runner,
):
    """Create views + run *runner(conn)*, self-healing once
    on a stale-metadata-cache read failure.

    On a stale read (see :func:`_is_stale_metadata_error`),
    invalidate the cached metadata path for every table and
    retry once — the re-resolve globs the filesystem and
    picks the current (healthy) ``metadata.json``.

    Args:
        table_names: Iceberg tables to expose as views.
        tolerate_missing: If True, skip tables with no
            metadata (JOIN queries); else propagate.
        runner: Callable ``(conn) -> result`` that executes
            the query and fully materializes the result
            before returning (connection closed afterwards).
    """
    for attempt in range(2):
        conn = get_connection()
        try:
            for tn in table_names:
                try:
                    _create_view(conn, tn)
                except FileNotFoundError:
                    if not tolerate_missing:
                        raise
            return runner(conn)
        except Exception as exc:
            if attempt == 0 and _is_stale_metadata_error(exc):
                log.warning(
                    "Stale Iceberg read for %s (%s); "
                    "invalidating cache and retrying",
                    ", ".join(table_names),
                    exc,
                )
                for tn in table_names:
                    invalidate_metadata(tn)
                continue
            raise
        finally:
            conn.close()


def query_iceberg_multi(
    table_names: list[str],
    sql: str,
    params: list | None = None,
) -> list[dict]:
    """Run SQL across multiple Iceberg tables.

    Creates views for each table, then executes
    the query. Useful for JOIN queries across
    tables (e.g. ScreenQL).

    Args:
        table_names: e.g. ['stocks.company_info',
            'stocks.analysis_summary']
        sql: SQL with $1, $2 placeholders
        params: Query parameters

    Returns:
        List of dicts (column_name: value)
    """

    def _runner(conn):
        result = conn.execute(sql, params or [])
        columns = [
            desc[0] for desc in result.description
        ]
        return [
            dict(zip(columns, row))
            for row in result.fetchall()
        ]

    return _run_with_heal(table_names, True, _runner)


def query_iceberg_table(
    table_name: str,
    sql: str,
    params: list | None = None,
) -> list[dict]:
    """Run SQL query against an Iceberg table.

    Args:
        table_name: e.g. 'stocks.ohlcv'
        sql: SQL with ? placeholders
        params: Query parameters

    Returns:
        List of dicts (column_name: value)
    """

    def _runner(conn):
        result = conn.execute(sql, params or [])
        columns = [desc[0] for desc in result.description]
        return [
            dict(zip(columns, row))
            for row in result.fetchall()
        ]

    return _run_with_heal([table_name], False, _runner)


def query_iceberg_df(
    table_name: str,
    sql: str,
    params: list | None = None,
):
    """Run SQL against Iceberg table, return DataFrame.

    Uses DuckDB's native ``fetchdf()`` for zero-copy
    transfer to pandas. Falls back to manual conversion
    if needed.
    """
    import pandas as pd  # noqa: F811

    def _runner(conn):
        result = conn.execute(sql, params or [])
        try:
            df = result.fetchdf()
        except Exception:
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            df = pd.DataFrame(rows, columns=columns)
        # Normalize date columns: DuckDB returns
        # datetime64 for Iceberg DateType, but
        # downstream code expects date objects.
        # Convert columns ending in _date, or named
        # "date", "quarter_end", "ex_date" etc.
        # Exclude timestamp columns like fetched_at,
        # updated_at, computed_at, created_at.
        _TS_SUFFIXES = (
            "_at",
            "timestamp",
            "started_at",
            "completed_at",
        )
        for col in df.columns:
            if not pd.api.types.is_datetime64_any_dtype(
                df[col],
            ):
                continue
            if any(col.endswith(s) for s in _TS_SUFFIXES):
                continue  # keep as timestamp
            df[col] = df[col].dt.date
        return df

    return _run_with_heal([table_name], False, _runner)
