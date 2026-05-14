"""Tests for ``backend.algo.backtest.index_intraday_backfill``
(ASETPLTFRM-402 / FE-6).

Covers:
- ``backfill_index_window`` happy path: Arrow conversion +
  upsert into ``stocks.index_intraday_bars``, scoped pre-delete
  fires with the resolved index tradingsymbols.
- Per-symbol failure isolation: one symbol's Kite call raises but
  the rest of the batch still upserts.
- Missing-token isolation: a symbol not in ``algo.instruments``
  is recorded as ``("SYM", "missing_token")`` and never reaches
  Kite.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.algo.backtest.index_intraday_backfill import (
    backfill_index_window,
    upsert_index_intraday_bars,
)
from backend.algo.backtest.types import BarData

# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────


_TEST_TICKER_PREFIX = "TST_IDX_"


@pytest.fixture(autouse=True)
def _ensure_table_and_clean_state():
    """The local Iceberg catalog is shared across the test run;
    make sure ``stocks.index_intraday_bars`` is registered before
    any test touches it and wipe rows from a previous run that
    used the test prefix.
    """
    from pyiceberg.expressions import StartsWith

    from stocks.create_tables import (
        _INDEX_INTRADAY_BARS_TABLE,
        _create_table,
        _get_catalog,
        _index_intraday_bars_schema,
        _ticker_year_month_partition_spec,
    )

    catalog = _get_catalog()
    schema = _index_intraday_bars_schema()
    _create_table(
        catalog,
        _INDEX_INTRADAY_BARS_TABLE,
        schema,
        _ticker_year_month_partition_spec(schema),
    )
    tbl = catalog.load_table(_INDEX_INTRADAY_BARS_TABLE)
    try:
        tbl.delete(StartsWith("ticker", _TEST_TICKER_PREFIX))
    except Exception:
        pass
    from backend.db.duckdb_engine import invalidate_metadata

    invalidate_metadata(_INDEX_INTRADAY_BARS_TABLE)
    yield


def _bar(ticker: str, day: date, hour: int, minute: int, *, close=22000.0):
    """Build a BarData with ``bar_open_ts_ns`` populated."""
    from datetime import timedelta as _td

    ist = timezone(_td(minutes=330))
    open_dt = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=ist,
    )
    ns = int(
        open_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000,
    )
    return BarData(
        ticker=ticker,
        date=day,
        open=Decimal(str(close - 5.0)),
        high=Decimal(str(close + 5.0)),
        low=Decimal(str(close - 10.0)),
        close=Decimal(str(close)),
        # Indices have no traded volume — Kite reports 0 — but the
        # column is required=True so the writer coerces to int.
        volume=0,
        bar_open_ts_ns=ns,
    )


def _fake_pg_session_with_tokens(token_map: dict[str, int]):
    """Build an AsyncMock session whose ``execute(...)`` returns
    the supplied {tradingsymbol: instrument_token} rows.

    Mirrors how ``resolve_index_instrument_tokens`` consumes the
    SQLAlchemy result: ``.mappings().all()`` ⇒ list[dict].
    """
    session = AsyncMock()
    result = MagicMock()
    mappings = MagicMock()
    mappings.all = MagicMock(
        return_value=[
            {"tradingsymbol": sym, "instrument_token": tok}
            for sym, tok in token_map.items()
        ]
    )
    result.mappings = MagicMock(return_value=mappings)
    session.execute = AsyncMock(return_value=result)
    return session


# ────────────────────────────────────────────────────────────────
# backfill_index_window
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_writes_arrow_to_iceberg():
    """3 symbols × one trading day each — all upsert cleanly into
    ``stocks.index_intraday_bars``."""
    from backend.db.duckdb_engine import query_iceberg_table

    symbols = [
        f"{_TEST_TICKER_PREFIX}NIFTY",
        f"{_TEST_TICKER_PREFIX}BANK",
        f"{_TEST_TICKER_PREFIX}IT",
    ]
    tokens = {sym: 100 + i for i, sym in enumerate(symbols)}
    bars_by_sym = {
        sym: [_bar(sym, date(2026, 4, 14), 9, 15, close=22000 + i)]
        for i, sym in enumerate(symbols)
    }
    kite = MagicMock()

    def _fetch(*, ticker, **_kw):
        return bars_by_sym.get(ticker, [])

    kite.fetch_intraday_historical_window.side_effect = _fetch
    session = _fake_pg_session_with_tokens(tokens)

    stats = await backfill_index_window(
        index_symbols=symbols,
        interval_sec=900,
        period_start=date(2026, 4, 14),
        period_end=date(2026, 4, 14),
        kite_client=kite,
        pg_session=session,
        source="test_index",
        batch_size=10,
    )

    assert stats.tickers_done == 3
    assert stats.tickers_failed == 0
    assert stats.bars_written == 3
    rows = query_iceberg_table(
        "stocks.index_intraday_bars",
        "SELECT ticker, year_month, interval_sec "
        "FROM index_intraday_bars "
        "WHERE ticker LIKE ? AND bar_date = ?",
        [f"{_TEST_TICKER_PREFIX}%", "2026-04-14"],
    )
    assert len(rows) == 3
    on_disk_tickers = sorted(r["ticker"] for r in rows)
    assert on_disk_tickers == sorted(symbols)
    # year_month derived from bar_date by the writer.
    assert all(r["year_month"] == "2026-04" for r in rows)
    assert all(r["interval_sec"] == 900 for r in rows)


@pytest.mark.asyncio
async def test_per_symbol_failure_continues_batch():
    """One symbol's Kite call raises — the other two still write."""
    from backend.db.duckdb_engine import query_iceberg_table

    symbols = [
        f"{_TEST_TICKER_PREFIX}OK1",
        f"{_TEST_TICKER_PREFIX}BAD",
        f"{_TEST_TICKER_PREFIX}OK2",
    ]
    tokens = {sym: 200 + i for i, sym in enumerate(symbols)}
    good_bars = {
        f"{_TEST_TICKER_PREFIX}OK1": [
            _bar(f"{_TEST_TICKER_PREFIX}OK1", date(2026, 4, 15), 9, 15)
        ],
        f"{_TEST_TICKER_PREFIX}OK2": [
            _bar(f"{_TEST_TICKER_PREFIX}OK2", date(2026, 4, 15), 9, 30)
        ],
    }
    kite = MagicMock()

    def _fetch(*, ticker, **_kw):
        if ticker == f"{_TEST_TICKER_PREFIX}BAD":
            raise RuntimeError("kite boom")
        return good_bars.get(ticker, [])

    kite.fetch_intraday_historical_window.side_effect = _fetch
    session = _fake_pg_session_with_tokens(tokens)

    stats = await backfill_index_window(
        index_symbols=symbols,
        interval_sec=900,
        period_start=date(2026, 4, 15),
        period_end=date(2026, 4, 15),
        kite_client=kite,
        pg_session=session,
        source="test_index",
        batch_size=10,
    )

    assert stats.tickers_done == 2
    assert stats.tickers_failed == 1
    assert stats.bars_written == 2
    fail_sym = stats.failures[0][0]
    assert fail_sym == f"{_TEST_TICKER_PREFIX}BAD"
    assert "kite boom" in stats.failures[0][1]
    rows = query_iceberg_table(
        "stocks.index_intraday_bars",
        "SELECT ticker FROM index_intraday_bars "
        "WHERE ticker LIKE ? AND bar_date = ?",
        [f"{_TEST_TICKER_PREFIX}%", "2026-04-15"],
    )
    on_disk = sorted(r["ticker"] for r in rows)
    assert on_disk == [
        f"{_TEST_TICKER_PREFIX}OK1",
        f"{_TEST_TICKER_PREFIX}OK2",
    ]


@pytest.mark.asyncio
async def test_missing_instrument_token_logs_and_skips():
    """A symbol not in ``algo.instruments`` is recorded with the
    ``missing_token`` reason and Kite is never called for it."""
    symbols = [
        f"{_TEST_TICKER_PREFIX}HAS",
        f"{_TEST_TICKER_PREFIX}NOT",
    ]
    # Only one symbol resolves.
    tokens = {f"{_TEST_TICKER_PREFIX}HAS": 999}
    bars = {
        f"{_TEST_TICKER_PREFIX}HAS": [
            _bar(f"{_TEST_TICKER_PREFIX}HAS", date(2026, 4, 16), 9, 15)
        ]
    }
    kite = MagicMock()
    kite.fetch_intraday_historical_window.side_effect = (
        lambda *, ticker, **_kw: bars.get(ticker, [])
    )
    session = _fake_pg_session_with_tokens(tokens)

    stats = await backfill_index_window(
        index_symbols=symbols,
        interval_sec=900,
        period_start=date(2026, 4, 16),
        period_end=date(2026, 4, 16),
        kite_client=kite,
        pg_session=session,
        source="test_index",
        batch_size=10,
    )

    assert stats.tickers_done == 1
    assert stats.tickers_failed == 1
    assert (
        f"{_TEST_TICKER_PREFIX}NOT",
        "missing_token",
    ) in stats.failures
    # Kite called only once — never for the missing-token symbol.
    called_for = {
        c.kwargs["ticker"]
        for c in kite.fetch_intraday_historical_window.call_args_list
    }
    assert called_for == {f"{_TEST_TICKER_PREFIX}HAS"}


# ────────────────────────────────────────────────────────────────
# upsert_index_intraday_bars (round-trip sanity)
# ────────────────────────────────────────────────────────────────


def test_upsert_index_intraday_bars_round_trips():
    from backend.db.duckdb_engine import query_iceberg_table

    sym = f"{_TEST_TICKER_PREFIX}RT"
    bars = [
        _bar(sym, date(2026, 4, 17), 9, 15, close=24000),
        _bar(sym, date(2026, 4, 17), 9, 30, close=24010),
    ]
    written = upsert_index_intraday_bars(
        bars,
        interval_sec=900,
        source="test_index",
    )
    assert written == 2
    rows = query_iceberg_table(
        "stocks.index_intraday_bars",
        "SELECT COUNT(*) AS c FROM index_intraday_bars "
        "WHERE ticker = ? AND bar_date = ?",
        [sym, "2026-04-17"],
    )
    assert rows[0]["c"] == 2


def test_upsert_empty_input_is_zero():
    assert (
        upsert_index_intraday_bars(
            [], interval_sec=900, source="test_index"
        )
        == 0
    )
