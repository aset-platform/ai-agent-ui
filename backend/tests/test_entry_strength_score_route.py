"""Route-level test: ESS + Nifty market context wired into the
watchlist-stocks route (``insights_routes.get_watchlist_stocks``).

``get_watchlist_stocks`` is a closure registered on the router
inside ``create_insights_router()`` — it is not a module-level
attribute of ``insights_routes``, so it can't be imported and
awaited directly. Instead we mount the real router on a bare
FastAPI app and drive it via ``TestClient``, overriding the auth
dependency with seeded user context — mirrors the fixture pattern
in ``tests/test_advanced_analytics_swing.py``.

``query_iceberg_df`` is imported inside the route body (``from
backend.db.duckdb_engine import query_iceberg_df``) on every call,
so per the "patch at SOURCE module" convention
(``mock-patching-gotchas``) we patch
``backend.db.duckdb_engine.query_iceberg_df`` rather than any
attribute of ``insights_routes``. It's a plain (sync) function, not
a coroutine, so a plain ``Mock`` — not ``AsyncMock`` — is used.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.insights_routes as ir
from auth.dependencies import get_current_user
from auth.models import UserContext


class _NoOpCache:
    def get(self, _k):
        return None

    def set(self, _k, _v, ttl=None):
        return None


def _build_watchlist_test_app(
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    app = FastAPI()
    router = ir.create_insights_router()
    app.include_router(router, prefix="/v1")

    def _ctx() -> UserContext:
        return UserContext(
            user_id="ess-route-test-user",
            email="ess-route@test",
            role="pro",
        )

    app.dependency_overrides[get_current_user] = _ctx
    monkeypatch.setattr(ir, "get_cache", lambda: _NoOpCache())

    return TestClient(app)


def _make_ohlcv_df() -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=300, freq="D")
    rows = []
    for i, d in enumerate(dates):
        rows.append(
            {
                "ticker": "TCS.NS",
                "date": d,
                "open": 100.0 + i * 0.05,
                "high": 101.0 + i * 0.05,
                "low": 99.0 + i * 0.05,
                "close": 100.0 + i * 0.05,
                "volume": 1_000_000.0,
            }
        )
    return pd.DataFrame(rows)


def _make_nifty_df() -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=300, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "close": [20000.0 + i * 2.0 for i in range(300)],
        }
    )


def test_watchlist_stocks_includes_ess_fields(
    monkeypatch: pytest.MonkeyPatch,
):
    """ESS + market-context fields flow through the real route."""
    ohlcv_df = _make_ohlcv_df()
    nifty_df = _make_nifty_df()

    client = _build_watchlist_test_app(monkeypatch)

    with (
        patch(
            "backend.db.duckdb_engine.query_iceberg_df",
            side_effect=[ohlcv_df, nifty_df],
        ),
        patch.object(
            ir, "_scoped_tickers", new_callable=AsyncMock
        ) as mock_scoped,
        patch.object(ir, "_is_indian_market_hours", return_value=False),
    ):
        mock_scoped.return_value = ["TCS.NS"]
        resp = client.get("/v1/insights/watchlist-stocks?market=india")

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["market_context"] is not None
    mc = body["market_context"]
    assert mc["nifty_return_pct"] is not None

    assert len(body["stocks"]) == 1
    row = body["stocks"][0]
    assert row["ticker"] == "TCS.NS"
    assert row["ess_score"] is not None
    assert row["ess_gate_passed"] is not None


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
