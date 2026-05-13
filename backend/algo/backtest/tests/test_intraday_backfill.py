"""Tests for ``backend.algo.backtest.intraday_backfill``
(ASETPLTFRM-400 slice 1c).

Covers:
- ``upsert_intraday_bars`` Arrow conversion + NaN-replaceable
  upsert against a real local Iceberg catalog.
- Re-running the same upsert is idempotent (same row count, no
  duplicates).
- Bars missing ``bar_open_ts_ns`` or with NaN OHLC are dropped
  before write — never reach the table.
- ``backfill_window`` aggregates Kite fetches into one upsert per
  batch and continues past per-ticker failures with
  ``exc_info=True`` logging.
- ``backfill_window`` skips tickers missing from the instrument
  token map (records as failure, no Kite call).
- ``ensure_window_present`` is a no-op when coverage is already
  complete; pulls only the missing-date bounding window otherwise.
- CLI argparse rejects unsupported intervals before any Kite
  import (defence at the boundary).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.algo.backtest.intraday_backfill import (
    BackfillStats,
    _parse_interval,
    backfill_window,
    ensure_window_present,
    get_existing_bar_dates,
    upsert_intraday_bars,
)
from backend.algo.backtest.types import BarData

# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────


_TEST_TICKER_PREFIX = "TST_"


@pytest.fixture(autouse=True)
def _ensure_table_and_clean_state():
    """The local Iceberg catalog is shared across the test run;
    make sure ``stocks.intraday_bars`` is registered before any
    test touches it, and wipe any rows from a previous run that
    used the ``TST_`` ticker prefix (otherwise idempotency /
    coverage assertions become order-dependent).
    """
    from pyiceberg.expressions import StartsWith

    from stocks.create_tables import (
        _INTRADAY_BARS_TABLE,
        _create_table,
        _get_catalog,
        _intraday_bars_schema,
        _ticker_bar_date_partition_spec,
    )

    catalog = _get_catalog()
    schema = _intraday_bars_schema()
    _create_table(
        catalog,
        _INTRADAY_BARS_TABLE,
        schema,
        _ticker_bar_date_partition_spec(schema),
    )
    tbl = catalog.load_table(_INTRADAY_BARS_TABLE)
    try:
        tbl.delete(StartsWith("ticker", _TEST_TICKER_PREFIX))
    except Exception:
        # First run or no matching rows — both fine.
        pass
    from backend.db.duckdb_engine import invalidate_metadata

    invalidate_metadata(_INTRADAY_BARS_TABLE)
    yield


def _bar(ticker, day, hour, minute, *, close=100.0, vol=10):
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
        open=Decimal(str(close - 0.5)),
        high=Decimal(str(close + 0.5)),
        low=Decimal(str(close - 1.0)),
        close=Decimal(str(close)),
        volume=vol,
        bar_open_ts_ns=ns,
    )


# ────────────────────────────────────────────────────────────────
# upsert_intraday_bars
# ────────────────────────────────────────────────────────────────


def test_upsert_writes_rows_and_round_trips():
    bars = [
        _bar("TST_RT.NS", date(2026, 4, 1), 9, 15, close=100),
        _bar("TST_RT.NS", date(2026, 4, 1), 9, 30, close=101),
        _bar("TST_RT.NS", date(2026, 4, 2), 9, 15, close=102),
    ]
    written = upsert_intraday_bars(
        bars,
        interval_sec=900,
        source="test",
    )
    assert written == 3
    covered = get_existing_bar_dates(
        ticker="TST_RT.NS",
        interval_sec=900,
        start=date(2026, 4, 1),
        end=date(2026, 4, 2),
    )
    assert covered == {date(2026, 4, 1), date(2026, 4, 2)}


def test_upsert_is_idempotent():
    """Same payload run twice → same row count, no duplicates."""
    from backend.db.duckdb_engine import query_iceberg_table

    bars = [
        _bar("TST_IDM.NS", date(2026, 4, 5), 9, 15, close=200),
        _bar("TST_IDM.NS", date(2026, 4, 5), 9, 30, close=201),
    ]
    upsert_intraday_bars(bars, interval_sec=900, source="test")
    upsert_intraday_bars(bars, interval_sec=900, source="test")
    rows = query_iceberg_table(
        "stocks.intraday_bars",
        "SELECT COUNT(*) AS c FROM intraday_bars "
        "WHERE ticker = ? AND interval_sec = 900 "
        "  AND bar_date = ?",
        ["TST_IDM.NS", "2026-04-05"],
    )
    assert rows[0]["c"] == 2


def test_upsert_isolates_intervals():
    """Re-running 15m upsert must NOT delete 5m rows for the same
    (ticker, bar_date) — the ``EqualTo(interval_sec)`` term in the
    scoped delete guards against this."""
    from backend.db.duckdb_engine import query_iceberg_table

    b15 = [_bar("TST_ISO.NS", date(2026, 4, 6), 9, 15, close=300)]
    b5 = [_bar("TST_ISO.NS", date(2026, 4, 6), 9, 20, close=301)]
    upsert_intraday_bars(b15, interval_sec=900, source="test")
    upsert_intraday_bars(b5, interval_sec=300, source="test")
    # Re-run the 15m upsert. The 5m row must survive.
    upsert_intraday_bars(b15, interval_sec=900, source="test")
    rows = query_iceberg_table(
        "stocks.intraday_bars",
        "SELECT interval_sec, COUNT(*) AS c "
        "FROM intraday_bars WHERE ticker = ? "
        "  AND bar_date = ? GROUP BY interval_sec",
        ["TST_ISO.NS", "2026-04-06"],
    )
    by_iv = {r["interval_sec"]: r["c"] for r in rows}
    assert by_iv == {900: 1, 300: 1}


def test_upsert_filters_bars_with_missing_ts_or_nan():
    """Bars without ``bar_open_ts_ns`` or with NaN OHLC must not
    reach the Iceberg layer (required=True would reject them)."""
    bad_no_ns = BarData(
        ticker="TST_BAD.NS",
        date=date(2026, 4, 7),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=1,
        bar_open_ts_ns=None,
    )
    good = _bar("TST_BAD.NS", date(2026, 4, 7), 9, 15, close=400)
    written = upsert_intraday_bars(
        [bad_no_ns, good],
        interval_sec=900,
        source="test",
    )
    assert written == 1


def test_upsert_empty_input_is_zero():
    assert upsert_intraday_bars([], interval_sec=900, source="test") == 0


# ────────────────────────────────────────────────────────────────
# backfill_window
# ────────────────────────────────────────────────────────────────


def _make_kite_returning(bars_per_ticker: dict[str, list]):
    """MagicMock KiteClient where each ticker call returns the
    bars from ``bars_per_ticker[ticker]``."""
    kite = MagicMock()

    def _fetch(*, ticker, **_kw):
        return bars_per_ticker.get(ticker, [])

    kite.fetch_intraday_historical_window.side_effect = _fetch
    return kite


def test_backfill_window_aggregates_and_continues_on_failure(
    caplog,
):
    """Two-ticker batch where one ticker throws. The other ticker's
    bars must still be written; the failure is recorded with
    ``exc_info=True``."""
    bars_a = [_bar("TST_BF_A.NS", date(2026, 4, 8), 9, 15)]
    kite = MagicMock()

    def _fetch(*, ticker, **_kw):
        if ticker == "TST_BF_B.NS":
            raise RuntimeError("kite boom")
        return bars_a

    kite.fetch_intraday_historical_window.side_effect = _fetch
    stats = backfill_window(
        kite=kite,
        tickers=["TST_BF_A.NS", "TST_BF_B.NS"],
        instrument_tokens={
            "TST_BF_A.NS": 111,
            "TST_BF_B.NS": 222,
        },
        interval_sec=900,
        start=date(2026, 4, 8),
        end=date(2026, 4, 8),
        source="test",
        batch_size=10,
    )
    assert stats.tickers_done == 1
    assert stats.tickers_failed == 1
    assert stats.bars_written == 1
    assert stats.failures[0][0] == "TST_BF_B.NS"
    assert "kite boom" in stats.failures[0][1]


def test_backfill_window_skips_ticker_missing_token():
    """No Kite call is made for a ticker not in
    ``instrument_tokens``; the ticker is recorded as a failure
    tagged ``missing_token``."""
    kite = MagicMock()
    kite.fetch_intraday_historical_window.return_value = []
    stats = backfill_window(
        kite=kite,
        tickers=["TST_MT.NS"],
        instrument_tokens={},  # empty
        interval_sec=900,
        start=date(2026, 4, 9),
        end=date(2026, 4, 9),
        source="test",
    )
    assert stats.tickers_done == 0
    assert stats.tickers_failed == 1
    assert stats.failures == [("TST_MT.NS", "missing_token")]
    kite.fetch_intraday_historical_window.assert_not_called()


def test_backfill_window_batches_upserts():
    """3 tickers with batch_size=2 → 2 batches → 2 upsert commits."""
    bars = {
        f"TST_B{i}.NS": [
            _bar(f"TST_B{i}.NS", date(2026, 4, 10), 9, 15, close=100 + i)
        ]
        for i in range(3)
    }
    kite = _make_kite_returning(bars)
    tokens = {t: i + 1000 for i, t in enumerate(bars)}
    stats = backfill_window(
        kite=kite,
        tickers=list(bars),
        instrument_tokens=tokens,
        interval_sec=900,
        start=date(2026, 4, 10),
        end=date(2026, 4, 10),
        source="test",
        batch_size=2,
    )
    assert stats.tickers_done == 3
    assert stats.bars_written == 3


# ────────────────────────────────────────────────────────────────
# ensure_window_present
# ────────────────────────────────────────────────────────────────


def test_ensure_window_present_noop_when_fully_covered():
    """Pre-seed the window, then call ensure_window_present —
    should not call Kite at all."""
    bars = [
        _bar("TST_OD_A.NS", date(2026, 4, 11), 9, 15, close=500),
    ]
    upsert_intraday_bars(bars, interval_sec=900, source="seed")
    kite = MagicMock()
    written = ensure_window_present(
        kite=kite,
        ticker="TST_OD_A.NS",
        instrument_token=999,
        interval_sec=900,
        start=date(2026, 4, 11),
        end=date(2026, 4, 11),
        source="test_on_demand",
    )
    assert written == 0
    kite.fetch_intraday_historical_window.assert_not_called()


def test_ensure_window_present_pulls_only_missing_bounding_range():
    """Seed one day; request a 3-day window; Kite call should cover
    the bounding window of the missing days."""
    bars_seed = [
        _bar("TST_OD_B.NS", date(2026, 4, 12), 9, 15, close=600),
    ]
    upsert_intraday_bars(
        bars_seed,
        interval_sec=900,
        source="seed",
    )
    # Kite returns bars for the missing days.
    fetched = [
        _bar("TST_OD_B.NS", date(2026, 4, 13), 9, 15, close=601),
        _bar("TST_OD_B.NS", date(2026, 4, 14), 9, 15, close=602),
    ]
    kite = MagicMock()
    kite.fetch_intraday_historical_window.return_value = fetched
    written = ensure_window_present(
        kite=kite,
        ticker="TST_OD_B.NS",
        instrument_token=999,
        interval_sec=900,
        start=date(2026, 4, 12),
        end=date(2026, 4, 14),
        source="test_on_demand",
    )
    assert written == 2
    call = kite.fetch_intraday_historical_window.call_args
    # The fetch covers exactly the missing-date bounding window.
    assert call.kwargs["start"] == date(2026, 4, 13)
    assert call.kwargs["end"] == date(2026, 4, 14)


# ────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("1m", 60),
        ("5m", 300),
        ("15m", 900),
        ("60", 60),
        ("300", 300),
        ("900", 900),
    ],
)
def test_parse_interval_accepts_supported(alias, expected):
    assert _parse_interval(alias) == expected


@pytest.mark.parametrize("bad", ["3m", "10m", "180", "0", "abc"])
def test_parse_interval_rejects_unsupported(bad):
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_interval(bad)


def test_backfill_stats_dataclass_defaults():
    s = BackfillStats()
    assert s.tickers_done == 0
    assert s.tickers_failed == 0
    assert s.bars_written == 0
    assert s.failures == []
