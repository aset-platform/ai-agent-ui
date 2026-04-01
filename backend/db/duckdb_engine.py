"""DuckDB in-process query engine for Iceberg tables."""
import logging
from pathlib import Path

import duckdb

from backend.paths import ICEBERG_WAREHOUSE

log = logging.getLogger(__name__)


def get_connection() -> duckdb.DuckDBPyConnection:
    """Create a new DuckDB connection with Iceberg support.

    Each connection is short-lived — create per query batch,
    close after use. DuckDB handles its own caching.
    """
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    log.debug("DuckDB connection created")
    return conn


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
            log.warning(
                "No metadata for %s", table_name,
            )
            return []

        view_name = table_name.split(".")[-1]
        conn.execute(
            f"CREATE VIEW {view_name} AS "
            f"SELECT * FROM iceberg_scan("
            f"'{metadata_files[0]}')"
        )

        result = conn.execute(sql, params or [])
        columns = [
            desc[0] for desc in result.description
        ]
        return [
            dict(zip(columns, row))
            for row in result.fetchall()
        ]
    finally:
        conn.close()
