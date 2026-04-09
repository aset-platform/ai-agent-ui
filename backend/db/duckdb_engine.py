"""DuckDB in-process query engine for Iceberg tables."""

import logging

import duckdb

from backend.paths import ICEBERG_WAREHOUSE

log = logging.getLogger(__name__)

_extensions_installed = False


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


def _resolve_metadata(table_name: str) -> str | None:
    """Find the latest Iceberg metadata JSON path.

    Returns:
        Path string or ``None`` if no metadata found.
    """
    metadata_path = (
        ICEBERG_WAREHOUSE / table_name.replace(".", "/") / "metadata"
    )
    metadata_files = sorted(
        metadata_path.glob("*.metadata.json"),
        reverse=True,
    )
    if not metadata_files:
        log.warning("No metadata for %s", table_name)
        return None
    return str(metadata_files[0])


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
            return result.fetchdf()
        except Exception:
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()
