"""HTTP-level tests for /v1/tickers/bulk + /v1/tickers/all."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from auth.endpoints.ticker_routes import (
    _bulk_link_impl,
    _unlink_all_impl,
    BulkTickerResponse,
)


def _csv_bytes(rows: list[str], header: str = "ticker") -> bytes:
    lines = [header, *rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_bulk_link_happy_path_via_csv_file():
    uid = str(uuid4())
    fake_repo = MagicMock()
    fake_repo.bulk_link_tickers = AsyncMock(
        return_value=(["AAPL", "MSFT", "RELIANCE.NS"], []),
    )
    csv = _csv_bytes(["AAPL", "MSFT", "RELIANCE.NS"])
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=fake_repo,
    ), patch(
        "auth.endpoints.ticker_routes._invalidate_watchlist_cache",
    ):
        out = await _bulk_link_impl(
            user_id=uid,
            csv_bytes=csv,
            filename="test.csv",
        )
    assert isinstance(out, BulkTickerResponse)
    assert sorted(out.added) == [
        "AAPL", "MSFT", "RELIANCE.NS",
    ]
    assert out.errors == []
    assert out.total_rows == 3


@pytest.mark.asyncio
async def test_bulk_link_skips_already_linked():
    uid = str(uuid4())
    fake_repo = MagicMock()
    fake_repo.bulk_link_tickers = AsyncMock(
        return_value=(["AAPL", "GOOG"], ["MSFT"]),
    )
    csv = _csv_bytes(["AAPL", "MSFT", "GOOG"])
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=fake_repo,
    ), patch(
        "auth.endpoints.ticker_routes._invalidate_watchlist_cache",
    ):
        out = await _bulk_link_impl(
            user_id=uid,
            csv_bytes=csv,
            filename="test.csv",
        )
    assert sorted(out.added) == ["AAPL", "GOOG"]
    assert out.skipped_already_linked == ["MSFT"]
    assert out.errors == []


@pytest.mark.asyncio
async def test_bulk_link_reports_invalid_tickers():
    uid = str(uuid4())
    fake_repo = MagicMock()
    fake_repo.bulk_link_tickers = AsyncMock(
        return_value=(["AAPL", "RELIANCE.NS"], []),
    )
    csv = _csv_bytes(["AAPL", "BAD$$", "", "RELIANCE.NS"])
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=fake_repo,
    ), patch(
        "auth.endpoints.ticker_routes._invalidate_watchlist_cache",
    ):
        out = await _bulk_link_impl(
            user_id=uid,
            csv_bytes=csv,
            filename="test.csv",
        )
    assert sorted(out.added) == ["AAPL", "RELIANCE.NS"]
    # 2 invalid rows: "BAD$$" and "" (empty)
    assert len(out.errors) == 2
    rows = sorted(e.row for e in out.errors)
    assert rows == [3, 4]
    assert out.total_rows == 4


@pytest.mark.asyncio
async def test_bulk_link_rejects_csv_without_ticker_column():
    uid = str(uuid4())
    csv = "symbol,name\nAAPL,Apple\n".encode("utf-8")
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=MagicMock(),
    ):
        with pytest.raises(HTTPException) as exc:
            await _bulk_link_impl(
                user_id=uid,
                csv_bytes=csv,
                filename="bad.csv",
            )
    assert exc.value.status_code == 400
    assert "ticker" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_bulk_link_rejects_over_5000_rows():
    uid = str(uuid4())
    csv = _csv_bytes([f"TKR{i}" for i in range(5001)])
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=MagicMock(),
    ):
        with pytest.raises(HTTPException) as exc:
            await _bulk_link_impl(
                user_id=uid,
                csv_bytes=csv,
                filename="too-big.csv",
            )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_unlink_all_requires_exact_confirm_phrase():
    uid = str(uuid4())
    fake_repo = MagicMock()
    fake_repo.unlink_all_tickers = AsyncMock(return_value=4)
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=fake_repo,
    ), patch(
        "auth.endpoints.ticker_routes._invalidate_watchlist_cache",
    ):
        # Wrong phrase.
        with pytest.raises(HTTPException) as exc:
            await _unlink_all_impl(
                user_id=uid, confirm="remove all",
            )
        assert exc.value.status_code == 400
        # Exact phrase.
        out = await _unlink_all_impl(
            user_id=uid, confirm="REMOVE ALL",
        )
        assert out.removed == 4
