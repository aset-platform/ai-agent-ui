from __future__ import annotations

from datetime import date
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
