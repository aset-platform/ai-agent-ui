"""Unit tests for GET /v1/algo/live/positions."""
from __future__ import annotations

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


def _kite_with_positions(net_rows):
    kc = MagicMock()
    kc.positions.return_value = {"net": net_rows}
    kite = MagicMock()
    kite._kc = kc
    return kite


class TestPositions:
    @patch("backend.algo.routes.live._ledger_kite_drift")
    @patch("backend.algo.routes.live._fetch_strategy_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_positions_joined_with_strategy(
        self, build_kite, attr, drift,
    ):
        from fastapi.testclient import TestClient

        build_kite.return_value = _kite_with_positions([
            {
                "tradingsymbol": "ITC", "exchange": "NSE",
                "quantity": 8, "average_price": 307.33,
                "last_price": 311.20, "pnl": 30.96,
                "product": "MIS",
            },
            {
                "tradingsymbol": "EXIT", "exchange": "NSE",
                "quantity": 0, "average_price": 0,
                "last_price": 0, "pnl": 0, "product": "MIS",
            },
        ])
        attr.return_value = {
            ("ITC", "MIS"): {
                "strategy_id": "abc-123",
                "strategy_name": "V3 Multi",
                "entry_ts_utc": "2026-05-11T04:19:54+00:00",
                "entry_reason": "BULL · momentum_z=1.4",
            },
        }
        drift.return_value = False

        client = TestClient(_app())
        resp = client.get("/v1/algo/live/positions")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ledger_drift"] is False
        rows = body["rows"]
        # quantity=0 row filtered out
        assert len(rows) == 1
        assert rows[0]["tradingsymbol"] == "ITC"
        assert rows[0]["strategy_id"] == "abc-123"
        assert rows[0]["entry_reason"].startswith("BULL")
        # pnl_pct ≈ ((311.20 - 307.33)/307.33)*100 ≈ 1.26
        from decimal import Decimal
        assert Decimal(rows[0]["pnl_pct"]).quantize(
            Decimal("0.01")
        ) == Decimal("1.26")

    @patch("backend.algo.routes.live._ledger_kite_drift")
    @patch("backend.algo.routes.live._fetch_strategy_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_positions_without_attribution(
        self, build_kite, attr, drift,
    ):
        from fastapi.testclient import TestClient

        build_kite.return_value = _kite_with_positions([
            {
                "tradingsymbol": "MANUAL", "exchange": "NSE",
                "quantity": 1, "average_price": 100,
                "last_price": 101, "pnl": 1, "product": "MIS",
            },
        ])
        attr.return_value = {}
        drift.return_value = False

        client = TestClient(_app())
        resp = client.get("/v1/algo/live/positions")

        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert rows[0]["strategy_id"] is None
        assert rows[0]["entry_reason"] is None
        assert rows[0]["strategy_name"] is None

    @patch("backend.algo.routes.live._build_kite_client_for_user")
    def test_positions_token_expired_returns_503(self, build_kite):
        from fastapi import HTTPException
        from fastapi.testclient import TestClient

        build_kite.side_effect = HTTPException(
            status_code=503, detail="Kite token expired",
        )

        client = TestClient(_app())
        resp = client.get("/v1/algo/live/positions")

        assert resp.status_code == 503
