"""Tests for kite_client.py's Content-Type-mismatch recovery helpers.

Found 2026-07-03: Kite's API occasionally serves a valid JSON
positions/holdings body under Content-Type: text/plain instead of
application/json. The kiteconnect SDK's _request() does a strict
Content-Type sniff before parsing and raises DataException on any
mismatch -- discarding a perfectly valid response body that it
embeds verbatim in its own error message. This was firing on every
poll, permanently zeroing "Currently committed" and the
Positions/Holdings tabs even though real positions were open.
Shared across every call site that reads kc.positions()/kc.holdings()
(routes/live.py, KiteClient.get_positions(), and others).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from kiteconnect.exceptions import DataException


class TestRecoverFromKiteContentTypeMismatch:
    def test_recovers_valid_success_payload(self):
        from backend.algo.broker.kite_client import (
            recover_from_kite_content_type_mismatch,
        )

        exc = DataException(
            "Unknown Content-Type (text/plain; charset=utf-8) "
            "with response: (b'{\"status\":\"success\",\"data\":"
            "{\"net\": [], \"day\": []}}')",
        )
        recovered = recover_from_kite_content_type_mismatch(exc)
        assert recovered == {"net": [], "day": []}

    def test_recovers_payload_with_real_positions(self):
        from backend.algo.broker.kite_client import (
            recover_from_kite_content_type_mismatch,
        )

        exc = DataException(
            "Unknown Content-Type (text/plain; charset=utf-8) "
            "with response: (b'{\"status\": \"success\", \"data\": "
            "{\"net\": [{\"tradingsymbol\": \"ITC\", \"quantity\": "
            "8}], \"day\": []}}')",
        )
        recovered = recover_from_kite_content_type_mismatch(exc)
        assert recovered["net"] == [
            {"tradingsymbol": "ITC", "quantity": 8},
        ]

    def test_returns_none_for_unrelated_exception_message(self):
        from backend.algo.broker.kite_client import (
            recover_from_kite_content_type_mismatch,
        )

        exc = DataException("Incorrect `api_key` or `access_token`.")
        assert recover_from_kite_content_type_mismatch(exc) is None

    def test_returns_none_when_embedded_payload_is_not_valid_json(
        self,
    ):
        from backend.algo.broker.kite_client import (
            recover_from_kite_content_type_mismatch,
        )

        exc = DataException(
            "Unknown Content-Type (text/html) with response: "
            "(b'<html>not json</html>')",
        )
        assert recover_from_kite_content_type_mismatch(exc) is None

    def test_returns_none_for_genuine_error_status_payload(self):
        """A real Kite error embedded in the same message shape must
        NOT be swallowed as a recovered success -- fall through so
        the original exception still propagates."""
        from backend.algo.broker.kite_client import (
            recover_from_kite_content_type_mismatch,
        )

        exc = DataException(
            "Unknown Content-Type (text/plain) with response: "
            "(b'{\"status\": \"error\", \"message\": \"Session "
            "expired\", \"error_type\": \"TokenException\"}')",
        )
        assert recover_from_kite_content_type_mismatch(exc) is None


class TestKiteCallTolerant:
    """Sync variant -- used from sync call sites like
    KiteClient.get_positions()."""

    def test_returns_result_on_success(self):
        from backend.algo.broker.kite_client import kite_call_tolerant

        fn = MagicMock(return_value={"net": []})
        result = kite_call_tolerant(fn)
        assert result == {"net": []}

    def test_recovers_from_content_type_mismatch(self):
        from backend.algo.broker.kite_client import kite_call_tolerant

        def fn():
            raise DataException(
                "Unknown Content-Type (text/plain; charset=utf-8) "
                "with response: (b'{\"status\":\"success\",\"data\":"
                "{\"net\": [], \"day\": []}}')",
            )

        result = kite_call_tolerant(fn)
        assert result == {"net": [], "day": []}

    def test_reraises_genuine_data_exception(self):
        from backend.algo.broker.kite_client import kite_call_tolerant

        def fn():
            raise DataException("Incorrect `api_key` or `access_token`.")

        with pytest.raises(DataException):
            kite_call_tolerant(fn)

    def test_reraises_non_data_exceptions_unchanged(self):
        from backend.algo.broker.kite_client import kite_call_tolerant

        def fn():
            raise RuntimeError("kite down")

        with pytest.raises(RuntimeError):
            kite_call_tolerant(fn)


class TestKiteCallTolerantAsync:
    """Async variant -- used from async route handlers."""

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        from backend.algo.broker.kite_client import (
            kite_call_tolerant_async,
        )

        fn = MagicMock(return_value={"net": []})
        result = await kite_call_tolerant_async(fn)
        assert result == {"net": []}

    @pytest.mark.asyncio
    async def test_recovers_from_content_type_mismatch(self):
        from backend.algo.broker.kite_client import (
            kite_call_tolerant_async,
        )

        def fn():
            raise DataException(
                "Unknown Content-Type (text/plain; charset=utf-8) "
                "with response: (b'{\"status\":\"success\",\"data\":"
                "{\"net\": [], \"day\": []}}')",
            )

        result = await kite_call_tolerant_async(fn)
        assert result == {"net": [], "day": []}

    @pytest.mark.asyncio
    async def test_reraises_genuine_data_exception(self):
        from backend.algo.broker.kite_client import (
            kite_call_tolerant_async,
        )

        def fn():
            raise DataException("Incorrect `api_key` or `access_token`.")

        with pytest.raises(DataException):
            await kite_call_tolerant_async(fn)

    @pytest.mark.asyncio
    async def test_reraises_non_data_exceptions_unchanged(self):
        from backend.algo.broker.kite_client import (
            kite_call_tolerant_async,
        )

        def fn():
            raise RuntimeError("kite down")

        with pytest.raises(RuntimeError):
            await kite_call_tolerant_async(fn)


class TestGetPositionsRecovery:
    """KiteClient.get_positions() itself must use the recovery
    wrapper -- it's the canonical sync entry point other modules
    (position_hydration.py, kill_switch.py) are being migrated to
    use instead of raw ._kc.positions()."""

    def test_recovers_despite_content_type_mismatch(self):
        from unittest.mock import patch

        from backend.algo.broker.kite_client import KiteClient

        with patch(
            "backend.algo.broker.kite_client.KiteConnect",
        ) as MockKC:
            kc_instance = MagicMock()
            kc_instance.positions.side_effect = DataException(
                "Unknown Content-Type (text/plain; charset=utf-8) "
                "with response: (b'{\"status\":\"success\",\"data\":"
                "{\"net\": [{\"tradingsymbol\": \"MMTC\", "
                "\"quantity\": 42}], \"day\": []}}')",
            )
            MockKC.return_value = kc_instance
            kite = KiteClient(
                api_key="k", access_token="tok", dry_run=False,
            )
            kite._kc = kc_instance

        result = kite.get_positions()
        assert result == [{"tradingsymbol": "MMTC", "quantity": 42}]
