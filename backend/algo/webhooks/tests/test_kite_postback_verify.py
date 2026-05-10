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
