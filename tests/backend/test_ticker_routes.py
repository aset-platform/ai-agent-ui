import pytest
from unittest.mock import AsyncMock, patch

from auth.endpoints import ticker_routes as tr


@pytest.mark.asyncio
async def test_bulk_link_tickers_dedupes_and_validates():
    repo = AsyncMock()
    repo.bulk_link_tickers = AsyncMock(
        return_value=(["TCS.NS", "INFY.NS"], ["ITC.NS"]),
    )
    with patch.object(tr._helpers, "_get_repo", return_value=repo), \
         patch.object(tr, "_invalidate_watchlist_cache") as inval:
        rows = [
            (1, "tcs.ns"),    # normalised -> TCS.NS
            (2, "INFY.NS"),
            (3, "ITC.NS"),    # repo reports already-linked
            (4, "tcs.ns"),    # in-batch dup -> error
            (5, ""),          # empty -> error
        ]
        resp = await tr._bulk_link_tickers(
            user_id="u1", rows=rows, source="bulk_json", total_rows=5,
        )
    sent = repo.bulk_link_tickers.await_args.args[1]
    assert sent == ["TCS.NS", "INFY.NS", "ITC.NS"]
    assert resp.added == ["TCS.NS", "INFY.NS"]
    assert resp.skipped_already_linked == ["ITC.NS"]
    assert "duplicate in batch" in {e.reason for e in resp.errors}
    assert resp.total_rows == 5
    inval.assert_called_once_with("u1")
