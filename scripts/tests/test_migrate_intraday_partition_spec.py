"""Unit tests for the intraday partition-spec migration helper.

Only ``_add_bar_date_d`` is unit-testable in isolation — the full
``migrate_table`` swap path touches the live catalog and is exercised
in Task 6 (gated on human approval), not here.
"""

from datetime import date

import pyarrow as pa


def test_add_bar_date_d_derives_from_bar_date_string():
    from scripts.migrate_intraday_partition_spec import _add_bar_date_d

    src = pa.table(
        {
            "ticker": ["A.NS"],
            "bar_date": ["2026-06-19"],
            "year_month": ["2026-06"],
        }
    )
    out = _add_bar_date_d(src)
    assert "bar_date_d" in out.schema.names
    assert out.column("bar_date_d")[0].as_py() == date(2026, 6, 19)
    assert out.num_rows == src.num_rows


def test_add_bar_date_d_preserves_all_rows_and_columns():
    from scripts.migrate_intraday_partition_spec import _add_bar_date_d

    src = pa.table(
        {
            "ticker": ["A.NS", "B.NS", "C.NS"],
            "bar_date": ["2026-06-19", "2026-06-20", "2026-07-01"],
            "year_month": ["2026-06", "2026-06", "2026-07"],
            "close": [101.5, 202.0, 303.25],
        }
    )
    out = _add_bar_date_d(src)

    # Original columns preserved unchanged.
    assert out.num_rows == 3
    assert out.column("ticker").to_pylist() == ["A.NS", "B.NS", "C.NS"]
    assert out.column("close").to_pylist() == [101.5, 202.0, 303.25]

    # Derived column matches every source string.
    assert out.column("bar_date_d").to_pylist() == [
        date(2026, 6, 19),
        date(2026, 6, 20),
        date(2026, 7, 1),
    ]


def test_add_bar_date_d_column_is_required_date32():
    from scripts.migrate_intraday_partition_spec import _add_bar_date_d

    src = pa.table(
        {
            "ticker": ["A.NS"],
            "bar_date": ["2026-01-02"],
        }
    )
    out = _add_bar_date_d(src)
    field = out.schema.field("bar_date_d")
    assert field.type == pa.date32()
    assert field.nullable is False
