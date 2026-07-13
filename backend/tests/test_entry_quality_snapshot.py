from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.jobs.entry_quality_snapshot import _run


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
