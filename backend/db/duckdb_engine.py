"""DuckDB in-process query engine for Iceberg tables."""

import logging
import threading

import duckdb

from backend.paths import ICEBERG_WAREHOUSE

log = logging.getLogger(__name__)

_extensions_installed = False

# Metadata cache: table_name → metadata JSON path.
# Avoids filesystem glob on every query (~30ms each).
_meta_cache: dict[str, str] = {}
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


def _resolve_metadata(table_name: str) -> str | None:
    """Find the latest Iceberg metadata JSON path.

    Caches the result in-memory. Invalidated by
    :func:`invalidate_metadata` after writes.
    """
    with _meta_lock:
        cached = _meta_cache.get(table_name)
    if cached:
        return cached

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
        _meta_cache[table_name] = result
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
    conn = get_connection()
    try:
        for tn in table_names:
            try:
                _create_view(conn, tn)
            except FileNotFoundError:
                pass
        result = conn.execute(
            sql, params or [],
        )
        columns = [
            desc[0] for desc in result.description
        ]
        return [
            dict(zip(columns, row))
            for row in result.fetchall()
        ]
    finally:
        conn.close()


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
    conn = get_connection()
    try:
        _create_view(conn, table_name)
        result = conn.execute(sql, params or [])
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]
    finally:
        conn.close()


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

    conn = get_connection()
    try:
        _create_view(conn, table_name)
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
    finally:
        conn.close()
