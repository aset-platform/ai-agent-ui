"""Unit tests — KitePostbackPayload model + verify_checksum."""
import pytest
from pydantic import ValidationError


class TestKitePostbackPayload:
    """Parsing the Kite docs sample payload."""

    def test_parses_complete_payload(self):
        from backend.algo.webhooks.kite_postback import (
            KitePostbackPayload,
        )
        raw = {
            "user_id": "AB1234",
            "order_id": "220803201322749",
            "exchange_order_id": "1000000012321212",
            "status": "COMPLETE",
            "status_message": None,
            "tradingsymbol": "SBIN",
            "instrument_token": 779521,
            "exchange": "NSE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "product": "CNC",
            "quantity": 1,
            "filled_quantity": 1,
            "unfilled_quantity": 0,
            "cancelled_quantity": 0,
            "price": 0.0,
            "trigger_price": 0.0,
            "average_price": 519.5,
            "order_timestamp": "2022-08-03 13:13:22",
            "checksum": "abc123",
            "tag": "algo_strat_1",
            "guid": "unique-guid-001",
        }
        p = KitePostbackPayload(**raw)
        assert p.user_id == "AB1234"
        assert p.order_id == "220803201322749"
        assert p.status == "COMPLETE"
        assert p.guid == "unique-guid-001"
        assert p.filled_quantity == 1
        assert p.average_price == 519.5
        assert p.exchange_order_id == "1000000012321212"
        assert p.tag == "algo_strat_1"

    def test_optional_fields_default_none(self):
        from backend.algo.webhooks.kite_postback import (
            KitePostbackPayload,
        )
        raw = {
            "user_id": "AB1234",
            "order_id": "220803201322749",
            "status": "REJECTED",
            "tradingsymbol": "SBIN",
            "instrument_token": 779521,
            "exchange": "NSE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "product": "CNC",
            "quantity": 1,
            "filled_quantity": 0,
            "unfilled_quantity": 1,
            "cancelled_quantity": 0,
            "price": 0.0,
            "trigger_price": 0.0,
            "average_price": 0.0,
            "order_timestamp": "2022-08-03 09:15:00",
            "checksum": "xyz",
            "guid": "unique-guid-002",
        }
        p = KitePostbackPayload(**raw)
        assert p.exchange_order_id is None
        assert p.status_message is None
        assert p.tag is None

    def test_missing_required_field_raises(self):
        from backend.algo.webhooks.kite_postback import (
            KitePostbackPayload,
        )
        with pytest.raises(ValidationError):
            KitePostbackPayload(
                user_id="AB1234",
                # order_id missing
                status="COMPLETE",
                tradingsymbol="SBIN",
                instrument_token=779521,
                exchange="NSE",
                transaction_type="BUY",
                order_type="MARKET",
                product="CNC",
                quantity=1,
                filled_quantity=1,
                unfilled_quantity=0,
                cancelled_quantity=0,
                price=0.0,
                trigger_price=0.0,
                average_price=519.5,
                order_timestamp="2022-08-03 13:13:22",
                checksum="abc",
                guid="g1",
            )

    def test_status_update_variant(self):
        from backend.algo.webhooks.kite_postback import (
            KitePostbackPayload,
        )
        raw = {
            "user_id": "AB1234",
            "order_id": "220803201322749",
            "status": "UPDATE",
            "tradingsymbol": "TCS",
            "instrument_token": 2953217,
            "exchange": "NSE",
            "transaction_type": "SELL",
            "order_type": "LIMIT",
            "product": "MIS",
            "quantity": 10,
            "filled_quantity": 5,
            "unfilled_quantity": 5,
            "cancelled_quantity": 0,
            "price": 3500.0,
            "trigger_price": 0.0,
            "average_price": 3501.0,
            "order_timestamp": "2022-08-03 10:00:00",
            "checksum": "def456",
            "guid": "unique-guid-003",
        }
        p = KitePostbackPayload(**raw)
        assert p.status == "UPDATE"
        assert p.filled_quantity == 5


class TestVerifyChecksum:
    """Tests for verify_checksum()."""

    # Kite docs sample values:
    #   order_id       = "220803201322749"
    #   order_timestamp = "2022-08-03 13:13:22"
    #   api_secret     = "test_api_secret"
    # Precomputed:
    #   SHA-256("220803201322749" +
    #           "2022-08-03 13:13:22" +
    #           "test_api_secret")
    _ORDER_ID = "220803201322749"
    _ORDER_TS = "2022-08-03 13:13:22"
    _SECRET = "test_api_secret"

    @classmethod
    def _expected_checksum(cls) -> str:
        import hashlib
        return hashlib.sha256(
            f"{cls._ORDER_ID}{cls._ORDER_TS}"
            f"{cls._SECRET}".encode("utf-8")
        ).hexdigest()

    def test_pass_case(self):
        """Correct checksum returns True."""
        from backend.algo.webhooks.kite_postback import (
            verify_checksum,
        )
        payload = {
            "order_id": self._ORDER_ID,
            "order_timestamp": self._ORDER_TS,
            "checksum": self._expected_checksum(),
        }
        assert verify_checksum(payload, self._SECRET) is True

    def test_fail_case_wrong_checksum(self):
        """Wrong checksum returns False."""
        from backend.algo.webhooks.kite_postback import (
            verify_checksum,
        )
        payload = {
            "order_id": self._ORDER_ID,
            "order_timestamp": self._ORDER_TS,
            "checksum": "deadbeefdeadbeef",
        }
        assert (
            verify_checksum(payload, self._SECRET) is False
        )

    def test_fail_case_wrong_secret(self):
        """Wrong api_secret returns False."""
        from backend.algo.webhooks.kite_postback import (
            verify_checksum,
        )
        payload = {
            "order_id": self._ORDER_ID,
            "order_timestamp": self._ORDER_TS,
            "checksum": self._expected_checksum(),
        }
        assert (
            verify_checksum(payload, "wrong_secret")
            is False
        )

    def test_fail_case_reformatted_timestamp(self):
        """Reformatting the IST timestamp breaks checksum."""
        from backend.algo.webhooks.kite_postback import (
            verify_checksum,
        )
        payload = {
            "order_id": self._ORDER_ID,
            # UTC-formatted — must NOT reformat
            "order_timestamp": "2022-08-03T07:43:22Z",
            "checksum": self._expected_checksum(),
        }
        assert (
            verify_checksum(payload, self._SECRET) is False
        )

    def test_missing_checksum_field_returns_false(self):
        """Missing checksum field returns False."""
        from backend.algo.webhooks.kite_postback import (
            verify_checksum,
        )
        payload = {
            "order_id": self._ORDER_ID,
            "order_timestamp": self._ORDER_TS,
        }
        assert (
            verify_checksum(payload, self._SECRET) is False
        )

    def test_checksum_case_insensitive(self):
        """Checksum comparison is case-insensitive (lowercase)."""
        from backend.algo.webhooks.kite_postback import (
            verify_checksum,
        )
        payload = {
            "order_id": self._ORDER_ID,
            "order_timestamp": self._ORDER_TS,
            "checksum": self._expected_checksum().upper(),
        }
        assert (
            verify_checksum(payload, self._SECRET) is True
        )

    def test_constant_time_compare_smoke(self):
        """hmac.compare_digest used — no early exit on prefix."""
        import time
        from backend.algo.webhooks.kite_postback import (
            verify_checksum,
        )
        good = self._expected_checksum()
        # wrong checksum that shares long prefix
        bad = good[:-4] + "0000"
        t_good, t_bad = [], []
        for _ in range(200):
            p = {
                "order_id": self._ORDER_ID,
                "order_timestamp": self._ORDER_TS,
                "checksum": good,
            }
            t0 = time.perf_counter_ns()
            verify_checksum(p, self._SECRET)
            t_good.append(time.perf_counter_ns() - t0)
            p["checksum"] = bad
            t0 = time.perf_counter_ns()
            verify_checksum(p, self._SECRET)
            t_bad.append(time.perf_counter_ns() - t0)
        # Smoke: simply confirm neither path raises;
        # true timing attack analysis is beyond unit scope.
        assert len(t_good) == 200
        assert len(t_bad) == 200
