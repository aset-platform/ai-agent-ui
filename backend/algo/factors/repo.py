"""Iceberg CRUD for ``stocks.daily_factors`` with NaN-replaceable
upsert.

Mirrors ``backend/algo/regime/repo.py``. Reads use
``query_iceberg_table`` (DuckDB fast-path); writes use PyIceberg
directly. NaN-replaceable upsert (scoped pre-delete by
``(ticker, bar_date)`` then append) keeps re-runs idempotent.

Cache invalidation: every successful write calls
``get_cache().invalidate("cache:factors:*")`` so any future API
endpoints (REGIME-2b) serve fresh data within one round-trip.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date

import pyarrow as pa
from pyiceberg.expressions import And, In

from backend.algo.factors.iceberg_init import (
    ALL_FACTOR_KEYS,
    DAILY_FACTORS_TABLE,
)
from backend.cache import get_cache
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)

_logger = logging.getLogger(__name__)


@dataclass
class FactorRow:
    ticker: str
    bar_date: date
    values: dict[str, float]
    sector: str | None = None


def _catalog():
    from stocks.create_tables import _get_catalog
    return _get_catalog()


def _arrow_schema() -> pa.Schema:
    """Explicit nullability — REGIME-1 lesson: PyArrow defaults to
    nullable=True, which PyIceberg rejects on append against a
    schema with required columns."""
    fields = [
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("bar_date", pa.date32(), nullable=False),
    ]
    for k in ALL_FACTOR_KEYS:
        fields.append(pa.field(k, pa.float64(), nullable=True))
    fields.append(pa.field("sector", pa.string(), nullable=True))
    return pa.schema(fields)


def _invalidate_factors_cache() -> None:
    """Best-effort wildcard invalidate. Sync; never await."""
    try:
        get_cache().invalidate("cache:factors:*")
    except Exception as exc:  # pragma: no cover
        _logger.debug("factors cache invalidate skipped: %s", exc)


def upsert_factors(rows: list[FactorRow]) -> int:
    """NaN-replaceable upsert keyed on ``(ticker, bar_date)``.

    Pre-deletes the cross-product of incoming tickers × bar_dates,
    then appends the batch. The scoped delete prevents accidental
    cross-ticker overwrite (CLAUDE §4.3 #18).
    """
    if not rows:
        return 0
    cat = _catalog()
    tbl = cat.load_table(DAILY_FACTORS_TABLE)
    tickers = sorted({r.ticker for r in rows})
    dates = sorted({r.bar_date for r in rows})
    try:
        tbl.delete(And(In("ticker", tickers), In("bar_date", dates)))
    except Exception as exc:  # first run on empty table is fine
        _logger.debug("daily_factors pre-delete skipped: %s", exc)

    cols: dict[str, list] = {
        "ticker": [r.ticker for r in rows],
        "bar_date": [r.bar_date for r in rows],
        "sector": [r.sector for r in rows],
    }
    for k in ALL_FACTOR_KEYS:
        cols[k] = [r.values.get(k, float("nan")) for r in rows]
    arrow_tbl = pa.table(cols, schema=_arrow_schema())
    tbl.append(arrow_tbl)
    invalidate_metadata(DAILY_FACTORS_TABLE)
    _invalidate_factors_cache()
    return len(rows)


def get_factors_window(
    tickers: list[str], start: date, end: date,
) -> list[FactorRow]:
    if not tickers:
        return []
    placeholders = ",".join(["?"] * len(tickers))
    cols_sql = ", ".join(
        ["ticker", "bar_date"] + ALL_FACTOR_KEYS + ["sector"],
    )
    rows = query_iceberg_table(
        DAILY_FACTORS_TABLE,
        f"SELECT {cols_sql} FROM daily_factors "
        f"WHERE ticker IN ({placeholders}) "
        f"AND bar_date BETWEEN ? AND ? "
        f"ORDER BY ticker ASC, bar_date ASC",
        [*tickers, start, end],
    )
    out: list[FactorRow] = []
    for r in rows:
        vals = {
            k: r[k] for k in ALL_FACTOR_KEYS
            if r.get(k) is not None and not (
                isinstance(r[k], float) and math.isnan(r[k])
            )
        }
        out.append(FactorRow(
            ticker=r["ticker"],
            bar_date=r["bar_date"],
            values=vals,
            sector=r.get("sector"),
        ))
    return out
