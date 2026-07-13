from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.jobs.entry_quality_snapshot import (
    _append_snapshot_rows,
    _run,
)


@pytest.mark.asyncio
async def test_snapshot_job_writes_expected_row_count():
    ohlcv_df = pd.DataFrame(
        {
            "ticker": ["TCS.NS"] * 300,
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "open": [100.0] * 300,
            "high": [101.0] * 300,
            "low": [99.0] * 300,
            "close": [100.0] * 300,
            "volume": [1_000_000.0] * 300,
        }
    )
    nifty_df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "close": [100.0] * 300,
        }
    )

    with (
        patch(
            "backend.jobs.entry_quality_snapshot.disposable_pg_session"
        ) as mock_pg,
        patch(
            "backend.jobs.entry_quality_snapshot.query_iceberg_df",
            new_callable=AsyncMock,
        ) as mock_query,
        patch(
            "backend.jobs.entry_quality_snapshot._append_snapshot_rows"
        ) as mock_append,
    ):
        # MagicMock base + explicit AsyncMock only on ``execute`` —
        # matches the established PG-session mock convention in
        # test_closed_trades_rollup.py::fake_session. A freshly
        # created ``AsyncMock()``'s own ``.return_value`` is ALSO an
        # AsyncMock by default (cascades recursively), so ``execute``
        # .return_value``.fetchall`` would come back an
        # awaitable coroutine — but the real SQLAlchemy Result
        # object's ``.fetchall()`` is a SYNC method. Explicitly pin
        # ``execute.return_value`` to a plain ``MagicMock`` to break
        # that cascade and match production semantics.
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("TCS.NS",)]
        mock_session.execute.return_value = mock_result
        mock_pg.return_value.__aenter__.return_value = mock_session
        mock_query.side_effect = [ohlcv_df, nifty_df]

        result = await _run({})

    assert result["rows_written"] == 1
    mock_append.assert_called_once()
    # Task 15: qm_score must be a real computed value now, not the
    # None placeholder Task 14 shipped.
    written_rows = mock_append.call_args.args[0]
    assert written_rows[0]["qm_score"] is not None


def test_append_snapshot_rows_scopes_delete_on_ticker_and_trade_date():
    """Re-triggering the job for a ``trade_date`` already written
    MUST NOT duplicate rows — the pre-delete predicate scopes on
    the incoming batch's ``(ticker, trade_date)`` pairs, mirroring
    ``daily_features_daily_compute.py``'s NaN-replaceable upsert
    pattern (idempotency-gap review finding)."""
    import pyarrow as pa
    from pyiceberg.expressions import And

    rows = [
        {
            "trade_date": date(2026, 7, 12),
            "ticker": "TCS.NS",
            "market": "india",
        }
    ]
    arrow_schema = pa.schema(
        [
            ("trade_date", pa.date32()),
            ("ticker", pa.string()),
            ("market", pa.string()),
        ]
    )
    mock_tbl = MagicMock()
    mock_tbl.schema.return_value.as_arrow.return_value = arrow_schema
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    with (
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch("backend.jobs.entry_quality_snapshot.invalidate_metadata"),
    ):
        _append_snapshot_rows(rows)

    mock_tbl.delete.assert_called_once()
    pred = mock_tbl.delete.call_args.args[0]
    assert isinstance(pred, And)

    seen_refs: set[str] = set()

    def _walk(p):
        if isinstance(p, And):
            _walk(p.left)
            _walk(p.right)
            return
        term = getattr(p, "term", None)
        if term is not None:
            name = getattr(term, "name", None)
            if name:
                seen_refs.add(name)

    _walk(pred)
    assert "ticker" in seen_refs, f"got {seen_refs}"
    assert "trade_date" in seen_refs, f"got {seen_refs}"
    mock_tbl.append.assert_called_once()


def test_delete_predicate_is_exact_not_cross_product():
    """Regression test for the ticker/trade_date cross-product data
    -loss bug: ``trade_date`` is computed independently per ticker
    (``grp["date"].iloc[-1].date()``), so a batch can legitimately
    contain two tickers with two DIFFERENT trade_dates (e.g. one
    ticker's OHLCV ingestion lagged). The old
    ``And(In(tickers), In(trade_dates))`` predicate would match
    ALL 4 (ticker, trade_date) combinations — including
    (TCS.NS, 2026-07-11) and (INFY.NS, 2026-07-12), neither of
    which is actually in the batch — silently deleting valid
    historical rows. The fixed predicate must match only the 2
    pairs actually written."""
    from backend.jobs.entry_quality_snapshot import _delete_predicate

    d1 = date(2026, 7, 12)
    d2 = date(2026, 7, 11)  # INFY.NS lagged a day behind TCS.NS
    rows = [
        {"trade_date": d1, "ticker": "TCS.NS", "market": "india"},
        {"trade_date": d2, "ticker": "INFY.NS", "market": "india"},
    ]

    pred = _delete_predicate(rows)

    def _matches(p, ticker: str, trade_date: date) -> bool:
        """Evaluate the predicate tree against a single
        (ticker, trade_date) row-shaped dict, mirroring how
        PyIceberg's row-filter evaluation walks And/Or/EqualTo."""
        from pyiceberg.expressions import And, EqualTo, Or

        if isinstance(p, And):
            return _matches(p.left, ticker, trade_date) and _matches(
                p.right, ticker, trade_date
            )
        if isinstance(p, Or):
            return _matches(p.left, ticker, trade_date) or _matches(
                p.right, ticker, trade_date
            )
        if isinstance(p, EqualTo):
            name = p.term.name
            if name == "ticker":
                return ticker == p.literal.value
            if name == "trade_date":
                # DateLiteral.value is epoch-day int, not a
                # date — round-trip through date.fromordinal.
                epoch_days = p.literal.value
                lit_date = date(1970, 1, 1) + timedelta(days=epoch_days)
                return trade_date == lit_date
            raise AssertionError(f"unexpected column {name}")
        raise AssertionError(f"unexpected node {p!r}")

    # The 2 pairs actually in the batch MUST match.
    assert _matches(pred, "TCS.NS", d1)
    assert _matches(pred, "INFY.NS", d2)

    # The cross-product combinations that were NEVER written by
    # any run and are NOT part of this batch MUST NOT match — this
    # is the exact case the old In()xIn() predicate got wrong.
    assert not _matches(pred, "TCS.NS", d2)
    assert not _matches(pred, "INFY.NS", d1)


def test_delete_predicate_none_for_empty_rows():
    """Zero rows must short-circuit to ``None`` (no-op delete) —
    the caller (``_run``) already guards ``if rows:`` before
    calling ``_append_snapshot_rows``, but ``_delete_predicate``
    itself must not crash or build a vacuous predicate."""
    from backend.jobs.entry_quality_snapshot import _delete_predicate

    assert _delete_predicate([]) is None


def test_delete_predicate_single_pair_no_or_wrapper():
    """A single (ticker, trade_date) pair must not be wrapped in an
    ``Or`` node — just the bare ``And(EqualTo, EqualTo)``."""
    from pyiceberg.expressions import And, Or

    from backend.jobs.entry_quality_snapshot import _delete_predicate

    rows = [
        {"trade_date": date(2026, 7, 12), "ticker": "TCS.NS"},
    ]
    pred = _delete_predicate(rows)
    assert isinstance(pred, And)
    assert not isinstance(pred, Or)


@pytest.mark.asyncio
async def test_snapshot_job_uses_detect_market_not_hardcoded_india():
    """``market`` MUST come from ``detect_market(ticker)`` (CLAUDE.md
    §4.3 #19) rather than a hardcoded ``"india"`` literal — a US
    ticker in the allowed-tickers universe must be tagged ``"us"``."""
    ohlcv_df = pd.DataFrame(
        {
            "ticker": ["AAPL"] * 300,
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "open": [100.0] * 300,
            "high": [101.0] * 300,
            "low": [99.0] * 300,
            "close": [100.0] * 300,
            "volume": [1_000_000.0] * 300,
        }
    )
    nifty_df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "close": [100.0] * 300,
        }
    )

    with (
        patch(
            "backend.jobs.entry_quality_snapshot.disposable_pg_session"
        ) as mock_pg,
        patch(
            "backend.jobs.entry_quality_snapshot.query_iceberg_df",
            new_callable=AsyncMock,
        ) as mock_query,
        patch(
            "backend.jobs.entry_quality_snapshot._append_snapshot_rows"
        ) as mock_append,
    ):
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("AAPL",)]
        mock_session.execute.return_value = mock_result
        mock_pg.return_value.__aenter__.return_value = mock_session
        mock_query.side_effect = [ohlcv_df, nifty_df]

        await _run({})

    written_rows = mock_append.call_args.args[0]
    assert written_rows[0]["market"] == "us"
