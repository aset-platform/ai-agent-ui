"""Unit tests for ``_compute_strategy_commitment`` and the
exposure-based override on GET /v1/algo/live/caps/{strategy_id}.

Replaces the legacy turnover counter shape with the
"currently-committed capital via this strategy's open positions
and holdings" view used by the Active Strategy panel.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from kiteconnect.exceptions import DataException

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
_STRATEGY_ID = UUID("33333333-3333-3333-3333-333333333333")
_OTHER_STRATEGY = UUID("44444444-4444-4444-4444-444444444444")
_USER_CTX = UserContext(
    user_id=str(_USER_ID), email="t@t.com", role="pro",
)


def _kite_with(positions=None, holdings=None):
    kc = MagicMock()
    kc.positions.return_value = {"net": positions or []}
    kc.holdings.return_value = holdings or []
    kite = MagicMock()
    kite._kc = kc
    return kite


# ---------------------------------------------------------------
# _compute_strategy_commitment (helper)
# ---------------------------------------------------------------


@pytest.mark.asyncio
class TestComputeStrategyCommitment:
    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    async def test_no_positions_returns_zero(
        self, build_kite, attr,
    ):
        from backend.algo.routes.live import (
            _compute_strategy_commitment,
        )

        build_kite.return_value = _kite_with()
        attr.return_value = {}

        committed, count = await _compute_strategy_commitment(
            _USER_ID, _STRATEGY_ID,
        )

        assert committed == Decimal("0")
        assert count == 0

    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    async def test_open_position_attributed_to_strategy(
        self, build_kite, attr,
    ):
        from backend.algo.routes.live import (
            _compute_strategy_commitment,
        )

        build_kite.return_value = _kite_with(
            positions=[{
                "tradingsymbol": "ITC", "exchange": "NSE",
                "quantity": 8, "average_price": 307.33,
                "product": "MIS",
            }],
        )
        attr.return_value = {
            "ITC": {"strategy_id": str(_STRATEGY_ID)},
        }

        committed, count = await _compute_strategy_commitment(
            _USER_ID, _STRATEGY_ID,
        )

        # 8 × 307.33 = 2458.64
        assert committed == Decimal("8") * Decimal("307.33")
        assert count == 1

    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    async def test_position_from_other_strategy_excluded(
        self, build_kite, attr,
    ):
        from backend.algo.routes.live import (
            _compute_strategy_commitment,
        )

        build_kite.return_value = _kite_with(
            positions=[{
                "tradingsymbol": "ITC", "exchange": "NSE",
                "quantity": 8, "average_price": 307.33,
                "product": "MIS",
            }],
        )
        attr.return_value = {
            "ITC": {"strategy_id": str(_OTHER_STRATEGY)},
        }

        committed, count = await _compute_strategy_commitment(
            _USER_ID, _STRATEGY_ID,
        )

        assert committed == Decimal("0")
        assert count == 0

    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    async def test_position_plus_holding_summed(
        self, build_kite, attr,
    ):
        from backend.algo.routes.live import (
            _compute_strategy_commitment,
        )

        build_kite.return_value = _kite_with(
            positions=[{
                "tradingsymbol": "ITC", "exchange": "NSE",
                "quantity": 8, "average_price": 300,
                "product": "MIS",
            }],
            holdings=[{
                "tradingsymbol": "RELIANCE", "exchange": "NSE",
                "quantity": 5, "t1_quantity": 0,
                "average_price": 2500,
                "product": "CNC",
            }],
        )
        attr.return_value = {
            "ITC": {"strategy_id": str(_STRATEGY_ID)},
            "RELIANCE": {"strategy_id": str(_STRATEGY_ID)},
        }

        committed, count = await _compute_strategy_commitment(
            _USER_ID, _STRATEGY_ID,
        )

        # 8×300 + 5×2500 = 2400 + 12500 = 14900
        assert committed == Decimal("14900")
        assert count == 2

    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    async def test_t1_quantity_counts_toward_commitment(
        self, build_kite, attr,
    ):
        """T+1 settling shares are legally owned today (CNC); the
        attribution helper must include them in the exposure sum
        per the same logic the /holdings endpoint applies.
        """
        from backend.algo.routes.live import (
            _compute_strategy_commitment,
        )

        build_kite.return_value = _kite_with(
            holdings=[{
                "tradingsymbol": "TCS", "exchange": "NSE",
                "quantity": 0, "t1_quantity": 3,
                "average_price": 4000, "product": "CNC",
            }],
        )
        attr.return_value = {
            "TCS": {"strategy_id": str(_STRATEGY_ID)},
        }

        committed, count = await _compute_strategy_commitment(
            _USER_ID, _STRATEGY_ID,
        )

        assert committed == Decimal("12000")  # 3 × 4000
        assert count == 1

    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    async def test_kite_read_failure_returns_zero(
        self, build_kite, attr,
    ):
        from backend.algo.routes.live import (
            _compute_strategy_commitment,
        )

        kc = MagicMock()
        kc.positions.side_effect = RuntimeError("kite down")
        kc.holdings.side_effect = RuntimeError("kite down")
        kite = MagicMock()
        kite._kc = kc
        build_kite.return_value = kite
        attr.return_value = {}

        committed, count = await _compute_strategy_commitment(
            _USER_ID, _STRATEGY_ID,
        )

        assert committed == Decimal("0")
        assert count == 0


# ---------------------------------------------------------------
# _recover_from_content_type_mismatch (pure helper)
#
# Found 2026-07-03: Kite's API occasionally serves a valid JSON
# positions/holdings body under Content-Type: text/plain instead of
# application/json. The kiteconnect SDK's _request() does a strict
# Content-Type sniff before parsing and raises DataException on any
# mismatch -- discarding a perfectly valid response body that it
# embeds verbatim in its own error message. This was firing on
# every poll, permanently zeroing "Currently committed" and the
# Positions tab even though real positions were open.
# ---------------------------------------------------------------


class TestRecoverFromContentTypeMismatch:
    def test_recovers_valid_success_payload(self):
        from backend.algo.routes.live import (
            _recover_from_content_type_mismatch,
        )

        exc = DataException(
            "Unknown Content-Type (text/plain; charset=utf-8) "
            "with response: (b'{\"status\":\"success\",\"data\":"
            "{\"net\": [], \"day\": []}}')",
        )
        recovered = _recover_from_content_type_mismatch(exc)
        assert recovered == {"net": [], "day": []}

    def test_recovers_payload_with_real_positions(self):
        from backend.algo.routes.live import (
            _recover_from_content_type_mismatch,
        )

        exc = DataException(
            "Unknown Content-Type (text/plain; charset=utf-8) "
            "with response: (b'{\"status\": \"success\", \"data\": "
            "{\"net\": [{\"tradingsymbol\": \"ITC\", \"quantity\": "
            "8}], \"day\": []}}')",
        )
        recovered = _recover_from_content_type_mismatch(exc)
        assert recovered["net"] == [
            {"tradingsymbol": "ITC", "quantity": 8},
        ]

    def test_returns_none_for_unrelated_exception_message(self):
        from backend.algo.routes.live import (
            _recover_from_content_type_mismatch,
        )

        exc = DataException("Incorrect `api_key` or `access_token`.")
        assert _recover_from_content_type_mismatch(exc) is None

    def test_returns_none_when_embedded_payload_is_not_valid_json(
        self,
    ):
        from backend.algo.routes.live import (
            _recover_from_content_type_mismatch,
        )

        exc = DataException(
            "Unknown Content-Type (text/html) with response: "
            "(b'<html>not json</html>')",
        )
        assert _recover_from_content_type_mismatch(exc) is None

    def test_returns_none_for_genuine_error_status_payload(self):
        """A real Kite error embedded in the same message shape must
        NOT be swallowed as a recovered success -- fall through so
        the original exception still propagates."""
        from backend.algo.routes.live import (
            _recover_from_content_type_mismatch,
        )

        exc = DataException(
            "Unknown Content-Type (text/plain) with response: "
            "(b'{\"status\": \"error\", \"message\": \"Session "
            "expired\", \"error_type\": \"TokenException\"}')",
        )
        assert _recover_from_content_type_mismatch(exc) is None


class TestKitePositionsOrHoldings:
    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        from backend.algo.routes.live import (
            _kite_positions_or_holdings,
        )

        fn = MagicMock(return_value={"net": []})
        result = await _kite_positions_or_holdings(fn)
        assert result == {"net": []}

    @pytest.mark.asyncio
    async def test_recovers_from_content_type_mismatch(self):
        from backend.algo.routes.live import (
            _kite_positions_or_holdings,
        )

        def fn():
            raise DataException(
                "Unknown Content-Type (text/plain; charset=utf-8) "
                "with response: (b'{\"status\":\"success\",\"data\":"
                "{\"net\": [], \"day\": []}}')",
            )

        result = await _kite_positions_or_holdings(fn)
        assert result == {"net": [], "day": []}

    @pytest.mark.asyncio
    async def test_reraises_genuine_data_exception(self):
        from backend.algo.routes.live import (
            _kite_positions_or_holdings,
        )

        def fn():
            raise DataException("Incorrect `api_key` or `access_token`.")

        with pytest.raises(DataException):
            await _kite_positions_or_holdings(fn)

    @pytest.mark.asyncio
    async def test_reraises_non_data_exceptions_unchanged(self):
        from backend.algo.routes.live import (
            _kite_positions_or_holdings,
        )

        def fn():
            raise RuntimeError("kite down")

        with pytest.raises(RuntimeError):
            await _kite_positions_or_holdings(fn)


class TestComputeStrategyCommitmentContentTypeRecovery:
    @pytest.mark.asyncio
    @patch("backend.algo.routes.live._fetch_holding_attribution")
    @patch("backend.algo.routes.live._build_kite_client_for_user")
    async def test_recovers_open_position_despite_content_type_bug(
        self, build_kite, attr,
    ):
        """The exact bug found 2026-07-03: kc.positions() raises the
        Content-Type DataException on every call even though real
        positions are open (via holdings()). Commitment must reflect
        the real holdings value, not silently fall back to 0."""
        from backend.algo.routes.live import (
            _compute_strategy_commitment,
        )

        kc = MagicMock()
        kc.positions.side_effect = DataException(
            "Unknown Content-Type (text/plain; charset=utf-8) "
            "with response: (b'{\"status\":\"success\",\"data\":"
            "{\"net\": [], \"day\": []}}')",
        )
        kc.holdings.return_value = [{
            "tradingsymbol": "MMTC", "exchange": "NSE",
            "quantity": 42, "t1_quantity": 0,
            "average_price": 68.84, "product": "CNC",
        }]
        kite = MagicMock()
        kite._kc = kc
        build_kite.return_value = kite
        attr.return_value = {
            "MMTC": {"strategy_id": str(_STRATEGY_ID)},
        }

        committed, count = await _compute_strategy_commitment(
            _USER_ID, _STRATEGY_ID,
        )

        assert committed == Decimal("42") * Decimal("68.84")
        assert count == 1


# ---------------------------------------------------------------
# GET /caps/{strategy_id} override
# ---------------------------------------------------------------


def _app():
    from fastapi import FastAPI
    from backend.algo.routes.live import create_live_router

    app = FastAPI()
    app.include_router(create_live_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: _USER_CTX
    return app


class TestGetCapsExposureOverride:
    @patch("backend.algo.live.caps_repo.CapsRepo.get_or_default")
    @patch("backend.algo.routes.live._compute_strategy_commitment")
    def test_response_uses_computed_exposure(
        self, commit_mock, get_or_default,
    ):
        """Stored counters on algo.live_caps are ignored — response
        must surface the exposure-based commitment instead.
        """
        from fastapi.testclient import TestClient

        get_or_default.return_value = {
            "user_id": _USER_ID,
            "strategy_id": _STRATEGY_ID,
            "max_inr": Decimal("3000"),
            "max_orders_per_day": 10,
            "allowed_tickers": ["ITC"],
            "live_orders_enabled": True,
            "approved_by": None,
            "approved_at": None,
            "last_walkforward_run_id": None,
            # Stale turnover counters that must NOT leak through.
            "cumulative_inr_today": Decimal("2707.60"),
            "orders_count_today": 2,
        }
        commit_mock.return_value = (Decimal("0"), 0)

        client = TestClient(_app())
        resp = client.get(f"/v1/algo/live/caps/{_STRATEGY_ID}")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Decimal(str(body["cumulative_inr_today"])) == Decimal("0")
        assert body["orders_count_today"] == 0

    @patch("backend.algo.live.caps_repo.CapsRepo.get_or_default")
    @patch("backend.algo.routes.live._compute_strategy_commitment")
    def test_response_reflects_open_exposure(
        self, commit_mock, get_or_default,
    ):
        from fastapi.testclient import TestClient

        get_or_default.return_value = {
            "user_id": _USER_ID,
            "strategy_id": _STRATEGY_ID,
            "max_inr": Decimal("3000"),
            "max_orders_per_day": 10,
            "allowed_tickers": ["ITC"],
            "live_orders_enabled": True,
            "approved_by": None,
            "approved_at": None,
            "last_walkforward_run_id": None,
            "cumulative_inr_today": Decimal("0"),
            "orders_count_today": 0,
        }
        commit_mock.return_value = (Decimal("2458.64"), 1)

        client = TestClient(_app())
        resp = client.get(f"/v1/algo/live/caps/{_STRATEGY_ID}")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Decimal(str(body["cumulative_inr_today"])) == Decimal(
            "2458.64",
        )
        assert body["orders_count_today"] == 1
