"""Market classification in the ScreenQL ``ci`` CTE
(ASETPLTFRM-326).

The CTE derives a ``market`` column consumed by
``WHERE market = 'india'`` filters and by Screener
row serialisation. Prior implementation used a
bare ticker-suffix check which drifted from the
authoritative ``company_info.exchange`` column.

These tests run the rewritten CTE fragment against
a DuckDB in-memory table to lock in the mapping.
"""

from __future__ import annotations

import duckdb
import pytest

from backend.insights.screen_parser import _CTE_TEMPLATES


@pytest.fixture
def market_cte_sql():
    """Extract just the CASE ... AS market expression
    from the ci CTE so we can exercise it in
    isolation without building the full query.
    """
    # The full template contains PEG + row-number
    # logic alongside. We reuse the whole ci_raw CTE
    # for realism and then project just `ticker,
    # market`.
    return (
        "WITH "
        + _CTE_TEMPLATES["ci"]
        + " SELECT ticker, market FROM ci_raw"
    )


@pytest.fixture
def conn(market_cte_sql):
    c = duckdb.connect(":memory:")
    # Create a minimal company_info table with just
    # the columns the CTE references.
    c.execute(
        """
        CREATE TABLE company_info (
            ticker VARCHAR,
            exchange VARCHAR,
            pe_ratio DOUBLE,
            earnings_growth DOUBLE,
            fetched_at TIMESTAMP
        )
        """,
    )
    return c


def _insert(conn, rows):
    conn.executemany(
        "INSERT INTO company_info VALUES "
        "(?, ?, ?, ?, NOW())",
        rows,
    )


def test_nsi_exchange_maps_to_india(
    conn, market_cte_sql,
):
    _insert(
        conn,
        [
            ("RELIANCE.NS", "NSI", 20.0, 0.1),
            ("TCS.NS", "NSI", 25.0, 0.15),
        ],
    )
    result = conn.execute(
        market_cte_sql,
    ).fetchall()
    assert all(
        r[1] == "india" for r in result
    ), result


def test_bse_exchange_maps_to_india(
    conn, market_cte_sql,
):
    _insert(
        conn,
        [("SOMESTOCK.BO", "BSE", 18.0, 0.08)],
    )
    result = conn.execute(
        market_cte_sql,
    ).fetchall()
    assert result[0][1] == "india"


def test_nms_snp_exchanges_map_to_us(
    conn, market_cte_sql,
):
    _insert(
        conn,
        [
            ("AAPL", "NMS", 30.0, 0.12),
            ("^GSPC", "SNP", None, None),
            ("CL=F", "NYM", None, None),
        ],
    )
    result = conn.execute(
        market_cte_sql,
    ).fetchall()
    assert all(
        r[1] == "us" for r in result
    ), result


def test_null_exchange_falls_back_to_ns_suffix(
    conn, market_cte_sql,
):
    """Backward-compat fallback: if exchange is NULL
    (13 such rows exist in prod as of 2026-04-24),
    we still classify Indian tickers correctly via
    the .NS / .BO suffix fallback.
    """
    _insert(
        conn,
        [
            ("HDFCBANK.NS", None, 22.0, 0.1),
            ("AAPL", None, 30.0, 0.12),
        ],
    )
    result = {
        r[0]: r[1]
        for r in conn.execute(
            market_cte_sql,
        ).fetchall()
    }
    assert result["HDFCBANK.NS"] == "india"
    assert result["AAPL"] == "us"


def test_known_indian_indices_classified_as_india(
    conn, market_cte_sql,
):
    """Rule 19-compliant: Indian index tickers
    (``^NSEI``, ``^BSESN``, ``^INDIAVIX``) lack a
    yfinance exchange and don't carry ``.NS``/``.BO``
    — the fallback list catches them.
    """
    _insert(
        conn,
        [
            ("^NSEI", None, None, None),
            ("^BSESN", None, None, None),
            ("^INDIAVIX", None, None, None),
        ],
    )
    result = conn.execute(
        market_cte_sql,
    ).fetchall()
    assert all(
        r[1] == "india" for r in result
    ), result


def test_empty_string_exchange_treated_as_null(
    conn, market_cte_sql,
):
    """Defensive: empty-string exchange values (not
    currently observed but possible after a failed
    fundamentals fetch) should fall through to the
    ticker-based fallback rather than classify as
    us via the ``exchange != ''`` branch.
    """
    _insert(
        conn,
        [("INFY.NS", "", None, None)],
    )
    result = conn.execute(
        market_cte_sql,
    ).fetchall()
    # .NS suffix fallback keeps this as india
    assert result[0][1] == "india"
