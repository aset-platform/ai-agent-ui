"""Test DuckDB query layer."""
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
