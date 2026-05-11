"""Unit tests for GET /v1/algo/live/dashboard-summary."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
_USER_CTX = UserContext(
    user_id=str(_USER_ID), email="t@t.com", role="pro",
)


def _app():
    from fastapi import FastAPI
    from backend.algo.routes.live import create_live_router

    app = FastAPI()
    app.include_router(create_live_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: _USER_CTX
    return app


def _kite_with_balances(open_pnl, day_pnl, cash):
    """Build a KiteClient mock exposing _kc with positions+margins."""
    kc = MagicMock()
    kc.positions.return_value = {
        "net": [
            {
                "tradingsymbol": "ITC", "quantity": 8,
                "pnl": open_pnl,
            },
        ],
        "day": [{"tradingsymbol": "ITC", "pnl": day_pnl}],
    }
    kc.margins.return_value = {
        "available": {"live_balance": cash},
    }
    kite = MagicMock()
    kite._kc = kc
    return kite


class TestDashboardSummary:
    @patch("backend.algo.routes.live._realised_pnl_today")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_happy_path(self, build_kite, realised):
        from fastapi.testclient import TestClient

        build_kite.return_value = _kite_with_balances(
            open_pnl=820.30, day_pnl=1240.50, cash=98432.10,
        )
        realised.return_value = Decimal("0")
        # Force a clean cache miss by patching cache.get to None.
        with patch(
            "backend.algo.routes.live.get_cache",
        ) as gc:
            cache_mock = MagicMock()
            cache_mock.get.return_value = None
            gc.return_value = cache_mock

            client = TestClient(_app())
            resp = client.get("/v1/algo/live/dashboard-summary")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Decimal(body["today_pnl_inr"]) == Decimal("1240.50")
        assert Decimal(body["open_pnl_inr"]) == Decimal("820.30")
        assert Decimal(body["cash_inr"]) == Decimal("98432.10")
        assert body["open_position_count"] == 1
        assert body["mode"] in {"live", "dry_run"}

    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_kite_token_expired_returns_503(self, build_kite):
        from fastapi import HTTPException
        from fastapi.testclient import TestClient

        build_kite.side_effect = HTTPException(
            status_code=503, detail="Kite token expired",
        )
        with patch(
            "backend.algo.routes.live.get_cache",
        ) as gc:
            cache_mock = MagicMock()
            cache_mock.get.return_value = None
            gc.return_value = cache_mock

            client = TestClient(_app())
            resp = client.get("/v1/algo/live/dashboard-summary")

        assert resp.status_code == 503

    @patch("backend.algo.routes.live._realised_pnl_today")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_cache_hit_returns_cached_body(self, build_kite, realised):
        """Cached JSON is returned without hitting Kite."""
        from fastapi.testclient import TestClient

        cached_json = (
            '{"today_pnl_inr":"42","open_pnl_inr":"0",'
            '"realised_pnl_inr":"0","cash_inr":"100",'
            '"open_position_count":0,"mode":"live",'
            '"ws_age_seconds":null,"kill_switch_active":false}'
        )
        with patch(
            "backend.algo.routes.live.get_cache",
        ) as gc:
            cache_mock = MagicMock()
            cache_mock.get.return_value = cached_json
            gc.return_value = cache_mock

            client = TestClient(_app())
            resp = client.get("/v1/algo/live/dashboard-summary")

        assert resp.status_code == 200
        assert Decimal(resp.json()["today_pnl_inr"]) == Decimal("42")
        build_kite.assert_not_called()
