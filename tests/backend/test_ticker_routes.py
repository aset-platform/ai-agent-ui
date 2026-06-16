import pytest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.endpoints import ticker_routes as tr
from auth.dependencies import get_current_user
from auth.models import UserContext


def _stub_user() -> UserContext:
    return UserContext(
        user_id="test-user-id",
        email="test@example.com",
        role="general",
        subscription_tier="free",
        subscription_status="active",
        usage_remaining=None,
    )


def _client_with_user() -> TestClient:
    # tr.router already has prefix="/users/me" baked in;
    # mount under "/v1" so paths become /v1/users/me/tickers/...
    app = FastAPI()
    app.include_router(tr.router, prefix="/v1")
    app.dependency_overrides[get_current_user] = lambda: _stub_user()
    return TestClient(app)


def test_bulk_add_tickers_json_happy(monkeypatch):
    repo = AsyncMock()
    repo.bulk_link_tickers = AsyncMock(return_value=(["TCS.NS"], []))
    monkeypatch.setattr(tr._helpers, "_get_repo", lambda: repo)
    monkeypatch.setattr(tr, "_invalidate_watchlist_cache", lambda u: None)
    c = _client_with_user()
    r = c.post("/v1/users/me/tickers/bulk-add", json={"tickers": ["TCS.NS"]})
    assert r.status_code == 200
    assert r.json()["added"] == ["TCS.NS"]


def test_bulk_add_tickers_empty_400():
    c = _client_with_user()
    r = c.post("/v1/users/me/tickers/bulk-add", json={"tickers": []})
    assert r.status_code == 400


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
            (6, "!BAD"),      # invalid ticker -> error
        ]
        resp = await tr._bulk_link_tickers(
            user_id="u1", rows=rows, source="bulk_json",
        )
    sent = repo.bulk_link_tickers.await_args.args[1]
    assert sent == ["TCS.NS", "INFY.NS", "ITC.NS"]
    assert resp.added == ["TCS.NS", "INFY.NS"]
    assert resp.skipped_already_linked == ["ITC.NS"]
    assert "duplicate in batch" in {e.reason for e in resp.errors}
    assert "!BAD" in [e.ticker for e in resp.errors]
    assert resp.total_rows == 6
    inval.assert_called_once_with("u1")
