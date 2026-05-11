"""Unit tests for GET /v1/algo/live/holdings."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
_USER_CTX = UserContext(
    user_id=str(_USER_ID), email="t@t.com", role="pro",
)


@pytest.fixture(autouse=True)
def _bypass_cache():
    """Force a clean cache miss for every test in this module."""
    with patch(
        "backend.algo.routes.live.get_cache",
    ) as gc:
        cache_mock = MagicMock()
        cache_mock.get.return_value = None
        gc.return_value = cache_mock
        yield


def _app():
    from fastapi import FastAPI
    from backend.algo.routes.live import create_live_router

    app = FastAPI()
    app.include_router(create_live_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: _USER_CTX
    return app


def _kite_with_holdings(rows):
    kc = MagicMock()
    kc.holdings.return_value = rows
    kite = MagicMock()
    kite._kc = kc
    return kite


class TestHoldings:
    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_holdings_with_days_held(self, build_kite, attr):
        from fastapi.testclient import TestClient

        build_kite.return_value = _kite_with_holdings([
            {
                "tradingsymbol": "ITC", "exchange": "NSE",
                "quantity": 8, "average_price": 305.0,
                "last_price": 311.2, "pnl": 49.6,
            },
        ])
        attr.return_value = {
            "ITC": {
                "strategy_id": "abc-123",
                "strategy_name": "V3",
                "days_held": 3,
            },
        }

        client = TestClient(_app())
        resp = client.get("/v1/algo/live/holdings")

        assert resp.status_code == 200, resp.text
        rows = resp.json()["rows"]
        assert len(rows) == 1
        assert rows[0]["days_held"] == 3
        assert rows[0]["strategy_id"] == "abc-123"
        assert rows[0]["strategy_name"] == "V3"

    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_holdings_without_ledger_match(
        self, build_kite, attr,
    ):
        from fastapi.testclient import TestClient

        build_kite.return_value = _kite_with_holdings([
            {
                "tradingsymbol": "EXTERN", "exchange": "NSE",
                "quantity": 5, "average_price": 100,
                "last_price": 110, "pnl": 50,
            },
        ])
        attr.return_value = {}

        client = TestClient(_app())
        resp = client.get("/v1/algo/live/holdings")

        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert rows[0]["strategy_id"] is None
        assert rows[0]["days_held"] is None

    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_holdings_filters_zero_quantity(
        self, build_kite, attr,
    ):
        from fastapi.testclient import TestClient

        build_kite.return_value = _kite_with_holdings([
            {
                "tradingsymbol": "A", "exchange": "NSE",
                "quantity": 2, "average_price": 100,
                "last_price": 105, "pnl": 10,
            },
            {
                "tradingsymbol": "B", "exchange": "NSE",
                "quantity": 0, "average_price": 0,
                "last_price": 0, "pnl": 0,
            },
        ])
        attr.return_value = {}

        client = TestClient(_app())
        resp = client.get("/v1/algo/live/holdings")

        rows = resp.json()["rows"]
        assert len(rows) == 1
        assert rows[0]["tradingsymbol"] == "A"
