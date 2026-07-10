"""Unit tests for off_universe_tickers on GET/PUT
/v1/algo/live/caps/{strategy_id} — ASETPLTFRM-471 input-side
warning: a ticker added to allowed_tickers that's absent from
stocks.universe_snapshot can never populate a bar-derived feature.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
_STRATEGY_ID = UUID("33333333-3333-3333-3333-333333333333")
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


class TestOffUniverseWarning:
    @patch(
        "backend.algo.routes.live.get_off_universe_tickers",
    )
    @patch(
        "backend.algo.routes.live._compute_strategy_commitment",
    )
    @patch("backend.algo.live.caps_repo.CapsRepo.get_or_default")
    def test_get_caps_includes_off_universe_tickers(
        self, mock_get, mock_commitment, mock_off_universe,
    ):
        from fastapi.testclient import TestClient

        mock_get.return_value = {
            "user_id": _USER_ID,
            "strategy_id": _STRATEGY_ID,
            "max_inr": Decimal("100000"),
            "max_orders_per_day": 5,
            "allowed_tickers": ["ITC.NS", "MOVALUE.NS"],
            "live_orders_enabled": False,
            "gtt_limit_headroom_pct": Decimal("0.01"),
        }
        mock_commitment.return_value = (Decimal("0"), 0)
        mock_off_universe.return_value = ["MOVALUE.NS"]

        client = TestClient(_app())
        resp = client.get(
            f"/v1/algo/live/caps/{_STRATEGY_ID}",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["off_universe_tickers"] == ["MOVALUE.NS"]
        mock_off_universe.assert_called_once_with(
            ["ITC.NS", "MOVALUE.NS"],
        )

    @patch(
        "backend.algo.routes.live.get_off_universe_tickers",
    )
    @patch("backend.algo.live.caps_repo.CapsRepo.upsert")
    def test_upsert_caps_includes_off_universe_tickers(
        self, mock_upsert, mock_off_universe,
    ):
        from fastapi.testclient import TestClient

        mock_upsert.return_value = {
            "user_id": _USER_ID,
            "strategy_id": _STRATEGY_ID,
            "max_inr": Decimal("100000"),
            "max_orders_per_day": 5,
            "allowed_tickers": ["SMALLCAP.NS"],
            "live_orders_enabled": False,
            "gtt_limit_headroom_pct": Decimal("0.01"),
            "cumulative_inr_today": Decimal("0"),
            "orders_count_today": 0,
        }
        mock_off_universe.return_value = ["SMALLCAP.NS"]

        client = TestClient(_app())
        resp = client.put(
            f"/v1/algo/live/caps/{_STRATEGY_ID}",
            json={
                "max_inr": "100000",
                "max_orders_per_day": 5,
                "allowed_tickers": ["SMALLCAP.NS"],
            },
        )

        assert resp.status_code == 200
        assert resp.json()["off_universe_tickers"] == ["SMALLCAP.NS"]
