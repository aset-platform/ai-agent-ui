"""panic_close_all must recover real open quantities from Kite's
Content-Type-mismatch DataException instead of silently treating
kc.positions()/kc.holdings() as empty. Found 2026-07-03: this bug
was firing on every kc.positions()/kc.holdings() call across the
whole app; in panic_close_all specifically, a silent empty fallback
means the panic-close SELL for that ticker is skipped entirely
(qty computed as 0), leaving a real, algo-opened position untouched
during what's meant to be an emergency flatten-everything action.

This test proves the recovered qty reaches the per-ticker SELL
loop by asserting the ticker shows up in ``errors`` with a
"no price available" message (Redis + OHLCV both empty in this
test) rather than being silently skipped -- silent-skip and
no-price-available are the two possible outcomes once qty is
correctly non-zero, and only "no price available" is reachable
when qty > 0 (the "qty <= 0" branch does a bare ``continue``,
never touching ``errors``). This isolates the fix without needing
to mock through to actual order placement, which is out of scope
for this bug.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from kiteconnect.exceptions import DataException

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_USER_ID = UUID("55555555-5555-5555-5555-555555555555")
_USER_CTX = UserContext(
    user_id=str(_USER_ID), email="t@t.com", role="pro",
)


def _content_type_exception(body: str) -> DataException:
    return DataException(
        f"Unknown Content-Type (text/plain; charset=utf-8) with "
        f"response: (b'{body}')",
    )


def _app():
    from backend.algo.routes.kill_switch import create_kill_switch_router

    app = FastAPI()
    app.include_router(create_kill_switch_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: _USER_CTX
    return app


class TestPanicCloseContentTypeRecovery:
    @patch("backend.db.duckdb_engine.query_iceberg_table")
    @patch("backend.algo.broker.kite_client.KiteConnect")
    @patch(
        "backend.algo.broker.credentials_repo.BrokerCredentialsRepo.load",
    )
    @patch(
        "backend.algo.paper.kill_switch_repo.KillSwitchRepo.arm",
        new_callable=AsyncMock,
    )
    def test_recovers_open_qty_despite_content_type_mismatch(
        self, arm_mock, load_creds, MockKC, iceberg_mock,
    ):
        # algo.events lookup: one algo-opened ticker, MMTC.
        iceberg_mock.return_value = [
            {"payload_json": json.dumps({"symbol": "MMTC"})},
        ]
        load_creds.return_value = {
            "api_key": "k", "access_token": "tok",
            "access_token_expired": False,
        }

        kc_instance = MagicMock()
        kc_instance.get_gtts.return_value = []
        kc_instance.holdings.return_value = []
        # positions() raises the Content-Type DataException on every
        # call; the embedded payload has a real, non-zero MMTC qty.
        kc_instance.positions.side_effect = _content_type_exception(
            '{"status":"success","data":{"net":[{"tradingsymbol":'
            '"MMTC","quantity":42}]}}',
        )
        MockKC.return_value = kc_instance

        # No Redis LTP cache and no OHLCV fallback configured on the
        # real cache/duckdb layer in this test process -- both will
        # naturally miss, forcing the "no price available" branch,
        # which only fires when qty > 0.
        with patch(
            "backend.cache.get_cache",
        ) as cache_mock:
            cache_mock.return_value.get.return_value = None
            client = TestClient(_app())
            resp = client.post("/v1/algo/kill-switch/panic-close-all")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert any(
            e.startswith("MMTC:") and "no price available" in e
            for e in body["errors"]
        ), (
            "MMTC must reach the price-lookup step (qty > 0, "
            "recovered from the Content-Type mismatch) -- if the "
            "recovery had failed, positions() would look empty and "
            "MMTC would be silently skipped with no error at all. "
            f"Got errors: {body['errors']}"
        )
